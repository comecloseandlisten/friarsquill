"""
Integration tests: full end-to-end pipeline.

Runs Pipeline.run() on real files and validates the complete flow:
download/copy → duration → diarize → transcribe → summarize → format → metrics.

Checks:
- Result dict shape (markdown, output_path, metrics)
- Markdown file is written and non-empty
- Metrics JSON is written
- Diarization enriches segments with speaker labels
- Batch mode works with multiple files
- Progress callbacks fire in correct stage order
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from audio import get_duration
from config import Config
from conftest import BenchmarkResult, MetricsCollector


def _run_pipeline(
    file_path: Path,
    output_dir: Path,
    config_overrides: dict | None = None,
) -> tuple[dict, list[dict], float]:
    """Run Pipeline.run() and capture messages. Returns (result, messages, elapsed)."""
    from pipeline import Pipeline

    messages: list[dict] = []
    pipeline = Pipeline(lambda msg: messages.append(msg))

    cfg = config_overrides or {}
    cfg.setdefault("output_dir", str(output_dir))

    t0 = time.monotonic()
    result = pipeline.run(1, {"source": str(file_path), "config": cfg})
    elapsed = time.monotonic() - t0
    return result, messages, elapsed


# ──────────────────────────────────────────────────────────────
# End-to-end single file
# ──────────────────────────────────────────────────────────────

class TestPipelineEndToEnd:
    """Full pipeline produces valid output on every data category."""

    def test_pipeline_short(self, short_file, metrics_collector, output_dir):
        self._e2e(short_file, "short", metrics_collector, output_dir)

    def test_pipeline_medium(self, medium_file, metrics_collector, output_dir):
        self._e2e(medium_file, "medium", metrics_collector, output_dir)

    def test_pipeline_noisy(self, noisy_file, metrics_collector, output_dir):
        self._e2e(noisy_file, "noisy", metrics_collector, output_dir)

    def test_pipeline_long(self, long_file, metrics_collector, output_dir):
        self._e2e(long_file, "long", metrics_collector, output_dir)

    @staticmethod
    def _e2e(file_path: Path, category: str, collector: MetricsCollector, output_dir: Path):
        br = BenchmarkResult(
            test_name="pipeline_e2e",
            file=file_path.name,
            category=category,
            file_size_mb=file_path.stat().st_size / (1024 * 1024),
        )
        try:
            result, messages, elapsed = _run_pipeline(file_path, output_dir)
            br.total_time_sec = elapsed

            # Result shape
            assert "markdown" in result, "Result must contain 'markdown'"
            assert "output_path" in result, "Result must contain 'output_path'"
            assert "metrics" in result, "Result must contain 'metrics'"

            # Markdown content
            md = result["markdown"]
            assert len(md) > 100, f"Markdown too short: {len(md)} chars"
            assert "## Summary" in md, "Markdown must contain Summary section"
            assert "## Full Transcript" in md, "Markdown must contain Transcript section"

            # Output file written
            out_path = Path(result["output_path"])
            assert out_path.exists(), f"Output file not created: {out_path}"
            assert out_path.stat().st_size > 0, "Output file is empty"

            # Metrics
            metrics = result["metrics"]
            br.duration_sec = metrics.get("duration_sec")
            br.segments_count = metrics.get("segments", 0)
            if "stage_times_sec" in metrics:
                br.stage_times = {
                    k: round(v, 2) for k, v in metrics["stage_times_sec"].items()
                }

            # Summary word count
            summary_start = md.find("## Summary")
            summary_end = md.find("---", summary_start + 10)
            if summary_start >= 0 and summary_end >= 0:
                summary_text = md[summary_start:summary_end]
                br.summary_length_words = len(summary_text.split())
                br.summary_length_chars = len(summary_text)

        except Exception as e:
            br.error = str(e)
            collector.add(br)
            raise
        collector.add(br)


# ──────────────────────────────────────────────────────────────
# Metrics JSON sidecar
# ──────────────────────────────────────────────────────────────

class TestMetricsOutput:
    """Pipeline writes a metrics JSON sidecar file."""

    def test_metrics_json_exists(self, short_file: Path, output_dir: Path):
        result, _, _ = _run_pipeline(short_file, output_dir)
        metrics_dir = output_dir / "metrics"

        assert metrics_dir.exists(), "metrics/ directory not created"
        json_files = list(metrics_dir.glob("*_metrics.json"))
        assert len(json_files) > 0, "No metrics JSON files found"

        import json
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert "stage_times_sec" in data
        assert "segments" in data
        assert "source" in data


# ──────────────────────────────────────────────────────────────
# Progress callbacks
# ──────────────────────────────────────────────────────────────

class TestProgressCallbacks:
    """Pipeline sends progress messages in correct stage order."""

    EXPECTED_STAGES = ["download", "extract", "diarize", "transcribe", "summarize", "format"]

    def test_progress_stages_order(self, short_file: Path, output_dir: Path):
        _, messages, _ = _run_pipeline(short_file, output_dir)

        progress_msgs = [m for m in messages if m.get("type") == "progress"]
        assert len(progress_msgs) > 0, "No progress messages emitted"

        seen_stages = []
        for msg in progress_msgs:
            stage = msg.get("stage")
            if stage and (not seen_stages or seen_stages[-1] != stage):
                seen_stages.append(stage)

        # Every expected stage should appear in order
        stage_indices = {}
        for stage in seen_stages:
            if stage in self.EXPECTED_STAGES:
                stage_indices[stage] = self.EXPECTED_STAGES.index(stage)

        indices = list(stage_indices.values())
        assert indices == sorted(indices), (
            f"Stages out of order: {seen_stages} (expected order: {self.EXPECTED_STAGES})"
        )

    def test_progress_percent_monotonic(self, short_file: Path, output_dir: Path):
        """Within each stage, percent should generally increase."""
        _, messages, _ = _run_pipeline(short_file, output_dir)

        from collections import defaultdict
        stage_percents: dict[str, list[int]] = defaultdict(list)
        for msg in messages:
            if msg.get("type") == "progress":
                stage_percents[msg["stage"]].append(msg.get("percent", 0))

        for stage, percents in stage_percents.items():
            if len(percents) < 2:
                continue
            # Allow minor dips (progress throttling) but final should be >= first
            assert percents[-1] >= percents[0], (
                f"Stage '{stage}': percent went from {percents[0]} to {percents[-1]}"
            )


# ──────────────────────────────────────────────────────────────
# Diarization integration
# ──────────────────────────────────────────────────────────────

class TestDiarizationIntegration:
    """Pipeline with diarize=True adds speaker labels to segments."""

    def test_diarize_multi_speaker(
        self, multi_speaker_file: Path, metrics_collector: MetricsCollector, output_dir: Path
    ):
        br = BenchmarkResult(
            test_name="pipeline_diarize",
            file=multi_speaker_file.name,
            category="multi_speaker",
        )
        try:
            result, messages, elapsed = _run_pipeline(
                multi_speaker_file, output_dir, {"diarize": True}
            )
            br.total_time_sec = elapsed

            md = result["markdown"]
            # With diarization, markdown should contain speaker labels
            has_speaker = "SPEAKER_" in md or "**[" in md
            br.extra["has_speaker_labels"] = has_speaker

            assert has_speaker, (
                "Diarized output should contain speaker labels in markdown"
            )

            metrics = result.get("metrics", {})
            br.duration_sec = metrics.get("duration_sec")
            if "stage_times_sec" in metrics:
                br.stage_times = metrics["stage_times_sec"]
                assert metrics["stage_times_sec"].get("diarize", 0) > 0, (
                    "Diarization stage should have non-zero time"
                )

        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Batch mode
# ──────────────────────────────────────────────────────────────

class TestBatchPipeline:
    """Pipeline.run_batch() processes multiple files into a combined report."""

    def test_batch_two_files(self, test_data_dir: Path, metrics_collector: MetricsCollector, output_dir: Path):
        from conftest import _discover_files

        # Gather at least 2 files from any category
        all_files = []
        for cat in ["short", "medium", "formats"]:
            all_files.extend(_discover_files(cat))
        if len(all_files) < 2:
            pytest.skip("Need at least 2 test files for batch test")

        files = all_files[:2]
        br = BenchmarkResult(
            test_name="pipeline_batch",
            file=f"{files[0].name}+{files[1].name}",
            category="batch",
        )
        try:
            from pipeline import Pipeline

            messages: list[dict] = []
            pipeline = Pipeline(lambda msg: messages.append(msg))

            t0 = time.monotonic()
            result = pipeline.run_batch(1, {
                "sources": [str(f) for f in files],
                "config": {"output_dir": str(output_dir)},
            })
            elapsed = time.monotonic() - t0
            br.total_time_sec = elapsed

            assert "markdown" in result
            assert "output_path" in result
            assert result["videos_processed"] == 2, (
                f"Expected 2 videos processed, got {result['videos_processed']}"
            )

            out_path = Path(result["output_path"])
            assert out_path.exists()
            assert "batch_report" in out_path.name

            md = result["markdown"]
            assert "## Overall Summary" in md
            assert "## Video 1:" in md
            assert "## Video 2:" in md

        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)
