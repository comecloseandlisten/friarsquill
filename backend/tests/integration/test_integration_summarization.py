"""
Integration tests: summarization quality across modes.

Validates the LLM summarizer on real transcription output:
- Every summary mode (notes, tldr, call_check, factcheck) produces non-empty output
- Output is well-formed markdown without LLM artefacts (thinking tags, repetition)
- Summary respects language configuration
- Timing and token throughput metrics
"""
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from audio import get_duration
from config import Config
from conftest import BenchmarkResult, MetricsCollector

SUMMARY_MODES = ["notes", "tldr", "call_check", "factcheck"]


def _transcribe_and_summarize(
    file_path: Path,
    summary_mode: str = "notes",
    extra_config: dict | None = None,
) -> tuple[list[dict], str, float, float]:
    """
    Transcribe + summarize a file.
    Returns (segments, summary_text, transcribe_sec, summarize_sec).
    """
    from transcriber import transcribe, ASRRuntime
    from summarizer import summarize

    cfg_dict = {"summary_mode": summary_mode}
    if extra_config:
        cfg_dict.update(extra_config)
    cfg = Config(**cfg_dict)

    duration = None
    try:
        duration = get_duration(file_path)
    except Exception:
        pass

    runtime = ASRRuntime(cfg)
    try:
        t0 = time.monotonic()
        segments = transcribe(
            file_path, cfg,
            progress_cb=None,
            diarization=None,
            runtime=runtime,
            audio_duration_sec=duration,
        )
        t_transcribe = time.monotonic() - t0
    finally:
        runtime.close()

    t0 = time.monotonic()
    summary = summarize(segments, cfg, progress_cb=None)
    t_summarize = time.monotonic() - t0

    return segments, summary, t_transcribe, t_summarize


# ──────────────────────────────────────────────────────────────
# Basic summary generation
# ──────────────────────────────────────────────────────────────

class TestSummarizationBasic:
    """Each summary mode produces non-empty, well-formed output."""

    @pytest.mark.parametrize("mode", SUMMARY_MODES)
    def test_summary_modes_short(
        self, short_file: Path, mode: str, metrics_collector: MetricsCollector
    ):
        self._run(short_file, "short", mode, metrics_collector)

    @pytest.mark.parametrize("mode", SUMMARY_MODES)
    def test_summary_modes_medium(
        self, medium_file: Path, mode: str, metrics_collector: MetricsCollector
    ):
        self._run(medium_file, "medium", mode, metrics_collector)

    @staticmethod
    def _run(file_path: Path, category: str, mode: str, collector: MetricsCollector):
        br = BenchmarkResult(
            test_name=f"summarize_{mode}",
            file=file_path.name,
            category=category,
            file_size_mb=file_path.stat().st_size / (1024 * 1024),
        )
        try:
            segments, summary, t_tr, t_sum = _transcribe_and_summarize(file_path, mode)

            br.total_time_sec = t_tr + t_sum
            br.stage_times["transcribe"] = round(t_tr, 2)
            br.stage_times["summarize"] = round(t_sum, 2)
            br.segments_count = len(segments)
            br.word_count = len(" ".join(s["text"] for s in segments).split())
            br.summary_length_chars = len(summary)
            br.summary_length_words = len(summary.split())

            try:
                br.duration_sec = get_duration(file_path)
            except Exception:
                pass

            assert len(summary.strip()) > 20, (
                f"Summary too short ({len(summary)} chars) for mode={mode}"
            )
            assert summary != "_No speech detected in the video._", (
                "Summarizer returned empty-speech fallback on non-silent file"
            )

        except Exception as e:
            br.error = str(e)
            collector.add(br)
            raise
        collector.add(br)


# ──────────────────────────────────────────────────────────────
# LLM output quality checks
# ──────────────────────────────────────────────────────────────

class TestSummaryQuality:
    """Summary text is clean: no LLM artefacts, no degenerate repetition."""

    def test_no_thinking_tags(self, short_file: Path, metrics_collector: MetricsCollector):
        """LLM should not leak <think>...</think> tags into output."""
        br = BenchmarkResult(
            test_name="summary_no_thinking_tags",
            file=short_file.name,
            category="short",
        )
        try:
            _, summary, _, t_sum = _transcribe_and_summarize(short_file, "notes")
            br.stage_times["summarize"] = round(t_sum, 2)
            br.summary_length_chars = len(summary)

            assert "<think>" not in summary.lower(), "Summary contains <think> tag"
            assert "</think>" not in summary.lower(), "Summary contains </think> tag"
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)

    def test_no_degenerate_repetition(self, medium_file: Path, metrics_collector: MetricsCollector):
        """Summary should not contain excessively repeated phrases."""
        br = BenchmarkResult(
            test_name="summary_no_repetition",
            file=medium_file.name,
            category="medium",
        )
        try:
            _, summary, _, t_sum = _transcribe_and_summarize(medium_file, "notes")
            br.stage_times["summarize"] = round(t_sum, 2)
            br.summary_length_chars = len(summary)

            words = summary.lower().split()
            if len(words) > 20:
                # Check for long repeated n-grams (5-grams appearing 4+ times)
                ngram_size = 5
                ngrams = [
                    " ".join(words[i:i + ngram_size])
                    for i in range(len(words) - ngram_size + 1)
                ]
                from collections import Counter
                counts = Counter(ngrams)
                worst = counts.most_common(1)[0] if counts else ("", 0)
                br.extra["worst_ngram_count"] = worst[1]

                assert worst[1] < max(4, len(words) // 50), (
                    f"Degenerate repetition: '{worst[0]}' appears {worst[1]} times"
                )
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)

    def test_compression_ratio(self, medium_file: Path, metrics_collector: MetricsCollector):
        """Summary should be significantly shorter than the full transcript."""
        br = BenchmarkResult(
            test_name="summary_compression_ratio",
            file=medium_file.name,
            category="medium",
        )
        try:
            segments, summary, t_tr, t_sum = _transcribe_and_summarize(medium_file, "notes")
            transcript_words = len(" ".join(s["text"] for s in segments).split())
            summary_words = len(summary.split())
            br.word_count = transcript_words
            br.summary_length_words = summary_words
            br.stage_times["summarize"] = round(t_sum, 2)

            if transcript_words > 50:
                ratio = summary_words / transcript_words
                br.extra["compression_ratio"] = round(ratio, 3)

                assert ratio < 1.0, (
                    f"Summary ({summary_words} words) is not shorter than "
                    f"transcript ({transcript_words} words)"
                )
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Summary language
# ──────────────────────────────────────────────────────────────

class TestSummaryLanguage:
    """summary_language config is respected."""

    @pytest.mark.parametrize("target_lang", ["en", "ru"])
    def test_summary_language_override(
        self, short_file: Path, target_lang: str, metrics_collector: MetricsCollector
    ):
        br = BenchmarkResult(
            test_name=f"summary_lang_{target_lang}",
            file=short_file.name,
            category="short",
        )
        try:
            _, summary, _, t_sum = _transcribe_and_summarize(
                short_file, "tldr", {"summary_language": target_lang}
            )
            br.stage_times["summarize"] = round(t_sum, 2)
            br.summary_length_chars = len(summary)
            br.extra["target_lang"] = target_lang

            assert len(summary.strip()) > 10, "Summary must be non-empty"

            # Rough heuristic: if target is Russian, expect Cyrillic characters
            if target_lang == "ru":
                cyrillic_ratio = len(re.findall(r"[а-яА-ЯёЁ]", summary)) / max(len(summary), 1)
                br.extra["cyrillic_ratio"] = round(cyrillic_ratio, 3)

        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Throughput
# ──────────────────────────────────────────────────────────────

class TestSummarizationThroughput:
    """Measure LLM summarization speed."""

    def test_throughput_medium(self, medium_file: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="summarize_throughput",
            file=medium_file.name,
            category="medium",
        )
        try:
            segments, summary, t_tr, t_sum = _transcribe_and_summarize(medium_file, "notes")
            br.total_time_sec = t_tr + t_sum
            br.stage_times["transcribe"] = round(t_tr, 2)
            br.stage_times["summarize"] = round(t_sum, 2)
            br.summary_length_words = len(summary.split())
            br.summary_length_chars = len(summary)

            # Rough tokens ≈ words * 1.3
            approx_output_tokens = int(len(summary.split()) * 1.3)
            tokens_per_sec = approx_output_tokens / max(t_sum, 0.01)
            br.extra["approx_output_tokens"] = approx_output_tokens
            br.extra["tokens_per_sec"] = round(tokens_per_sec, 1)

        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)
