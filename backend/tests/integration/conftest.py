"""Integration test fixtures: data discovery, metrics collection, pipeline helpers."""
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TEST_DATA_DIR = _PROJECT_ROOT / "test_data"
_REPORTS_DIR = Path(__file__).resolve().parent / "reports"
_AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg",
    ".opus", ".mkv", ".flac", ".aac", ".wma", ".avi", ".mov",
}

# ---------------------------------------------------------------------------
# Data discovery helpers
# ---------------------------------------------------------------------------

def _discover_files(category: str) -> list[Path]:
    """Return sorted list of media files in test_data/<category>/."""
    folder = _TEST_DATA_DIR / category
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS
    )


def _reference_transcript(media_path: Path) -> str | None:
    """Load reference transcript for a media file, if it exists."""
    ref = _TEST_DATA_DIR / "reference_transcripts" / f"{media_path.stem}.txt"
    if ref.exists():
        return ref.read_text(encoding="utf-8").strip()
    return None


# ---------------------------------------------------------------------------
# Metrics dataclass
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    test_name: str
    file: str
    category: str
    file_size_mb: float = 0.0
    duration_sec: float | None = None
    stage_times: dict = field(default_factory=dict)
    total_time_sec: float = 0.0
    segments_count: int = 0
    word_count: int = 0
    wer: float | None = None
    cer: float | None = None
    summary_length_chars: int = 0
    summary_length_words: int = 0
    peak_ram_mb: float | None = None
    peak_vram_mb: float | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)


class MetricsCollector:
    """Accumulates BenchmarkResult entries across the entire test session."""

    def __init__(self):
        self.results: list[BenchmarkResult] = []

    def add(self, result: BenchmarkResult):
        self.results.append(result)

    def to_dict(self) -> list[dict]:
        out = []
        for r in self.results:
            d = {
                "test_name": r.test_name,
                "file": r.file,
                "category": r.category,
                "file_size_mb": r.file_size_mb,
                "duration_sec": r.duration_sec,
                "stage_times": r.stage_times,
                "total_time_sec": round(r.total_time_sec, 3),
                "segments_count": r.segments_count,
                "word_count": r.word_count,
                "wer": r.wer,
                "cer": r.cer,
                "summary_length_chars": r.summary_length_chars,
                "summary_length_words": r.summary_length_words,
                "peak_ram_mb": r.peak_ram_mb,
                "peak_vram_mb": r.peak_vram_mb,
                "error": r.error,
                "extra": r.extra,
            }
            out.append(d)
        return out

    def write_json(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def write_markdown(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Integration Benchmark Report\n"]
        lines.append(f"**Total tests:** {len(self.results)}  ")
        passed = sum(1 for r in self.results if r.error is None)
        failed = len(self.results) - passed
        lines.append(f"**Passed:** {passed}  **Failed:** {failed}\n")
        lines.append("---\n")

        # Summary table
        lines.append("| Test | File | Category | Duration | Total Time | Segments | Words | WER | Error |")
        lines.append("|------|------|----------|----------|------------|----------|-------|-----|-------|")
        for r in self.results:
            dur = f"{r.duration_sec:.1f}s" if r.duration_sec else "?"
            wer = f"{r.wer:.2%}" if r.wer is not None else "-"
            err = r.error[:40] if r.error else "OK"
            lines.append(
                f"| {r.test_name} | {r.file} | {r.category} | {dur} "
                f"| {r.total_time_sec:.1f}s | {r.segments_count} "
                f"| {r.word_count} | {wer} | {err} |"
            )

        lines.append("\n---\n")

        # Stage timing breakdown
        lines.append("## Stage Timings\n")
        lines.append("| Test | File | download | extract | diarize | transcribe | summarize | format |")
        lines.append("|------|------|----------|---------|---------|------------|-----------|--------|")
        for r in self.results:
            st = r.stage_times
            lines.append(
                f"| {r.test_name} | {r.file} "
                f"| {st.get('download', 0):.1f}s "
                f"| {st.get('extract', 0):.1f}s "
                f"| {st.get('diarize', 0):.1f}s "
                f"| {st.get('transcribe', 0):.1f}s "
                f"| {st.get('summarize', 0):.1f}s "
                f"| {st.get('format', 0):.1f}s |"
            )

        lines.append("\n---\n")

        # Quality metrics for files with references
        has_wer = [r for r in self.results if r.wer is not None]
        if has_wer:
            lines.append("## Quality Metrics (WER)\n")
            lines.append("| File | WER | CER | Words (ref) | Words (hyp) |")
            lines.append("|------|-----|-----|-------------|-------------|")
            for r in has_wer:
                cer = f"{r.cer:.2%}" if r.cer is not None else "-"
                lines.append(
                    f"| {r.file} | {r.wer:.2%} | {cer} "
                    f"| {r.extra.get('ref_words', '?')} | {r.word_count} |"
                )

        path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def metrics_collector() -> MetricsCollector:
    return MetricsCollector()


@pytest.fixture(scope="session", autouse=True)
def _write_reports(metrics_collector: MetricsCollector):
    """After all tests finish, persist metrics to reports/."""
    yield
    if metrics_collector.results:
        ts = time.strftime("%Y%m%d_%H%M%S")
        metrics_collector.write_json(_REPORTS_DIR / f"benchmark_{ts}.json")
        metrics_collector.write_markdown(_REPORTS_DIR / f"benchmark_{ts}.md")
        # Also write a "latest" symlink-equivalent
        metrics_collector.write_json(_REPORTS_DIR / "benchmark_latest.json")
        metrics_collector.write_markdown(_REPORTS_DIR / "benchmark_latest.md")


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    return _TEST_DATA_DIR


# ---------------------------------------------------------------------------
# Per-category parametrize helpers
# ---------------------------------------------------------------------------

def _file_ids(files: list[Path]) -> list[str]:
    return [f.name for f in files]


def pytest_generate_tests(metafunc):
    """Auto-parametrize fixtures named 'short_file', 'medium_file', etc."""
    mapping = {
        "short_file": "short",
        "medium_file": "medium",
        "long_file": "long",
        "multilingual_file": "multilingual",
        "noisy_file": "noisy",
        "multi_speaker_file": "multi_speaker",
        "edge_case_file": "edge_cases",
        "format_file": "formats",
    }
    for fixture_name, category in mapping.items():
        if fixture_name in metafunc.fixturenames:
            files = _discover_files(category)
            if not files:
                metafunc.parametrize(fixture_name, [pytest.param(None, marks=pytest.mark.skip(reason=f"No test data in test_data/{category}/"))], ids=["NO_DATA"])
            else:
                metafunc.parametrize(fixture_name, files, ids=_file_ids(files))


# ---------------------------------------------------------------------------
# Shared pipeline helper
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def output_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("integration_output")


def run_pipeline_on_file(
    file_path: Path,
    config_overrides: dict | None = None,
    output_dir: Path | None = None,
) -> dict:
    """
    Run the full pipeline on a local file and return its result dict.
    Imports are deferred so tests can be collected even without heavy deps.
    """
    import sys
    backend_dir = str(Path(__file__).resolve().parents[2])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from config import Config
    from pipeline import Pipeline

    cfg = config_overrides or {}
    if output_dir:
        cfg.setdefault("output_dir", str(output_dir))

    messages: list[dict] = []
    pipeline = Pipeline(lambda msg: messages.append(msg))
    result = pipeline.run(1, {"source": str(file_path), "config": cfg})
    result["_messages"] = messages
    return result


def compute_wer(reference: str, hypothesis: str) -> tuple[float, float]:
    """
    Compute Word Error Rate and Character Error Rate.
    Returns (wer, cer) as floats in [0, 1+].
    """
    ref_words = reference.lower().split()
    hyp_words = hypothesis.lower().split()
    wer = _edit_distance(ref_words, hyp_words) / max(len(ref_words), 1)

    ref_chars = list(reference.lower().replace(" ", ""))
    hyp_chars = list(hypothesis.lower().replace(" ", ""))
    cer = _edit_distance(ref_chars, hyp_chars) / max(len(ref_chars), 1)
    return wer, cer


def _edit_distance(a: list, b: list) -> int:
    """Classic Levenshtein via DP."""
    n, m = len(a), len(b)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[m]
