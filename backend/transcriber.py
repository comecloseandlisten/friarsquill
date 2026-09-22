import gc
import re
import sys
import tempfile
from pathlib import Path

from config import Config
from audio import get_duration, split_audio_chunks


def _log(msg):
    """Debug log to stderr (visible in Electron console, not in JSON-RPC)."""
    print(f"[transcriber] {msg}", file=sys.stderr, flush=True)


_WHISPER_HALLUCINATION_RE = re.compile(
    r"DimaTorzok"
    r"|субтитры\s+создавал"
    r"|subtitles\s+by"
    r"|sous[- ]titres\s+par"
    r"|untertitel\s+von",
    re.IGNORECASE,
)


def _is_whisper_hallucination(text: str) -> bool:
    """Check if a segment matches known Whisper hallucination patterns."""
    return bool(_WHISPER_HALLUCINATION_RE.search(text))


def _filter_hallucinations(segments: list[dict]) -> list[dict]:
    """Remove segments that match known Whisper hallucination patterns."""
    filtered = [s for s in segments if not _is_whisper_hallucination(s.get("text", ""))]
    removed = len(segments) - len(filtered)
    if removed:
        _log(f"Filtered {removed} hallucinated segment(s)")
    return filtered


def _resolve_device_and_compute(config: Config) -> tuple[str, str]:
    """
    Resolve device (cuda/cpu) and compute_type for faster-whisper.
    Uses ctranslate2.get_cuda_device_count() for CUDA detection (no torch needed).
    Falls back to CPU if CUDA is unavailable or cuDNN is missing.
    """
    device = config.device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception as e:
            _log(f"CUDA detection via ctranslate2 failed: {e}, falling back to CPU")
            device = "cpu"

    compute_type = config.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    # Auto-correct: float16 not supported on CPU
    if device == "cpu" and compute_type == "float16":
        compute_type = "int8"

    return device, compute_type


_LEAKED_CT2_REFS: list = []


class ASRRuntime:
    """Reusable faster-whisper runtime to avoid repeated model reloads."""

    def __init__(self, config: Config):
        from faster_whisper import WhisperModel

        self.device, self.compute_type = _resolve_device_and_compute(config)
        _log(
            f"Initializing ASR runtime model={config.whisper_model} "
            f"device={self.device} compute={self.compute_type}"
        )
        self.model = WhisperModel(
            config.whisper_model,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=max(1, int(config.asr_cpu_threads)),
        )
        _log("WhisperModel constructor finished; ASR runtime ready")

    def close(self):
        # ctranslate2's C++ destructor crashes on Windows with
        # STATUS_STACK_BUFFER_OVERRUN (0xC0000409) when freeing CUDA resources.
        # Strategy: unload weights (frees VRAM), then park the object in a
        # global list so its C++ destructor never fires during this process.
        if self.model is not None:
            try:
                self.model.model.unload_model()
                _log("ASR model weights unloaded from VRAM")
            except Exception as e:
                _log(f"unload_model() failed ({e}), leaking model reference")
            _LEAKED_CT2_REFS.append(self.model)
            self.model = None
            _log("ASR model reference parked (destructor suppressed)")


def _transcribe_options(config: Config, duration_hint_sec: float | None = None) -> dict:
    """Build effective transcribe options, with optional long-video fast profile."""
    beam_size = config.beam_size
    vad_filter = config.vad_filter
    word_timestamps = config.word_timestamps

    if (
        config.fast_profile_for_long_video
        and duration_hint_sec
        and duration_hint_sec >= config.fast_profile_min_duration_sec
    ):
        beam_size = config.fast_profile_beam_size
        vad_filter = config.fast_profile_vad_filter
        word_timestamps = config.fast_profile_word_timestamps
        _log(
            "Applying long-video fast profile: "
            f"beam_size={beam_size} vad_filter={vad_filter} word_timestamps={word_timestamps}"
        )

    return {
        "beam_size": beam_size,
        "vad_filter": vad_filter,
        "vad_parameters": dict(min_silence_duration_ms=500),
        "word_timestamps": word_timestamps,
        "hallucination_silence_threshold": 2.0,
        "initial_prompt": "Дословная транскрипция.",
    }


def _get_runtime(config: Config, runtime: ASRRuntime | None) -> tuple[ASRRuntime, bool]:
    if runtime is not None:
        return runtime, False
    return ASRRuntime(config), True


def _detect_languages(
    wav_path: Path,
    config: Config,
    runtime: ASRRuntime | None = None,
) -> list[tuple[float, float, str, float]]:
    """
    Detect languages in audio file using sliding windows.

    Returns a list of (start_time, end_time, language_code, probability) tuples.
    Uses 30-second windows with 15-second step.
    """
    runtime_obj, owned = _get_runtime(config, runtime)
    model = runtime_obj.model

    try:
        import numpy as np
        import tempfile
        from audio import extract_audio_slice, get_duration as _get_audio_duration

        _log(f"Detecting languages via per-window slices: {wav_path}")
        try:
            duration_s = _get_audio_duration(wav_path)
        except Exception:
            duration_s = 0.0
        if duration_s <= 0:
            _log("Cannot determine audio duration, skipping language detection")
            return []

        _log(f"Audio duration: {duration_s:.1f}s")

        window_sec = 30.0
        step_sec = 15.0
        language_map = []
        start_s = 0.0

        while start_s < duration_s:
            end_s = min(start_s + window_sec, duration_s)
            slice_dur = end_s - start_s
            if slice_dur < 1.0:
                break

            tmp_slice = None
            try:
                tmp_slice = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_slice.close()
                extract_audio_slice(wav_path, Path(tmp_slice.name), start_s, slice_dur)

                samples = np.memmap(tmp_slice.name, dtype=np.int16, mode='r', offset=44)
                float_samples = samples.astype(np.float32) / 32768.0

                result = model.detect_language(float_samples)
                language_code = result[0]
                probability = result[1]

                del float_samples, samples

                _log(f"  [{start_s:.1f}s-{end_s:.1f}s] {language_code} ({probability:.0%})")
                language_map.append((start_s, end_s, language_code, probability))
            except Exception as e:
                _log(f"Error processing language window [{start_s:.1f}s-{end_s:.1f}s]: {e}")
                raise
            finally:
                if tmp_slice is not None:
                    try:
                        Path(tmp_slice.name).unlink(missing_ok=True)
                    except OSError:
                        pass

            start_s += step_sec

        _log(f"Language detection complete: {len(language_map)} windows analyzed")
        return language_map

    finally:
        if owned:
            runtime_obj.close()


def _is_multilingual(language_map: list[tuple[float, float, str, float]], threshold: float) -> bool:
    """
    Check if language map indicates multilingual content.
    Returns True if >1 language detected above threshold.
    """
    languages_above_threshold = set()
    for _, _, lang, prob in language_map:
        if prob >= threshold:
            languages_above_threshold.add(lang)

    return len(languages_above_threshold) > 1


def _transcribe_with_language_fallback(
    wav_path: Path,
    config: Config,
    language_map: list[tuple[float, float, str, float]] = None,
    progress_cb=None,
    runtime: ASRRuntime | None = None,
    duration_hint_sec: float | None = None,
) -> tuple[list[dict], str, float]:
    """
    Transcribe audio, trying native multilingual mode first, falling back if needed.

    Returns (segments, detected_language, language_probability).
    Adds 'language' field to each segment.
    """
    runtime_obj, owned = _get_runtime(config, runtime)
    model = runtime_obj.model
    opts = _transcribe_options(config, duration_hint_sec)

    try:
        try:
            # Try native mode first: language=None lets faster-whisper auto-detect per segment
            _log("Attempting native multilingual transcription (language=None)")

            segments_raw, info = model.transcribe(
                str(wav_path),
                language=None,  # Auto-detect
                **opts,
            )

            segments = []
            for i, seg in enumerate(segments_raw):
                seg_data = {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text.strip(),
                    "language": info.language,
                }
                if hasattr(seg, "language") and seg.language:
                    seg_data["language"] = seg.language
                segments.append(seg_data)

                if progress_cb and i % 10 == 0:
                    progress_cb(
                        min(10 + int(80 * (seg.end / max(info.duration, 1))), 90),
                        f"Transcribing... {seg.end:.0f}s / {info.duration:.0f}s",
                    )

            _log(
                f"Detected language: {info.language} "
                f"({info.language_probability:.0%}), segments: {len(segments)}"
            )

            if info.language_probability >= 0.7 or not language_map:
                _log(
                    "Native mode selected: "
                    f"language probability {info.language_probability:.0%}"
                )
                return segments, info.language, info.language_probability

            _log(
                "Native mode low confidence, switching to "
                "segment-based fallback"
            )
        except Exception as e:
            _log(f"Native mode failed: {e}")

        return _transcribe_segment_based(
            wav_path,
            config,
            language_map,
            progress_cb=progress_cb,
            runtime=runtime_obj,
            duration_hint_sec=duration_hint_sec,
        )
    finally:
        if owned:
            runtime_obj.close()


def transcribe_single_language(
    wav_path: Path,
    config: Config,
    lang: str,
    progress_cb=None,
    runtime: ASRRuntime | None = None,
    duration_hint_sec: float | None = None,
) -> tuple[list[dict], str, float]:
    """
    Fast-path transcription for a single explicit language.

    Skips all language detection and directly transcribes with the given language code.

    Args:
        wav_path: Path to WAV audio file
        config: Config object
        lang: Explicit language code (e.g. "en", "ru", "es")
        progress_cb: Optional progress callback (percent, message)

    Returns:
        (segments, language_code, language_probability) where probability is always 1.0
    """
    runtime_obj, owned = _get_runtime(config, runtime)
    opts = _transcribe_options(config, duration_hint_sec)
    model = runtime_obj.model
    _log(
        f"Fast-path transcription: language={lang} "
        f"device={runtime_obj.device} compute={runtime_obj.compute_type}"
    )

    try:
        _log(f"Model loaded. Transcribing {wav_path}")

        segments_raw, info = model.transcribe(
            str(wav_path),
            language=lang,  # Explicit language, no detection
            **opts,
        )

        _log(f"Fast-path transcription complete: {lang}, duration: {info.duration:.0f}s")

        if progress_cb:
            progress_cb(10, f"Using language: {lang}")

        segments = []
        for i, seg in enumerate(segments_raw):
            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip(),
                "language": lang,
            })
            if progress_cb and i % 10 == 0:
                progress_cb(
                    min(10 + int(80 * (seg.end / max(info.duration, 1))), 90),
                    f"Transcribing... {seg.end:.0f}s / {info.duration:.0f}s",
                )

        _log(f"Transcription complete: {len(segments)} segments")

        return segments, lang, 1.0  # Fast-path has 100% confidence (user-specified)

    finally:
        if owned:
            runtime_obj.close()


def _transcribe_segment_based(
    wav_path: Path,
    config: Config,
    language_map: list[tuple[float, float, str, float]],
    progress_cb=None,
    runtime: ASRRuntime | None = None,
    duration_hint_sec: float | None = None,
) -> tuple[list[dict], str, float]:
    """
    Transcribe audio by splitting into language zones and transcribing each with explicit language.

    Uses language_map to determine which language to use for each zone.
    Returns (segments, primary_language, weighted_avg_probability).
    """
    from pydub import AudioSegment

    _log(f"Starting segment-based transcription with {len(language_map)} zones")

    runtime_obj, owned = _get_runtime(config, runtime)
    opts = _transcribe_options(config, duration_hint_sec)
    model = runtime_obj.model
    _log(f"Segment-based mode: device={runtime_obj.device} compute={runtime_obj.compute_type}")

    try:
        audio = AudioSegment.from_file(str(wav_path))

        # Merge overlapping zones and determine language for each merged zone
        merged_zones = []
        if language_map:
            # Sort by start time
            sorted_map = sorted(language_map, key=lambda x: x[0])

            current_zone = None
            for start_s, end_s, lang, prob in sorted_map:
                if current_zone is None:
                    current_zone = [start_s, end_s, lang, prob]
                elif lang == current_zone[2]:
                    # Same language, extend zone
                    current_zone[1] = max(current_zone[1], end_s)
                else:
                    # Language change, save current and start new
                    merged_zones.append(current_zone)
                    current_zone = [start_s, end_s, lang, prob]

            if current_zone:
                merged_zones.append(current_zone)

        _log(f"Merged into {len(merged_zones)} language zones")

        # Transcribe each zone
        all_segments = []
        total_duration = len(audio) / 1000.0
        language_weights = {}  # Track time per language for weighted average

        for zone_idx, (start_s, end_s, lang, prob) in enumerate(merged_zones):
            _log(f"  Zone {zone_idx + 1}/{len(merged_zones)}: [{start_s:.1f}s-{end_s:.1f}s] lang={lang} prob={prob:.0%}")

            # Extract audio segment
            start_ms = int(start_s * 1000)
            end_ms = int(end_s * 1000)
            segment_audio = audio[start_ms:end_ms]

            # Save segment to temp file (delete=False to avoid Windows lock)
            tmp_fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp_path = Path(tmp_fd.name)
            tmp_fd.close()
            try:
                segment_audio.export(str(tmp_path), format="wav")

                # Transcribe with explicit language
                seg_segments_raw, seg_info = model.transcribe(
                    str(tmp_path),
                    language=lang,  # Explicit language
                    **opts,
                )

                # Collect segments, adjusting timestamps
                for seg in seg_segments_raw:
                    all_segments.append({
                        "start": seg.start + start_s,
                        "end": seg.end + start_s,
                        "text": seg.text.strip(),
                        "language": lang,
                    })

                # Track language time
                zone_duration = end_s - start_s
                language_weights[lang] = language_weights.get(lang, 0) + zone_duration

                _log(f"    Transcribed zone {zone_idx + 1}")

                if progress_cb:
                    progress_cb(
                        min(10 + int(80 * (end_s / max(total_duration, 1))), 90),
                        f"Transcribing zone {zone_idx + 1}/{len(merged_zones)}... {end_s:.0f}s / {total_duration:.0f}s",
                    )
            except Exception as e:
                _log(f"Error processing zone: {e}")
                raise
            finally:
                tmp_path.unlink(missing_ok=True)

        # Determine primary language (most spoken)
        if language_weights:
            primary_lang = max(language_weights, key=language_weights.get)
            total_time = sum(language_weights.values())
            weighted_prob = language_weights[primary_lang] / max(total_time, 1)
        else:
            primary_lang = "unknown"
            weighted_prob = 0.0

        _log(f"Segment-based transcription complete: {len(all_segments)} total segments, primary={primary_lang} ({weighted_prob:.0%})")

        return all_segments, primary_lang, weighted_prob

    finally:
        if owned:
            runtime_obj.close()


def _merge_diarization_with_segments(segments: list[dict], diarization: list[dict]) -> list[dict]:
    """
    Merge diarization results with transcript segments based on time overlap.

    For each segment, find which speaker is active based on time overlap.
    """
    if not diarization:
        for segment in segments:
            segment["speaker"] = "SPEAKER_00"
        return segments

    diarization_sorted = sorted(diarization, key=lambda d: float(d["start"]))
    turn_idx = 0
    turn_count = len(diarization_sorted)

    for segment in segments:
        seg_start = segment["start"]
        seg_end = segment["end"]
        seg_mid = (seg_start + seg_end) / 2.0

        # Advance pointer while current turn ends before this segment midpoint.
        while turn_idx < turn_count and diarization_sorted[turn_idx]["end"] <= seg_mid:
            turn_idx += 1

        speaker_found = None
        if turn_idx < turn_count:
            current_turn = diarization_sorted[turn_idx]
            if current_turn["start"] <= seg_mid < current_turn["end"]:
                speaker_found = current_turn["speaker"]

        if speaker_found:
            segment["speaker"] = speaker_found
        else:
            # Fallback: assign to first speaker
            segment["speaker"] = "SPEAKER_00"

    return segments


def _transcribe_chunked(
    wav_path: Path,
    config: Config,
    progress_cb=None,
    runtime: ASRRuntime | None = None,
    diarization: list[dict] | None = None,
    audio_duration_sec: float | None = None,
) -> list[dict]:
    duration = audio_duration_sec if audio_duration_sec is not None else get_duration(wav_path)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        chunks = split_audio_chunks(
            wav_path,
            Path(tmpdir),
            chunk_sec=config.long_video_chunk_sec,
            overlap_sec=config.long_video_overlap_sec,
            chunk_timeout_sec=config.ffmpeg_chunk_extract_timeout_sec,
        )
        if not chunks:
            return []

        cfg_no_chunk = Config(**config.model_dump())
        cfg_no_chunk.long_video_threshold_sec = 10**9

        merged: list[dict] = []
        last_end = -1.0
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            start = float(chunk["start"])
            end = float(chunk["end"])

            chunk_segments = transcribe(
                chunk["path"],
                cfg_no_chunk,
                progress_cb=None,
                diarization=None,
                runtime=runtime,
                audio_duration_sec=max(end - start, 0.1),
                allow_chunking=False,
            )

            for seg in chunk_segments:
                shifted = {
                    "start": seg["start"] + start,
                    "end": seg["end"] + start,
                    "text": seg["text"],
                    "language": seg.get("language"),
                }
                if shifted["end"] <= last_end:
                    continue
                merged.append(shifted)
                last_end = max(last_end, shifted["end"] - (config.long_video_overlap_sec * 0.5))

            if progress_cb:
                progress_cb(
                    min(int(10 + 80 * (i + 1) / total), 95),
                    f"Transcribing chunk {i + 1}/{total}... {min(end, duration):.0f}s / {duration:.0f}s",
                )

        if diarization:
            merged = _merge_diarization_with_segments(merged, diarization)
        return merged


def transcribe(
    wav_path: Path,
    config: Config,
    progress_cb=None,
    diarization: list[dict] = None,
    runtime: ASRRuntime | None = None,
    audio_duration_sec: float | None = None,
    allow_chunking: bool = True,
) -> list[dict]:
    """
    Transcribe audio with support for fast-path (explicit language) or multilingual detection.

    FAST-PATH (user-specified language):
    - If config.language is a concrete code (e.g. "en", "ru") → skip language detection,
      transcribe directly with that language (single pass).

    MULTILINGUAL PATH (auto-detect):
    - If config.language is None or "auto" → perform language detection across 30s windows,
      then transcribe with native multilingual mode or segment-based fallback.

    Each segment includes a 'language' field indicating the detected/specified language.
    If diarization is provided, also includes a 'speaker' field.

    Args:
        wav_path: Path to WAV audio file
        config: Config object
        progress_cb: Optional progress callback (percent, message)
        diarization: Optional pre-computed diarization results (list of speaker turn dicts)

    Returns:
        List of segment dicts with text, start, end, language, and optionally speaker fields
    """
    runtime_obj, owned = _get_runtime(config, runtime)
    try:
        duration_hint = audio_duration_sec if audio_duration_sec is not None else None
        if duration_hint is None and allow_chunking:
            try:
                duration_hint = get_duration(wav_path)
            except Exception:
                duration_hint = None

        if (
            allow_chunking
            and duration_hint
            and duration_hint >= config.long_video_threshold_sec
        ):
            _log(
                f"Long video detected ({duration_hint:.0f}s), enabling chunked ASR "
                f"({config.long_video_chunk_sec}s chunks)"
            )
            segments = _transcribe_chunked(
                wav_path,
                config,
                progress_cb=progress_cb,
                runtime=runtime_obj,
                diarization=diarization,
                audio_duration_sec=duration_hint,
            )
            segments = _filter_hallucinations(segments)
            if progress_cb:
                progress_cb(100, f"Transcription complete: {len(segments)} segments")
            return segments

        # Check for fast-path: user specified a concrete language
        if config.language and config.language != "auto":
            _log(f"Fast-path enabled: language={config.language}, skipping language detection")
            segments, detected_lang, lang_prob = transcribe_single_language(
                wav_path,
                config,
                config.language,
                progress_cb=progress_cb,
                runtime=runtime_obj,
                duration_hint_sec=duration_hint,
            )
            _log(f"Fast-path transcription complete: {len(segments)} segments")
            if progress_cb:
                progress_cb(10, f"Using language: {config.language}")
        else:
            language_map = None
            enable_multilingual = False

            if config.multilingual == "true":
                enable_multilingual = True
            elif config.multilingual == "auto":
                if progress_cb:
                    progress_cb(5, "Detecting languages in audio...")
                language_map = _detect_languages(wav_path, config, runtime=runtime_obj)
                enable_multilingual = _is_multilingual(language_map, config.multilingual_threshold)
                _log(f"Multilingual auto-detection: enable={enable_multilingual}")

            if enable_multilingual:
                if progress_cb:
                    progress_cb(8, "Multilingual mode detected, preparing for adaptive transcription...")
                segments, detected_lang, lang_prob = _transcribe_with_language_fallback(
                    wav_path,
                    config,
                    language_map,
                    progress_cb=progress_cb,
                    runtime=runtime_obj,
                    duration_hint_sec=duration_hint,
                )
                _log(
                    f"Multilingual transcription complete: {len(segments)} segments, "
                    f"primary={detected_lang} ({lang_prob:.0%})"
                )
                if progress_cb:
                    progress_cb(
                        10,
                        f"Detected languages (multilingual), primary: {detected_lang} ({lang_prob:.0%})",
                    )
            else:
                opts = _transcribe_options(config, duration_hint)
                segments_raw, info = runtime_obj.model.transcribe(
                    str(wav_path),
                    language=None,  # Auto-detect per segment
                    **opts,
                )
                _log(
                    f"Detected language: {info.language} ({info.language_probability:.0%}), "
                    f"duration: {info.duration:.0f}s"
                )
                if progress_cb:
                    progress_cb(10, f"Detected language: {info.language} ({info.language_probability:.0%})")
                segments = []
                for i, seg in enumerate(segments_raw):
                    segments.append(
                        {
                            "start": seg.start,
                            "end": seg.end,
                            "text": seg.text.strip(),
                            "language": info.language,
                        }
                    )
                    if progress_cb and i % 10 == 0:
                        progress_cb(
                            min(10 + int(80 * (seg.end / max(info.duration, 1))), 90),
                            f"Transcribing... {seg.end:.0f}s / {info.duration:.0f}s",
                        )
                _log(f"Transcription complete: {len(segments)} segments")

        if diarization:
            _log(f"Merging diarization ({len(diarization)} speaker turns) with {len(segments)} segments")
            segments = _merge_diarization_with_segments(segments, diarization)

        segments = _filter_hallucinations(segments)
        if progress_cb:
            progress_cb(100, f"Transcription complete: {len(segments)} segments")

        return segments
    finally:
        if owned:
            runtime_obj.close()
