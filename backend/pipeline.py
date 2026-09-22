import gc
import json
import math
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from config import Config
from downloader import download_audio
from audio import (
    WHISPER_EXTRACT_AUDIO_FORMAT,
    extract_audio,
    extract_audio_slice,
    get_duration,
)

from transcriber import transcribe, ASRRuntime, resolve_device
from summarizer import summarize
from formatter import format_markdown, format_batch_markdown
from eta import estimate, record_run, format_eta


def _log(msg):
    """Log pipeline messages to stderr."""
    print(f"[pipeline] {msg}", file=sys.stderr, flush=True)


def _log_security(msg):
    """Log security-related messages to stderr."""
    print(f"[pipeline_security] {msg}", file=sys.stderr, flush=True)


def _validate_source(source: str) -> str:
    """
    Validate source to be either a URL with http/https or a valid file path.

    Args:
        source: URL or file path

    Returns:
        Validated source string

    Raises:
        ValueError: If source is invalid
    """
    if not source or not isinstance(source, str):
        raise ValueError("Invalid source: must be a non-empty string")

    # Check for URL
    if source.startswith(("http://", "https://")):
        # Validate URL format
        try:
            parsed = urlparse(source)
            if not parsed.netloc:
                _log_security(f"Invalid URL: no network location in {repr(source)}")
                raise ValueError("Invalid URL format")
        except Exception as e:
            _log_security(f"URL parsing error: {e}")
            raise ValueError(f"Invalid URL: {str(e)}")
        return source

    # Check for file path
    try:
        path = Path(source).resolve()
        # Verify path exists (will fail for non-existent files)
        # For now, just ensure it's a valid path and not escaped
        if ".." in str(source) or source.startswith("/"):
            # Allow absolute paths but be careful
            _log_security(f"Absolute path used: {path}")
        return str(path)
    except Exception as e:
        _log_security(f"Path validation error for {repr(source)}: {e}")
        raise ValueError(f"Invalid file path: {str(e)}")


def _validate_output_dir(output_dir: str, base_max_depth: int = 5) -> Path:
    """
    Validate output directory is safe and not escaped.

    Args:
        output_dir: Output directory path
        base_max_depth: Maximum directory depth to allow

    Returns:
        Validated Path object

    Raises:
        ValueError: If directory is invalid
    """
    if not output_dir or not isinstance(output_dir, str):
        raise ValueError("Invalid output_dir")

    try:
        path = Path(output_dir).resolve()
        # Check for path traversal in the string representation
        if ".." in output_dir:
            _log_security(f"Path traversal attempt in output_dir: {repr(output_dir)}")
            raise ValueError("Path traversal not allowed")
        return path
    except Exception as e:
        _log_security(f"Output directory validation error: {e}")
        raise ValueError(f"Invalid output directory: {str(e)}")


def _cleanup_summary_checkpoints(output_dir: Path) -> None:
    """
    Remove summary map-phase checkpoint files under output_dir/.checkpoints.

    These JSON files exist to resume interrupted summarization; after a successful
    run they are stale. Best-effort only (permissions, concurrent access).
    """
    cp_dir = output_dir / ".checkpoints"
    if not cp_dir.is_dir():
        return
    removed = 0
    try:
        for f in cp_dir.glob("summary_*.json"):
            try:
                f.unlink(missing_ok=True)
                removed += 1
            except OSError as e:
                _log(f"Could not remove summary checkpoint {f}: {e}")
        try:
            if not any(cp_dir.iterdir()):
                cp_dir.rmdir()
        except OSError:
            pass
        if removed:
            _log(f"Removed {removed} summary checkpoint file(s) under {cp_dir}")
    except OSError as e:
        _log(f"Summary checkpoint cleanup skipped: {e}")


def _normalize_runtime_config(config: Config) -> None:
    """Normalize key runtime fields to safe defaults for resilient execution/tests."""
    if not isinstance(config.whisper_model, str) or not config.whisper_model:
        config.whisper_model = "small"
    if not isinstance(config.device, str) or not config.device:
        config.device = "auto"
    if not isinstance(config.compute_type, str) or not config.compute_type:
        config.compute_type = "auto"
    if config.language is not None and not isinstance(config.language, str):
        config.language = None
    if not isinstance(config.output_dir, str) or not config.output_dir:
        config.output_dir = "./output"
    if not isinstance(config.speaker_names, dict):
        config.speaker_names = {}


class Pipeline:
    def __init__(self, progress_callback):
        self.send = progress_callback
        self._cancel_event = threading.Event()
        self._last_progress_by_id: dict[int, dict] = {}
        self._oracle = None  # lazily created on first oracle.chat call

    def cancel(self):
        self._cancel_event.set()

    # ----- Oracle (post-transcription chat) ---------------------------

    def _get_oracle(self):
        if self._oracle is None:
            from oracle import Oracle
            self._oracle = Oracle(self.send)
        return self._oracle

    def oracle_chat(self, msg_id: int, params: dict) -> dict:
        """Route a user question to the Oracle. Streams tokens via send()."""
        return self._get_oracle().chat(msg_id, params)

    def oracle_cancel(self) -> None:
        if self._oracle is not None:
            self._oracle.cancel()

    def oracle_close(self) -> None:
        if self._oracle is not None:
            self._oracle.close()

    def _progress(self, msg_id, stage, percent, message):
        percent = int(max(0, min(100, percent)))
        now = time.monotonic()
        state = self._last_progress_by_id.get(msg_id)
        should_emit = (
            state is None
            or state["stage"] != stage
            or percent >= 100
            or percent <= state["percent"]
            or (percent - state["percent"]) >= 2
            or (now - state["ts"]) >= 0.25
        )
        if not should_emit:
            return
        self._last_progress_by_id[msg_id] = {"stage": stage, "percent": percent, "ts": now}
        self.send({
            "id": msg_id,
            "type": "progress",
            "stage": stage,
            "percent": percent,
            "message": message,
        })

    def _eta(self, msg_id, stage, eta_dict):
        """Send ETA event to client."""
        self.send({
            "id": msg_id,
            "type": "eta",
            "stage": stage,
            **eta_dict,
        })

    def _resource_snapshot(self) -> dict:
        snap = {"ts": time.time()}
        try:
            import psutil
            proc = psutil.Process()
            mem = proc.memory_info()
            snap["ram_mb"] = round(mem.rss / (1024 * 1024), 1)
        except Exception:
            pass
        try:
            import torch
            if torch.cuda.is_available():
                snap["cuda_alloc_mb"] = round(torch.cuda.memory_allocated() / (1024 * 1024), 1)
                snap["cuda_reserved_mb"] = round(torch.cuda.memory_reserved() / (1024 * 1024), 1)
        except Exception:
            pass
        return snap

    def _write_baseline_metrics(self, output_dir: Path, stem: str, metrics: dict):
        try:
            metrics_dir = output_dir / "metrics"
            metrics_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = metrics_dir / f"{stem}_metrics.json"
            metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            _log(f"Failed to persist metrics: {e}")

    def _transcribe_prechunked(
        self,
        msg_id: int,
        stage: str,
        chunks: list[dict],
        config: Config,
        duration_sec: float | None = None,
        batch_progress_mapper=None,
        batch_prefix: str = "",
    ) -> list[dict]:
        """Transcribe pre-extracted WAV chunks sequentially and merge timestamps."""
        asr_runtime = ASRRuntime(config)
        merged: list[dict] = []
        last_end = -1.0
        total = max(len(chunks), 1)
        try:
            for i, chunk in enumerate(chunks):
                chunk_start = float(chunk["start"])
                chunk_end = float(chunk["end"])
                chunk_duration = max(chunk_end - chunk_start, 0.1)

                def _chunk_progress(p: int, msg: str):
                    overall = int(((i + (max(0, min(100, p)) / 100.0)) / total) * 100)
                    if batch_progress_mapper:
                        mapped = batch_progress_mapper(overall)
                        self._progress(
                            msg_id,
                            stage,
                            mapped,
                            f"{batch_prefix}Chunk {i + 1}/{total}: {msg}",
                        )
                    else:
                        self._progress(
                            msg_id,
                            stage,
                            overall,
                            f"Chunk {i + 1}/{total}: {msg}",
                        )

                chunk_segments = transcribe(
                    chunk["path"],
                    config,
                    progress_cb=_chunk_progress,
                    diarization=None,
                    runtime=asr_runtime,
                    audio_duration_sec=chunk_duration,
                    allow_chunking=False,
                )

                for seg in chunk_segments:
                    shifted = {
                        "start": seg["start"] + chunk_start,
                        "end": seg["end"] + chunk_start,
                        "text": seg["text"],
                        "language": seg.get("language"),
                    }
                    # Drop duplicate overlap content between adjacent chunks.
                    if shifted["end"] <= last_end:
                        continue
                    merged.append(shifted)
                    last_end = max(last_end, shifted["end"] - (config.long_video_overlap_sec * 0.5))

                if batch_progress_mapper:
                    self._progress(
                        msg_id,
                        stage,
                        batch_progress_mapper(int(((i + 1) / total) * 100)),
                        f"{batch_prefix}Transcribed chunk {i + 1}/{total}",
                    )
                else:
                    self._progress(
                        msg_id,
                        stage,
                        int(((i + 1) / total) * 100),
                        f"Transcribed chunk {i + 1}/{total}",
                    )
        finally:
            asr_runtime.close()

        if duration_sec:
            _log(f"Pre-chunked transcription complete: {len(merged)} segments over {duration_sec:.0f}s")
        else:
            _log(f"Pre-chunked transcription complete: {len(merged)} segments")
        return merged

    def _transcribe_streaming_source(
        self,
        msg_id: int,
        stage: str,
        source_path: Path,
        config: Config,
        duration_sec: float,
        work_dir: Path,
        batch_progress_mapper=None,
        batch_prefix: str = "",
        asr_runtime: ASRRuntime | None = None,
    ) -> list[dict]:
        """For long media: extract+transcribe one chunk at a time.

        If ``asr_runtime`` is provided, the caller owns its lifecycle (do not close here).
        If ``None``, a runtime is created for this call and closed on exit.
        """
        step = max(config.long_video_chunk_sec - config.long_video_overlap_sec, 1.0)
        total_chunks = max(1, math.ceil(duration_sec / step))
        merged: list[dict] = []
        last_end = -1.0
        owned_runtime = asr_runtime is None
        if asr_runtime is None:
            asr_runtime = ASRRuntime(config)
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            for idx in range(total_chunks):
                chunk_start = idx * step
                if chunk_start >= duration_sec:
                    break
                chunk_end = min(chunk_start + config.long_video_chunk_sec, duration_sec)
                chunk_duration = max(chunk_end - chunk_start, 0.1)
                chunk_path = work_dir / f"stream_chunk_{idx:04d}.wav"

                if batch_progress_mapper:
                    self._progress(
                        msg_id,
                        stage,
                        batch_progress_mapper(int(100 * idx / total_chunks)),
                        f"{batch_prefix}Preparing chunk {idx + 1}/{total_chunks}...",
                    )
                else:
                    self._progress(
                        msg_id,
                        stage,
                        int(100 * idx / total_chunks),
                        f"Preparing chunk {idx + 1}/{total_chunks}...",
                    )

                extract_audio_slice(
                    source_path,
                    chunk_path,
                    start_sec=chunk_start,
                    duration_sec=chunk_duration,
                    chunk_timeout_sec=config.ffmpeg_chunk_extract_timeout_sec,
                )

                def _chunk_progress(p: int, msg: str):
                    bounded = max(0, min(100, int(p)))
                    overall = int(((idx + (bounded / 100.0)) / total_chunks) * 100)
                    if batch_progress_mapper:
                        self._progress(
                            msg_id,
                            stage,
                            batch_progress_mapper(overall),
                            f"{batch_prefix}Chunk {idx + 1}/{total_chunks}: {msg}",
                        )
                    else:
                        self._progress(
                            msg_id,
                            stage,
                            overall,
                            f"Chunk {idx + 1}/{total_chunks}: {msg}",
                        )

                chunk_segments = transcribe(
                    chunk_path,
                    config,
                    progress_cb=_chunk_progress,
                    diarization=None,
                    runtime=asr_runtime,
                    audio_duration_sec=chunk_duration,
                    allow_chunking=False,
                )

                for seg in chunk_segments:
                    shifted = {
                        "start": seg["start"] + chunk_start,
                        "end": seg["end"] + chunk_start,
                        "text": seg["text"],
                        "language": seg.get("language"),
                    }
                    if shifted["end"] <= last_end:
                        continue
                    merged.append(shifted)
                    last_end = max(last_end, shifted["end"] - (config.long_video_overlap_sec * 0.5))

                chunk_path.unlink(missing_ok=True)

            return merged
        finally:
            if owned_runtime:
                asr_runtime.close()

    def run(self, msg_id: int, params: dict) -> dict:
        self._cancel_event.clear()
        self._last_progress_by_id.pop(msg_id, None)
        # Release any Oracle LLM before starting a new run — we never want
        # two LLM instances resident in memory simultaneously (see README).
        try:
            self.oracle_close()
        except Exception as e:
            _log(f"oracle_close at run start failed (continuing): {e}")
        config = Config(**params.get("config", {}))
        source = params["source"]

        # Validate source
        try:
            source = _validate_source(source)
        except ValueError as e:
            _log_security(f"Source validation failed: {e}")
            raise ValueError(f"Invalid source: {str(e)}")

        # Optionally override llm_model_preset and whisper_model from params
        if "llm_model_preset" in params:
            config.llm_model_preset = params["llm_model_preset"]
        if "whisper_model" in params:
            config.whisper_model = params["whisper_model"]

        # Optionally override summary_mode and summary_mode_config from params
        if "summary_mode" in params:
            config.summary_mode = params["summary_mode"]
        if "summary_mode_config" in params:
            if isinstance(params["summary_mode_config"], dict):
                config.summary_mode_config = params["summary_mode_config"]
            else:
                _log_security(f"Invalid summary_mode_config type: {type(params['summary_mode_config'])}")
                raise ValueError("summary_mode_config must be a dictionary")

        # Optionally override multilingual mode and threshold from params
        if "multilingual" in params:
            config.multilingual = params["multilingual"]
        if "multilingual_threshold" in params:
            try:
                threshold = float(params["multilingual_threshold"])
                if not 0.0 <= threshold <= 1.0:
                    raise ValueError("threshold out of range")
                config.multilingual_threshold = threshold
            except (ValueError, TypeError) as e:
                _log_security(f"Invalid multilingual_threshold: {e}")
                raise ValueError(f"multilingual_threshold must be between 0.0 and 1.0")

        # Optionally override summary_language from params
        if "summary_language" in params:
            config.summary_language = params["summary_language"]

        # Optionally override no_summary from params
        if "no_summary" in params:
            config.no_summary = bool(params["no_summary"])

        # Optionally override topic segmentation settings from params
        if "topic_segmentation" in params:
            config.topic_segmentation = bool(params["topic_segmentation"])
        if "topic_min_segments" in params:
            config.topic_min_segments = max(3, int(params["topic_min_segments"]))
        if "topic_boundary_confidence" in params:
            try:
                conf = float(params["topic_boundary_confidence"])
                if 0.0 <= conf <= 1.0:
                    config.topic_boundary_confidence = conf
            except (ValueError, TypeError):
                pass

        # Optionally override diarization settings from params
        if "diarize" in params:
            config.diarize = bool(params["diarize"])
        if "speaker_names" in params:
            if isinstance(params["speaker_names"], dict):
                config.speaker_names = params["speaker_names"]
            else:
                _log_security(f"Invalid speaker_names type: {type(params['speaker_names'])}")
                raise ValueError("speaker_names must be a dictionary")

        _normalize_runtime_config(config)

        stage_times: dict[str, float] = {}
        stage_resources: dict[str, dict] = {}
        stage_resources["start"] = self._resource_snapshot()

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            tmpdir = Path(tmpdir)

            # 1. Download or copy
            self._progress(msg_id, "download", 0, "Preparing source...")
            stage_start = time.monotonic()
            is_url = source.startswith(("http://", "https://"))
            remote_title: str | None = None
            _log(f"Source type: {'URL' if is_url else 'local file'}, source={source[:150]}")
            if is_url:
                audio_path, remote_title = download_audio(
                    source, tmpdir,
                    lambda p: self._progress(msg_id, "download", p, f"Downloading... {p}%"),
                )
            else:
                audio_path = Path(source)
                if not audio_path.exists():
                    raise FileNotFoundError(f"Source file not found: {audio_path}")
                audio_path = audio_path.resolve()
            stage_times["download"] = time.monotonic() - stage_start
            try:
                src_size_mb = audio_path.stat().st_size / (1024 * 1024)
                _log(f"Download done in {stage_times['download']:.1f}s → {audio_path} ({src_size_mb:.1f} MB)")
            except Exception:
                _log(f"Download done in {stage_times['download']:.1f}s → {audio_path}")

            if self._cancel_event.is_set():
                raise Exception("Cancelled")

            # 2.5. Resolve duration for ETA + transcribe hints.
            try:
                duration_sec = get_duration(audio_path)
                _log(f"Source duration: {duration_sec:.1f}s ({duration_sec/60:.1f}min)")
            except Exception as e:
                _log(f"Failed to get duration: {e}, skipping ETA")
                duration_sec = None

            use_prechunked_long_mode = (
                duration_sec is not None
                and duration_sec >= config.streaming_extract_threshold_sec
                and not config.diarize
            )

            # 3. Extract clean audio (strips subtitle/data/video tracks via -sn -dn -vn).
            #    Skip extraction when: (a) input is audio-only, or (b) streaming mode
            #    will be used (extract_audio_slice already strips per-chunk).
            _AUDIO_ONLY_EXTS = {".wav", ".mp3", ".ogg", ".opus", ".flac", ".aac", ".m4a", ".wma"}
            extract_start = time.monotonic()
            if audio_path.suffix.lower() in _AUDIO_ONLY_EXTS:
                wav_path = audio_path
                self._progress(msg_id, "extract", 100, "Audio file — no extraction needed")
                stage_times["extract"] = 0.0
                _log(f"Extract skipped: {audio_path.suffix} is audio-only, no subtitle tracks to strip")
                try:
                    nbytes = audio_path.stat().st_size
                    mib = nbytes / (1024 * 1024)
                    _log(
                        f"Using source audio in-place: file={audio_path.resolve()} | "
                        f"size={nbytes} bytes ({mib:.2f} MiB) | container={audio_path.suffix.lower()}"
                    )
                except OSError as e:
                    _log(f"Could not stat source audio for logging: {e}")
            elif use_prechunked_long_mode:
                wav_path = audio_path
                self._progress(msg_id, "extract", 100, "Streaming mode — per-chunk extraction")
                stage_times["extract"] = 0.0
                chunk_dir = tmpdir / "stream_chunks"
                _log(
                    "Extract skipped (streaming): one full WAV is not written — ffmpeg slices per chunk "
                    f"into {chunk_dir.resolve()} as stream_chunk_*.wav | format={WHISPER_EXTRACT_AUDIO_FORMAT} "
                    "(flags -vn -sn -dn, -acodec pcm_s16le -ar 16000 -ac 1)"
                )
            else:
                self._progress(msg_id, "extract", 0, "Extracting audio...")
                wav_path = tmpdir / "audio.wav"
                extract_audio(
                    audio_path, wav_path,
                    progress_cb=lambda p: self._progress(msg_id, "extract", p, f"Extracting audio... {p}%"),
                    duration_sec=duration_sec,
                    cancel_event=self._cancel_event,
                )
                stage_times["extract"] = time.monotonic() - extract_start
                _log(f"Extract done in {stage_times['extract']:.1f}s → {wav_path}")

            if self._cancel_event.is_set():
                raise Exception("Cancelled")

            if duration_sec is not None:
                try:
                    device_resolved = resolve_device(config.device)
                    eta_estimate = estimate(config, duration_sec, device_resolved)
                    self._eta(msg_id, "pre", {
                        "duration_sec": duration_sec,
                        "transcribe_sec": eta_estimate["transcribe_sec"],
                        "summarize_sec": eta_estimate["summarize_sec"],
                        "diarize_sec": eta_estimate["diarize_sec"],
                        "total_sec": eta_estimate["total_sec"],
                        "source": eta_estimate["source"],
                    })
                except Exception as e:
                    _log(f"ETA estimation failed: {e}")

            # 4. Transcribe (load model -> transcribe -> unload)
            if config.language and config.language != "auto":
                _log(f"language={config.language} → fast-path enabled, skipping language scan")

            self._progress(msg_id, "transcribe", 0, "Loading transcription model...")
            transcribe_start = time.monotonic()
            asr_runtime = ASRRuntime(config)
            try:
                self._progress(
                    msg_id,
                    "transcribe",
                    2,
                    "Transcription model loaded — starting speech pass...",
                )
                if use_prechunked_long_mode and duration_sec is not None:
                    segments = self._transcribe_streaming_source(
                        msg_id,
                        "transcribe",
                        audio_path,
                        config,
                        duration_sec=duration_sec,
                        work_dir=tmpdir / "stream_chunks",
                        asr_runtime=asr_runtime,
                    )
                else:
                    segments = transcribe(
                        wav_path, config,
                        progress_cb=lambda p, msg: self._progress(msg_id, "transcribe", p, msg),
                        runtime=asr_runtime,
                        audio_duration_sec=duration_sec,
                    )
            finally:
                _log("Closing ASR runtime...")
                asr_runtime.close()
                _log("ASR runtime closed")
            transcribe_elapsed = time.monotonic() - transcribe_start
            stage_times["transcribe"] = transcribe_elapsed
            _log(f"Transcribe stage done: {transcribe_elapsed:.1f}s, {len(segments)} segments")
            stage_resources["post_transcribe"] = self._resource_snapshot()

            # Record transcription time for calibration
            try:
                device_resolved = resolve_device(config.device)
                rt_factor = transcribe_elapsed / duration_sec if duration_sec else None
                if rt_factor:
                    record_run("transcribe", f"{config.whisper_model}_{device_resolved}", rt_factor)
            except Exception as e:
                _log(f"Failed to record transcription time: {e}")

            # Recalculate ETA now that transcription is done
            if duration_sec is not None:
                try:
                    recalc_device = resolve_device(config.device)
                    updated_eta = estimate(config, duration_sec, recalc_device)
                    remaining_sec = updated_eta["summarize_sec"]
                    self._eta(msg_id, "runtime", {
                        "remaining_sec": remaining_sec,
                        "elapsed_sec": transcribe_elapsed,
                    })
                except Exception as e:
                    _log(f"Runtime ETA recalculation failed: {e}")

            gc.collect()

            if self._cancel_event.is_set():
                raise Exception("Cancelled")

            # 5. Diarize — feature disabled, skip entirely
            stage_times["diarize"] = 0.0

            # 6. Summarize
            transcript_language = segments[0].get("language") if segments else None
            _log("Entering summarize stage...")
            if config.no_summary:
                # Skip summarization — transcription only mode
                self._progress(msg_id, "summarize", 100, "Skipped — transcription only")
                summary = "_Summary skipped (transcription-only mode)._"
                stage_times["summarize"] = 0.0
                _log("Summarization skipped (no_summary=True)")
            elif segments:
                self._progress(msg_id, "summarize", 0, "Loading summarization model...")
                _log(f"Starting summarization: {len(segments)} segments, language={transcript_language}")
                summarize_start = time.monotonic()
                summarize_last_eta_emit = 0.0
                summarize_last_remaining_sec = None  # Track previous remaining_sec for monotonicity
                def _summarize_progress(p: int, msg: str):
                    nonlocal summarize_last_eta_emit, summarize_last_remaining_sec
                    self._progress(msg_id, "summarize", p, msg)
                    # Runtime ETA for summarize stage based on actual observed speed.
                    # This quickly corrects inaccurate pre-estimates on long jobs.
                    bounded = max(1, min(99, int(p)))
                    now = time.monotonic()
                    if (now - summarize_last_eta_emit) < 2.0 and bounded < 99:
                        return
                    summarize_elapsed_live = now - summarize_start
                    # Calculate remaining time from observed pace: if X% done in Y seconds,
                    # estimate (100-X)/X * Y seconds remain.
                    remaining_sec = int(max(0, summarize_elapsed_live * (100 - bounded) / bounded))

                    # Enforce monotonicity: remaining_sec should never increase by more than 2s.
                    # This prevents jumps when progress callbacks are sparse or regression occurs.
                    if summarize_last_remaining_sec is not None:
                        # Allow small grace period (2s) for re-calibration, but hard-clamp above that.
                        max_remaining = summarize_last_remaining_sec + 2
                        remaining_sec = min(remaining_sec, max_remaining)

                    summarize_last_remaining_sec = remaining_sec
                    self._eta(msg_id, "runtime", {
                        "remaining_sec": remaining_sec,
                        "elapsed_sec": transcribe_elapsed + summarize_elapsed_live,
                    })
                    summarize_last_eta_emit = now
                summary = summarize(
                    segments, config,
                    progress_cb=_summarize_progress,
                    transcript_language=transcript_language,
                )
                summarize_elapsed = time.monotonic() - summarize_start
                stage_times["summarize"] = summarize_elapsed

                try:
                    device_resolved = resolve_device(config.device)

                    llm_key_parts = []
                    if config.llm_model_path:
                        llm_key_parts.append(Path(config.llm_model_path).stem)
                    else:
                        repo_name = config.llm_model_repo.split("/")[-1].lower()
                        file_name = config.llm_model_file.split(".")[0].lower()
                        llm_key_parts.append(f"{repo_name}_{file_name}")

                    llm_key = f"{'_'.join(llm_key_parts)}_{device_resolved}".replace("-", "_")

                    n_chunks = max(
                        1,
                        math.ceil((duration_sec / 60.0) * 150 / config.chunk_size_tokens),
                    ) if duration_sec else 1

                    avg_sec_per_chunk = summarize_elapsed / n_chunks if n_chunks else summarize_elapsed
                    record_run("summarize", llm_key, avg_sec_per_chunk)
                except Exception as e:
                    _log(f"Failed to record summarization time: {e}")
            else:
                summary = "_No speech detected in the video._"
                stage_times["summarize"] = 0.0

            if self._cancel_event.is_set():
                raise Exception("Cancelled")

            # 7. Format & save
            self._progress(msg_id, "format", 90, "Generating markdown...")
            stage_start = time.monotonic()
            if source.startswith(("http://", "https://")):
                title = (remote_title or "").strip() or source
            else:
                title = Path(source).stem
            markdown = format_markdown(title, source, segments, summary, speaker_names=config.speaker_names)

            # Validate output directory
            try:
                output_dir = _validate_output_dir(params.get("output_dir", config.output_dir))
            except ValueError as e:
                _log_security(f"Output directory validation failed: {e}")
                raise ValueError(f"Invalid output directory: {str(e)}")

            output_dir.mkdir(parents=True, exist_ok=True)

            stem = Path(source).stem if not source.startswith("http") else "video"
            safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in stem)[:80]
            output_path = output_dir / f"{safe_name}_summary.md"
            output_path.write_text(markdown, encoding="utf-8")
            _cleanup_summary_checkpoints(output_dir)
            stage_times["format"] = time.monotonic() - stage_start
            stage_times["total"] = sum(stage_times.values())
            stage_resources["end"] = self._resource_snapshot()

            metrics = {
                "source": source,
                "duration_sec": duration_sec,
                "stage_times_sec": stage_times,
                "resource_snapshots": stage_resources,
                "segments": len(segments),
            }
            self._write_baseline_metrics(output_dir, safe_name, metrics)

            self._progress(msg_id, "format", 100, "Done!")

            return {"markdown": markdown, "output_path": str(output_path), "metrics": metrics}

    def run_batch(self, msg_id: int, params: dict) -> dict:
        """
        Process multiple video sources and generate a combined report.

        Args:
            msg_id: Message ID for progress callbacks
            params: Dict with:
                - sources: list[str] — list of URLs or file paths
                - config: dict — configuration (same as run())
                - output_dir: str — output directory (optional)
                - summary_mode: str (optional) — override config
                - summary_mode_config: dict (optional) — override config
                - multilingual: str (optional) — override config
                - multilingual_threshold: float (optional) — override config
                - diarize: bool (optional) — override config
                - speaker_names: dict (optional) — override config

        Returns:
            Dict with:
                - markdown: str — formatted batch report
                - output_path: str — path to saved report
                - videos_processed: int — number of successfully processed videos
        """
        self._cancel_event.clear()
        self._last_progress_by_id.pop(msg_id, None)
        config = Config(**params.get("config", {}))
        sources = params.get("sources", [])

        if not sources:
            raise ValueError("No sources provided for batch processing")

        # Validate and sanitize sources
        if not isinstance(sources, list):
            raise ValueError("sources must be a list")

        # Apply resource limits
        if len(sources) > config.batch_max_files:
            _log_security(f"Batch size {len(sources)} exceeds limit {config.batch_max_files}")
            raise ValueError(f"Batch size exceeds limit of {config.batch_max_files} files")

        # Validate each source
        validated_sources = []
        for i, source in enumerate(sources):
            try:
                validated_source = _validate_source(source)
                validated_sources.append(validated_source)
            except ValueError as e:
                _log_security(f"Source {i} validation failed: {e}")
                raise ValueError(f"Invalid source at index {i}: {str(e)}")

        sources = validated_sources

        # Apply parameter overrides
        if "llm_model_preset" in params:
            config.llm_model_preset = params["llm_model_preset"]
        if "whisper_model" in params:
            config.whisper_model = params["whisper_model"]

        # Apply parameter overrides
        if "summary_mode" in params:
            config.summary_mode = params["summary_mode"]
        if "summary_mode_config" in params:
            if isinstance(params["summary_mode_config"], dict):
                config.summary_mode_config = params["summary_mode_config"]
            else:
                _log_security(f"Invalid summary_mode_config type: {type(params['summary_mode_config'])}")
                raise ValueError("summary_mode_config must be a dictionary")
        if "multilingual" in params:
            config.multilingual = params["multilingual"]
        if "multilingual_threshold" in params:
            try:
                threshold = float(params["multilingual_threshold"])
                if not 0.0 <= threshold <= 1.0:
                    raise ValueError("threshold out of range")
                config.multilingual_threshold = threshold
            except (ValueError, TypeError) as e:
                _log_security(f"Invalid multilingual_threshold: {e}")
                raise ValueError(f"multilingual_threshold must be between 0.0 and 1.0")
        if "summary_language" in params:
            config.summary_language = params["summary_language"]
        if "no_summary" in params:
            config.no_summary = bool(params["no_summary"])
        if "diarize" in params:
            config.diarize = bool(params["diarize"])
        if "speaker_names" in params:
            if isinstance(params["speaker_names"], dict):
                config.speaker_names = params["speaker_names"]
            else:
                _log_security(f"Invalid speaker_names type: {type(params['speaker_names'])}")
                raise ValueError("speaker_names must be a dictionary")

        _normalize_runtime_config(config)

        batch_results = []
        total_segments_all = []  # Accumulate segments for combined summary

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir_root:
            tmpdir_root = Path(tmpdir_root)
            # Phase 1: Transcribe all videos (ASR only — no LLM in memory)
            asr_runtime = ASRRuntime(config) if config.batch_reuse_asr_runtime else None
            transcribed_videos: list[dict] = []
            try:
                for video_idx, source in enumerate(sources, 1):
                    self._progress(msg_id, "batch", int(5 + 90 * (video_idx - 1) / len(sources)), f"Processing video {video_idx}/{len(sources)}: {source}")
                    try:
                        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                            tmpdir = Path(tmpdir)

                            self._progress(msg_id, "batch", int(5 + 90 * (video_idx - 1) / len(sources)), f"[{video_idx}/{len(sources)}] Preparing source...")
                            remote_title: str | None = None
                            if source.startswith(("http://", "https://")):
                                audio_path, remote_title = download_audio(
                                    source, tmpdir,
                                    lambda p: self._progress(msg_id, "batch", int(5 + 90 * (video_idx - 1) / len(sources) + 5 * p / 100), f"[{video_idx}/{len(sources)}] Downloading... {p}%"),
                                )
                            else:
                                audio_path = Path(source)
                                if not audio_path.exists():
                                    raise FileNotFoundError(f"Source file not found: {audio_path}")
                                audio_path = audio_path.resolve()

                            if self._cancel_event.is_set():
                                raise Exception("Cancelled")

                            video_duration = None
                            try:
                                video_duration = get_duration(audio_path)
                            except Exception:
                                video_duration = None
                            use_prechunked_long_mode = (
                                video_duration is not None
                                and video_duration >= config.streaming_extract_threshold_sec
                                and not config.diarize
                            )

                            _AUDIO_ONLY_EXTS = {".wav", ".mp3", ".ogg", ".opus", ".flac", ".aac", ".m4a", ".wma"}
                            if audio_path.suffix.lower() in _AUDIO_ONLY_EXTS:
                                wav_path = audio_path
                                self._progress(msg_id, "batch", max(1, int(10 + 90 * (video_idx - 1) / len(sources))), f"[{video_idx}/{len(sources)}] Audio file — no extraction needed")
                                _log(f"[batch {video_idx}] Extract skipped: {audio_path.suffix} is audio-only")
                                try:
                                    nbytes = audio_path.stat().st_size
                                    mib = nbytes / (1024 * 1024)
                                    _log(
                                        f"[batch {video_idx}] Using source audio in-place: file={audio_path} | "
                                        f"size={nbytes} bytes ({mib:.2f} MiB) | container={audio_path.suffix.lower()}"
                                    )
                                except OSError as e:
                                    _log(f"[batch {video_idx}] Could not stat source audio: {e}")
                            elif use_prechunked_long_mode:
                                wav_path = audio_path
                                self._progress(
                                    msg_id, "batch", max(1, int(10 + 90 * (video_idx - 1) / len(sources))),
                                    f"[{video_idx}/{len(sources)}] Streaming mode — per-chunk extraction",
                                )
                                bchunk = tmpdir / "stream_chunks"
                                _log(
                                    f"[batch {video_idx}] Extract skipped (streaming): per-chunk WAV under "
                                    f"{bchunk.resolve()} as stream_chunk_*.wav | format={WHISPER_EXTRACT_AUDIO_FORMAT}"
                                )
                            else:
                                self._progress(msg_id, "batch", max(1, int(10 + 90 * (video_idx - 1) / len(sources))), f"[{video_idx}/{len(sources)}] Extracting audio...")
                                wav_path = tmpdir / "audio.wav"
                                extract_audio(
                                    audio_path, wav_path,
                                    progress_cb=lambda p: self._progress(msg_id, "batch", max(1, int(10 + 90 * (video_idx - 1) / len(sources))), f"[{video_idx}/{len(sources)}] Extracting audio... {p}%"),
                                    duration_sec=video_duration,
                                    cancel_event=self._cancel_event,
                                )
                                _log(f"[batch {video_idx}] Batch extract done → {wav_path}")

                            if self._cancel_event.is_set():
                                raise Exception("Cancelled")

                            if config.language and config.language != "auto":
                                _log(f"language={config.language} → fast-path enabled, skipping language scan")

                            self._progress(msg_id, "batch", int(25 + 90 * (video_idx - 1) / len(sources)), f"[{video_idx}/{len(sources)}] Loading transcription model...")
                            if use_prechunked_long_mode and video_duration is not None:
                                segments = self._transcribe_streaming_source(
                                    msg_id,
                                    "batch",
                                    audio_path,
                                    config,
                                    duration_sec=video_duration,
                                    work_dir=tmpdir / "stream_chunks",
                                    batch_progress_mapper=lambda p: int(25 + 90 * (video_idx - 1) / len(sources) + 40 * p / 100),
                                    batch_prefix=f"[{video_idx}/{len(sources)}] ",
                                    asr_runtime=asr_runtime,
                                )
                            else:
                                segments = transcribe(
                                    wav_path, config,
                                    progress_cb=lambda p, msg: self._progress(msg_id, "batch", int(25 + 90 * (video_idx - 1) / len(sources) + 40 * p / 100), f"[{video_idx}/{len(sources)}] {msg}"),
                                    runtime=asr_runtime,
                                    audio_duration_sec=video_duration,
                                )
                            gc.collect()

                            if self._cancel_event.is_set():
                                raise Exception("Cancelled")

                            if segments:
                                transcribed_videos.append({
                                    "source": source,
                                    "video_idx": video_idx,
                                    "segments": segments,
                                    "remote_title": remote_title,
                                })

                            self._progress(msg_id, "batch", int(55 + 90 * (video_idx - 1) / len(sources)), f"[{video_idx}/{len(sources)}] Transcription done: {len(segments)} segments")
                    except Exception:
                        error_msg = "An error occurred processing this video"
                        self._progress(msg_id, "batch", int(55 + 90 * (video_idx - 1) / len(sources)), f"[{video_idx}/{len(sources)}] {error_msg}")
            finally:
                if asr_runtime:
                    asr_runtime.close()
                    asr_runtime = None
                gc.collect()
                _log("Phase 1 complete: ASR runtime closed, memory freed")

            if self._cancel_event.is_set():
                raise Exception("Cancelled")

            # Phase 2: Summarize (LLM only — ASR fully unloaded, diarize disabled)
            for entry in transcribed_videos:
                source = entry["source"]
                video_idx = entry["video_idx"]
                segments = entry["segments"]

                duration = segments[-1]["end"] if segments else 0
                if not source.startswith(("http://", "https://")):
                    title = Path(source).stem
                else:
                    rt = entry.get("remote_title")
                    title = (rt.strip() if isinstance(rt, str) and rt.strip() else None) or source.split("/")[-1][:50]
                batch_results.append({
                    "title": title,
                    "source": source,
                    "segments": segments,
                    "duration": duration,
                })
                total_segments_all.append({
                    "start": 0,
                    "end": 0,
                    "text": f"=== Video {video_idx}: \"{title}\" (duration: {duration:.0f}s) ===",
                })
                total_segments_all.extend(segments)

            if self._cancel_event.is_set():
                raise Exception("Cancelled")

            # Combined summarization
            if config.no_summary:
                self._progress(msg_id, "batch", 95, "Skipped — transcription only")
                combined_summary = "_Summary skipped (transcription-only mode)._"
                _log("Batch summarization skipped (no_summary=True)")
            elif total_segments_all:
                transcript_language = None
                for seg in total_segments_all:
                    if "language" in seg:
                        transcript_language = seg["language"]
                        break

                self._progress(msg_id, "batch", 70, "Loading summarization model...")
                combined_summary = summarize(
                    total_segments_all, config,
                    progress_cb=lambda p, msg: self._progress(msg_id, "batch", int(70 + 25 * p / 100), msg),
                    batch_mode=True,
                    transcript_language=transcript_language,
                )
            else:
                combined_summary = "_No speech detected in any video._"

            if self._cancel_event.is_set():
                raise Exception("Cancelled")

            # 6. Format & save
            self._progress(msg_id, "batch", 95, "Generating markdown...")
            markdown = format_batch_markdown(batch_results, combined_summary, speaker_names=config.speaker_names)

            try:
                output_dir = _validate_output_dir(params.get("output_dir", config.output_dir))
            except ValueError as e:
                _log_security(f"Output directory validation failed: {e}")
                raise ValueError(f"Invalid output directory: {str(e)}")

            output_dir.mkdir(parents=True, exist_ok=True)

            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = output_dir / f"batch_report_{timestamp}.md"
            output_path.write_text(markdown, encoding="utf-8")
            _cleanup_summary_checkpoints(output_dir)

            self._progress(msg_id, "batch", 100, "Batch processing complete!")

            return {
                "markdown": markdown,
                "output_path": str(output_path),
                "videos_processed": len(batch_results),
            }
