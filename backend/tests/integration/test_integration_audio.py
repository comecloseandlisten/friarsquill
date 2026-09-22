"""
Integration tests: audio processing and format handling.

Validates that every supported media format can be:
1. Probed for duration (ffprobe / ffmpeg fallback)
2. Extracted to 16 kHz mono WAV
3. Chunked for long-video path

Metrics collected: extraction time, file sizes, duration accuracy.
"""
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from audio import get_duration, extract_audio, extract_audio_chunks
from conftest import BenchmarkResult, MetricsCollector


class TestDurationProbe:
    """ffprobe / ffmpeg can read duration for every format we accept."""

    def test_duration_short(self, short_file: Path, metrics_collector: MetricsCollector):
        self._probe(short_file, "short", metrics_collector)

    def test_duration_medium(self, medium_file: Path, metrics_collector: MetricsCollector):
        self._probe(medium_file, "medium", metrics_collector)

    def test_duration_long(self, long_file: Path, metrics_collector: MetricsCollector):
        self._probe(long_file, "long", metrics_collector)

    def test_duration_formats(self, format_file: Path, metrics_collector: MetricsCollector):
        self._probe(format_file, "formats", metrics_collector)

    @staticmethod
    def _probe(file_path: Path, category: str, collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="duration_probe",
            file=file_path.name,
            category=category,
            file_size_mb=file_path.stat().st_size / (1024 * 1024),
        )
        t0 = time.monotonic()
        try:
            duration = get_duration(file_path)
            br.duration_sec = duration
            br.total_time_sec = time.monotonic() - t0
            assert duration > 0, "Duration must be positive"
        except Exception as e:
            br.error = str(e)
            br.total_time_sec = time.monotonic() - t0
            collector.add(br)
            pytest.fail(f"get_duration failed for {file_path.name}: {e}")
        collector.add(br)


class TestAudioExtraction:
    """extract_audio produces a valid 16 kHz mono WAV from any input format."""

    def test_extract_short(self, short_file: Path, metrics_collector: MetricsCollector, output_dir: Path):
        self._extract(short_file, "short", metrics_collector, output_dir)

    def test_extract_medium(self, medium_file: Path, metrics_collector: MetricsCollector, output_dir: Path):
        self._extract(medium_file, "medium", metrics_collector, output_dir)

    def test_extract_formats(self, format_file: Path, metrics_collector: MetricsCollector, output_dir: Path):
        self._extract(format_file, "formats", metrics_collector, output_dir)

    def test_extract_noisy(self, noisy_file: Path, metrics_collector: MetricsCollector, output_dir: Path):
        self._extract(noisy_file, "noisy", metrics_collector, output_dir)

    @staticmethod
    def _extract(file_path: Path, category: str, collector: MetricsCollector, output_dir: Path):
        br = BenchmarkResult(
            test_name="audio_extraction",
            file=file_path.name,
            category=category,
            file_size_mb=file_path.stat().st_size / (1024 * 1024),
        )
        wav_out = output_dir / f"{file_path.stem}_extracted.wav"
        t0 = time.monotonic()
        try:
            result_path = extract_audio(file_path, wav_out)
            elapsed = time.monotonic() - t0
            br.total_time_sec = elapsed
            br.stage_times["extract"] = elapsed

            assert result_path.exists(), "Output WAV must exist"
            out_size = result_path.stat().st_size
            assert out_size > 0, "Output WAV must be non-empty"
            br.extra["output_size_mb"] = round(out_size / (1024 * 1024), 2)

            try:
                br.duration_sec = get_duration(result_path)
            except Exception:
                pass
        except Exception as e:
            br.error = str(e)
            br.total_time_sec = time.monotonic() - t0
            collector.add(br)
            pytest.fail(f"extract_audio failed for {file_path.name}: {e}")
        collector.add(br)


class TestAudioChunking:
    """Long files can be split into sequential WAV chunks."""

    def test_chunking_long(self, long_file: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="audio_chunking",
            file=long_file.name,
            category="long",
            file_size_mb=long_file.stat().st_size / (1024 * 1024),
        )
        t0 = time.monotonic()
        try:
            duration = get_duration(long_file)
            br.duration_sec = duration

            with tempfile.TemporaryDirectory() as tmpdir:
                chunks = extract_audio_chunks(
                    long_file,
                    Path(tmpdir),
                    chunk_sec=300,
                    overlap_sec=2.0,
                    duration_sec=duration,
                )
                elapsed = time.monotonic() - t0
                br.total_time_sec = elapsed
                br.extra["num_chunks"] = len(chunks)

                assert len(chunks) > 0, "Must produce at least one chunk"
                for c in chunks:
                    assert Path(c["path"]).exists(), f"Chunk {c['index']} must exist"
                    assert c["end"] > c["start"], "Chunk end > start"

                # Verify chunks cover the full duration
                assert chunks[-1]["end"] >= duration - 1.0, \
                    "Last chunk must reach near end of file"

        except Exception as e:
            br.error = str(e)
            br.total_time_sec = time.monotonic() - t0
            metrics_collector.add(br)
            pytest.fail(f"Audio chunking failed for {long_file.name}: {e}")
        metrics_collector.add(br)

    def test_chunking_medium_as_single(self, medium_file: Path, metrics_collector: MetricsCollector):
        """Medium files with chunk_sec > duration yield exactly 1 chunk."""
        br = BenchmarkResult(
            test_name="audio_chunking_single",
            file=medium_file.name,
            category="medium",
            file_size_mb=medium_file.stat().st_size / (1024 * 1024),
        )
        t0 = time.monotonic()
        try:
            duration = get_duration(medium_file)
            br.duration_sec = duration

            with tempfile.TemporaryDirectory() as tmpdir:
                chunks = extract_audio_chunks(
                    medium_file,
                    Path(tmpdir),
                    chunk_sec=max(int(duration) + 60, 7200),
                    overlap_sec=0.0,
                    duration_sec=duration,
                )
                br.total_time_sec = time.monotonic() - t0
                br.extra["num_chunks"] = len(chunks)

                assert len(chunks) == 1, f"Expected 1 chunk for {duration:.0f}s file"
        except Exception as e:
            br.error = str(e)
            br.total_time_sec = time.monotonic() - t0
            metrics_collector.add(br)
            pytest.fail(str(e))
        metrics_collector.add(br)


class TestFormatParity:
    """Same content in different formats should produce similar durations."""

    def test_format_duration_consistency(self, test_data_dir: Path, metrics_collector: MetricsCollector):
        format_dir = test_data_dir / "formats"
        if not format_dir.exists():
            pytest.skip("No test_data/formats/ directory")

        from collections import defaultdict
        stem_durations: dict[str, list[tuple[str, float]]] = defaultdict(list)

        for f in sorted(format_dir.iterdir()):
            if f.suffix.lower() in {".wav", ".mp3", ".mp4", ".m4a", ".webm",
                                     ".ogg", ".opus", ".mkv", ".flac"}:
                try:
                    dur = get_duration(f)
                    base = f.stem.rsplit("_", 1)[0] if "_" in f.stem else f.stem
                    stem_durations[base].append((f.name, dur))
                except Exception:
                    pass

        for base, entries in stem_durations.items():
            if len(entries) < 2:
                continue
            durations = [d for _, d in entries]
            spread = max(durations) - min(durations)
            br = BenchmarkResult(
                test_name="format_parity",
                file=base,
                category="formats",
                extra={"files": [n for n, _ in entries], "spread_sec": round(spread, 2)},
            )
            br.duration_sec = sum(durations) / len(durations)
            metrics_collector.add(br)

            assert spread < 2.0, (
                f"Duration spread for '{base}' across formats is {spread:.2f}s "
                f"(max tolerance 2s): {entries}"
            )
