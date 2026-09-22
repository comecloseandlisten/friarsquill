"""Tests for extract_audio cooperative cancellation and progress helpers."""
import io
import shutil
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audio import (
    _parse_last_out_time,
    _terminate_ffmpeg_process,
    extract_audio,
)


class _FakeProc:
    """Minimal subprocess-like object for extract_audio wait loop."""

    def __init__(self):
        self.terminated = False
        self.pid = 4242
        self.stderr = io.StringIO("")

    def poll(self):
        if self.terminated:
            return 0
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_terminate_ffmpeg_process_noop_when_exited():
    p = MagicMock()
    p.poll.return_value = 0
    _terminate_ffmpeg_process(p)
    p.terminate.assert_not_called()


def test_parse_last_out_time_us_and_ms():
    content = "progress=continue\nout_time_us=5000000\n"
    assert _parse_last_out_time(content) == pytest.approx(5.0)
    content2 = "out_time_ms=3000\n"
    assert _parse_last_out_time(content2) == pytest.approx(3.0)
    # Microseconds mis-labeled as out_time_ms (large raw int)
    assert _parse_last_out_time("out_time_ms=5000000\n") == pytest.approx(5.0)


def test_parse_last_out_time_skips_na():
    assert _parse_last_out_time("out_time_us=N/A\n") is None


def test_extract_audio_cancel_terminates_ffmpeg(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    inp = tmp_path / "in.mp4"
    inp.write_bytes(b"dummy")
    out = tmp_path / "audio.wav"
    out.touch()

    cancel = threading.Event()
    fake = _FakeProc()
    sleeps = {"n": 0}

    def fake_sleep(_dur):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            cancel.set()

    def fake_popen(*_a, **_k):
        return fake

    with patch("audio.subprocess.Popen", side_effect=fake_popen), patch("audio.time.sleep", fake_sleep):
        with pytest.raises(RuntimeError, match="Cancelled"):
            extract_audio(
                inp,
                out,
                duration_sec=120.0,
                stall_timeout_sec=3600,
                cancel_event=cancel,
            )

    assert fake.terminated is True


def test_extract_audio_cancel_before_popen_raises(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    inp = tmp_path / "in.mp4"
    inp.write_bytes(b"dummy")
    out = tmp_path / "audio.wav"
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(RuntimeError, match="Cancelled"):
        extract_audio(
            inp,
            out,
            use_progress_pipe=False,
            total_timeout_sec=60,
            cancel_event=cancel,
        )
