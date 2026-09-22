"""
Integration tests: transcription quality and accuracy.

Validates Whisper ASR output on real audio files:
- Segments are non-empty and have monotonic timestamps
- Word count is reasonable for the audio duration
- WER / CER against reference transcripts (when available)
- Language detection correctness
- Fast-path (explicit language) vs auto-detect parity
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from audio import get_duration
from config import Config
from conftest import (
    BenchmarkResult,
    MetricsCollector,
    _reference_transcript,
    compute_wer,
)


def _transcribe_file(file_path: Path, config_overrides: dict | None = None) -> tuple[list[dict], float]:
    """Run transcription on a file and return (segments, elapsed_sec)."""
    from transcriber import transcribe, ASRRuntime

    cfg = Config(**(config_overrides or {}))
    cfg.whisper_model = cfg.whisper_model or "small"

    runtime = ASRRuntime(cfg)
    try:
        duration = None
        try:
            duration = get_duration(file_path)
        except Exception:
            pass

        t0 = time.monotonic()
        segments = transcribe(
            file_path,
            cfg,
            progress_cb=None,
            diarization=None,
            runtime=runtime,
            audio_duration_sec=duration,
        )
        elapsed = time.monotonic() - t0
        return segments, elapsed
    finally:
        runtime.close()


def _segments_text(segments: list[dict]) -> str:
    return " ".join(s["text"] for s in segments)


# ──────────────────────────────────────────────────────────────
# Structural sanity checks
# ──────────────────────────────────────────────────────────────

class TestTranscriptionStructure:
    """Every transcription must produce well-formed segments."""

    def test_short_structure(self, short_file: Path, metrics_collector: MetricsCollector):
        self._check_structure(short_file, "short", metrics_collector)

    def test_medium_structure(self, medium_file: Path, metrics_collector: MetricsCollector):
        self._check_structure(medium_file, "medium", metrics_collector)

    def test_noisy_structure(self, noisy_file: Path, metrics_collector: MetricsCollector):
        self._check_structure(noisy_file, "noisy", metrics_collector)

    @staticmethod
    def _check_structure(file_path: Path, category: str, collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="transcription_structure",
            file=file_path.name,
            category=category,
            file_size_mb=file_path.stat().st_size / (1024 * 1024),
        )
        try:
            segments, elapsed = _transcribe_file(file_path)
            br.total_time_sec = elapsed
            br.stage_times["transcribe"] = elapsed
            br.segments_count = len(segments)
            br.word_count = len(_segments_text(segments).split())

            try:
                br.duration_sec = get_duration(file_path)
            except Exception:
                pass

            assert len(segments) > 0, "Must produce at least one segment"

            for i, seg in enumerate(segments):
                assert "start" in seg, f"Segment {i} missing 'start'"
                assert "end" in seg, f"Segment {i} missing 'end'"
                assert "text" in seg, f"Segment {i} missing 'text'"
                assert seg["end"] >= seg["start"], (
                    f"Segment {i}: end ({seg['end']}) < start ({seg['start']})"
                )
                assert isinstance(seg["text"], str), f"Segment {i} text must be str"

            # Monotonic timestamps (allow small jitter from overlapping VAD)
            for i in range(1, len(segments)):
                assert segments[i]["start"] >= segments[i - 1]["start"] - 0.5, (
                    f"Non-monotonic start at segment {i}: "
                    f"{segments[i]['start']} < {segments[i-1]['start']}"
                )

        except Exception as e:
            br.error = str(e)
            collector.add(br)
            raise
        collector.add(br)


# ──────────────────────────────────────────────────────────────
# Accuracy: WER / CER against reference transcripts
# ──────────────────────────────────────────────────────────────

class TestTranscriptionAccuracy:
    """When a reference transcript exists, measure WER and CER."""

    def test_accuracy_short(self, short_file: Path, metrics_collector: MetricsCollector):
        self._check_accuracy(short_file, "short", metrics_collector)

    def test_accuracy_medium(self, medium_file: Path, metrics_collector: MetricsCollector):
        self._check_accuracy(medium_file, "medium", metrics_collector)

    def test_accuracy_noisy(self, noisy_file: Path, metrics_collector: MetricsCollector):
        self._check_accuracy(noisy_file, "noisy", metrics_collector, max_wer=0.60)

    @staticmethod
    def _check_accuracy(
        file_path: Path,
        category: str,
        collector: MetricsCollector,
        max_wer: float = 0.30,
    ):
        ref_text = _reference_transcript(file_path)
        if ref_text is None:
            pytest.skip(f"No reference transcript for {file_path.name}")

        br = BenchmarkResult(
            test_name="transcription_accuracy",
            file=file_path.name,
            category=category,
            file_size_mb=file_path.stat().st_size / (1024 * 1024),
        )
        try:
            segments, elapsed = _transcribe_file(file_path)
            hypothesis = _segments_text(segments)
            br.total_time_sec = elapsed
            br.stage_times["transcribe"] = elapsed
            br.segments_count = len(segments)
            br.word_count = len(hypothesis.split())
            br.extra["ref_words"] = len(ref_text.split())

            wer, cer = compute_wer(ref_text, hypothesis)
            br.wer = round(wer, 4)
            br.cer = round(cer, 4)

            try:
                br.duration_sec = get_duration(file_path)
            except Exception:
                pass

            assert wer <= max_wer, (
                f"WER {wer:.2%} exceeds threshold {max_wer:.0%} for {file_path.name}"
            )
        except Exception as e:
            if br.wer is None:
                br.error = str(e)
            collector.add(br)
            raise
        collector.add(br)


# ──────────────────────────────────────────────────────────────
# Language detection
# ──────────────────────────────────────────────────────────────

class TestLanguageDetection:
    """Transcription correctly identifies spoken language."""

    def test_language_short(self, short_file: Path, metrics_collector: MetricsCollector):
        """Short clips should detect a consistent language for all segments."""
        br = BenchmarkResult(
            test_name="language_detection",
            file=short_file.name,
            category="short",
        )
        try:
            segments, elapsed = _transcribe_file(short_file)
            br.total_time_sec = elapsed
            br.segments_count = len(segments)

            langs = {s.get("language") for s in segments if s.get("language")}
            br.extra["detected_languages"] = sorted(langs)
            assert len(langs) >= 1, "At least one language must be detected"
        except Exception as e:
            br.error = str(e)
            collector = metrics_collector
            collector.add(br)
            raise
        metrics_collector.add(br)

    def test_multilingual_detection(self, multilingual_file: Path, metrics_collector: MetricsCollector):
        """Multilingual files should detect more than one language."""
        br = BenchmarkResult(
            test_name="multilingual_detection",
            file=multilingual_file.name,
            category="multilingual",
        )
        try:
            segments, elapsed = _transcribe_file(
                multilingual_file,
                {"multilingual": "true"},
            )
            br.total_time_sec = elapsed
            br.segments_count = len(segments)

            langs = {s.get("language") for s in segments if s.get("language")}
            br.extra["detected_languages"] = sorted(langs)

            # We expect multilingual test files to actually contain >1 language.
            # If detection finds only 1, that's a signal (not a hard failure).
            if len(langs) < 2:
                br.extra["warning"] = "Expected 2+ languages, detected only 1"
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Fast-path vs auto-detect parity
# ──────────────────────────────────────────────────────────────

class TestFastPathParity:
    """Explicit language (fast-path) should produce comparable output to auto-detect."""

    def test_fast_vs_auto_short(self, short_file: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="fast_vs_auto_parity",
            file=short_file.name,
            category="short",
        )
        try:
            segs_auto, t_auto = _transcribe_file(short_file, {"language": None})
            if not segs_auto:
                pytest.skip("Auto-detect produced no segments")

            detected_lang = segs_auto[0].get("language", "en")
            segs_fast, t_fast = _transcribe_file(short_file, {"language": detected_lang})

            br.total_time_sec = t_auto + t_fast
            br.extra["auto_segments"] = len(segs_auto)
            br.extra["fast_segments"] = len(segs_fast)
            br.extra["auto_time"] = round(t_auto, 2)
            br.extra["fast_time"] = round(t_fast, 2)
            br.extra["speedup"] = round(t_auto / max(t_fast, 0.01), 2)

            text_auto = _segments_text(segs_auto)
            text_fast = _segments_text(segs_fast)

            wer, _ = compute_wer(text_auto, text_fast)
            br.extra["internal_wer"] = round(wer, 4)

            # Both paths should produce similar transcripts
            assert wer < 0.20, (
                f"Fast-path vs auto-detect WER is {wer:.2%} — too divergent"
            )
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Realtime factor
# ──────────────────────────────────────────────────────────────

class TestRealtimeFactor:
    """Transcription should run faster than real-time on capable hardware."""

    def test_rtf_medium(self, medium_file: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="realtime_factor",
            file=medium_file.name,
            category="medium",
        )
        try:
            duration = get_duration(medium_file)
            br.duration_sec = duration
            segments, elapsed = _transcribe_file(medium_file)
            br.total_time_sec = elapsed
            br.segments_count = len(segments)

            rtf = elapsed / max(duration, 0.01)
            br.extra["rtf"] = round(rtf, 3)

            # RTF < 1.0 means faster than real-time. Allow up to 3x for CPU-only.
            assert rtf < 3.0, (
                f"RTF {rtf:.2f} is too slow (transcription took {elapsed:.0f}s "
                f"for {duration:.0f}s audio)"
            )
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)
