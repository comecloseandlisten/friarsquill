import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


def _log(msg):
    print(f"[audio] {msg}", file=sys.stderr, flush=True)


# ffmpeg extract_audio / extract_audio_slice output (Whisper input)
WHISPER_EXTRACT_AUDIO_FORMAT = "WAV (RIFF), PCM s16le, mono, 16000 Hz"


def log_written_extracted_wav(path: Path, *, label: str = "Extracted") -> None:
    """Log absolute path, size in bytes and MiB, and fixed PCM WAV format."""
    try:
        resolved = path.resolve()
        nbytes = path.stat().st_size
        mib = nbytes / (1024 * 1024)
        _log(
            f"{label}: file={resolved} | size={nbytes} bytes ({mib:.2f} MiB) | format={WHISPER_EXTRACT_AUDIO_FORMAT}"
        )
    except OSError as e:
        _log(f"{label}: could not stat output ({path}): {e}")


def _parse_progress_time_value(val: str) -> float | None:
    """Parse a single time value from ffmpeg -progress (skip N/A and blanks)."""
    s = val.strip()
    if not s or s.upper() == "N/A":
        return None
    try:
        return int(s) / 1_000_000.0
    except ValueError:
        return None


def _parse_last_out_time(content: str) -> float | None:
    """Parse the last out_time value from ffmpeg progress file content."""
    last_time = None
    for line in content.replace("\r\n", "\n").split("\n"):
        entry = line.strip()
        if not entry or "=" not in entry:
            continue
        key, _, rest = entry.partition("=")
        key = key.strip()
        rest = rest.strip()
        if key == "out_time_us":
            t = _parse_progress_time_value(rest)
            if t is not None:
                last_time = t
        elif key == "out_time_ms":
            # Some builds emit microseconds under this key; disambiguate vs true milliseconds.
            if rest.upper() == "N/A":
                continue
            try:
                n = int(rest)
            except ValueError:
                continue
            as_ms = n / 1000.0
            as_us = n / 1_000_000.0
            if as_ms > 3600.0 and as_us <= 3600.0:
                cand = as_us
            elif as_ms <= 86400.0:
                cand = as_ms
            else:
                cand = as_us
            if cand >= 0:
                last_time = cand
        elif key == "out_time":
            try:
                hh, mm, ss = rest.split(":")
                last_time = int(hh) * 3600 + int(mm) * 60 + float(ss)
            except (ValueError, IndexError):
                pass
    return last_time


def _expected_pcm_wav_data_bytes(duration_sec: float) -> float:
    """Bytes of PCM16 mono @16kHz (excluding a small WAV header)."""
    return max(duration_sec, 0.1) * 16000 * 2


def _extract_pct_from_wav_growth(file_size: int, duration_sec: float | None) -> int | None:
    """Rough extract percent from output file size vs expected PCM payload (+header slack)."""
    if duration_sec is None or duration_sec <= 0:
        return None
    expected_data = _expected_pcm_wav_data_bytes(duration_sec)
    # Header + mux slack; avoid div-by-zero
    denom = expected_data + 8192
    raw = (max(0, file_size) / denom) * 100
    if raw <= 0:
        return None
    return int(max(0, min(99, math.ceil(raw))))


def _terminate_ffmpeg_process(proc: subprocess.Popen) -> None:
    """Stop ffmpeg subprocess (terminate, then kill if needed)."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=8)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def get_duration(input_path: Path) -> float:
    """
    Get audio duration in seconds using ffprobe.

    Args:
        input_path: Path to media file

    Returns:
        Duration in seconds

    Raises:
        RuntimeError: If ffprobe fails or duration cannot be determined
    """
    _log(f"get_duration: {input_path}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # Fallback: try ffmpeg with stderr parsing
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "ffprobe/ffmpeg not found. Please install ffmpeg and ensure it is on your PATH.\n"
                "  Windows: winget install ffmpeg\n"
                "  macOS:   brew install ffmpeg\n"
                "  Linux:   sudo apt install ffmpeg"
            )
        return _get_duration_from_ffmpeg(input_path, ffmpeg)

    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(input_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"[audio] ffprobe error: {result.stderr[:200]}", file=sys.stderr, flush=True)
            raise RuntimeError(f"ffprobe failed: {result.stderr[:500]}")

        data = json.loads(result.stdout)
        duration = data.get("format", {}).get("duration")
        if duration is None:
            _log(f"  ffprobe returned no duration, raw output: {result.stdout[:300]}")
            raise RuntimeError("No duration found in ffprobe output")

        dur_val = float(duration)
        _log(f"  duration={dur_val:.2f}s ({dur_val/60:.1f}min)")
        return dur_val
    except json.JSONDecodeError as e:
        print(f"[audio] Failed to parse ffprobe output: {e}", file=sys.stderr, flush=True)
        raise RuntimeError(f"ffprobe output parsing failed: {e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe timeout")


def _get_duration_from_ffmpeg(input_path: Path, ffmpeg: str) -> float:
    """Fallback: extract duration from ffmpeg stderr."""
    cmd = [ffmpeg, "-i", str(input_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    # Look for "Duration: HH:MM:SS.ms" in stderr
    stderr = result.stderr
    for line in stderr.split("\n"):
        if "Duration:" in line:
            # Example: "Duration: 00:05:30.50, start: 0.000000, bitrate: 128 kb/s"
            parts = line.split("Duration:")[1].split(",")[0].strip()
            try:
                h, m, s = parts.split(":")
                total_sec = int(h) * 3600 + int(m) * 60 + float(s)
                return total_sec
            except (ValueError, IndexError):
                pass

    raise RuntimeError(f"Could not extract duration from ffmpeg output")


def extract_audio(
    input_path: Path,
    output_path: Path,
    progress_cb=None,
    duration_sec: float | None = None,
    stall_timeout_sec: int | None = None,
    total_timeout_sec: int | None = None,
    use_progress_pipe: bool = True,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Extract audio as 16kHz mono WAV for Whisper with optional progress callback."""
    extract_t0 = time.monotonic()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found. Please install ffmpeg and ensure it is on your PATH.\n"
            "  Windows: winget install ffmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )

    _log(f"extract_audio called: input={input_path}, output={output_path}")
    _log(f"  duration_sec={duration_sec}, use_progress_pipe={use_progress_pipe}")
    _log(f"  stall_timeout_sec={stall_timeout_sec}, total_timeout_sec={total_timeout_sec}")

    try:
        input_size_mb = input_path.stat().st_size / (1024 * 1024)
        _log(f"  input file size: {input_size_mb:.1f} MB")
    except Exception as e:
        _log(f"  could not stat input: {e}")

    if progress_cb and duration_sec is None:
        try:
            duration_sec = get_duration(input_path)
            _log(f"  resolved duration via get_duration: {duration_sec:.1f}s")
        except Exception as e:
            _log(f"  get_duration failed: {e}, duration_sec remains None")
            duration_sec = None

    # On Windows, ffmpeg's -progress pipe:N doesn't work with Python subprocess
    # pipes (fd handles don't align). Use a temp file for progress instead.
    progress_file = output_path.parent / f".ffprogress_{os.getpid()}.txt"

    cmd = [
        ffmpeg,
        "-y",
        "-v", "error",
        "-threads",
        "0",
    ]
    if use_progress_pipe:
        cmd += ["-nostats", "-progress", str(progress_file)]
    cmd += [
        "-i", str(input_path),
        "-map", "a:0?",
        "-vn",
        "-sn",
        "-dn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_path),
    ]

    _log(f"  ffmpeg command: {' '.join(cmd)}")

    if stall_timeout_sec is None:
        if duration_sec is None:
            effective_stall_timeout = 1200
        else:
            effective_stall_timeout = max(600, min(21600, int(duration_sec * 2) + 600))
    else:
        effective_stall_timeout = max(60, int(stall_timeout_sec))

    if total_timeout_sec is None:
        if duration_sec is None:
            effective_total_timeout = max(effective_stall_timeout, 7200)
        else:
            effective_total_timeout = max(
                effective_stall_timeout,
                min(86400, int(duration_sec * 4.0) + 900),
            )
    else:
        effective_total_timeout = max(60, int(total_timeout_sec))

    _log(f"  effective_stall_timeout={effective_stall_timeout}s, effective_total_timeout={effective_total_timeout}s")

    if not use_progress_pipe:
        _log("  running ffmpeg in non-progress-pipe (compat) mode")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Cancelled")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_total_timeout,
        )
        elapsed = time.monotonic() - extract_t0
        _log(f"  compat mode done: rc={result.returncode}, elapsed={elapsed:.1f}s")
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")
        if not output_path.exists():
            raise FileNotFoundError("ffmpeg produced no output")
        log_written_extracted_wav(output_path, label="extract_audio (compat)")
        if progress_cb:
            progress_cb(100)
        return output_path

    _log(f"  running ffmpeg with progress file: {progress_file}")
    stderr_tail: list[str] = []

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    _log(f"  ffmpeg PID: {proc.pid}")

    def _stderr_reader():
        if not proc.stderr:
            return
        for raw_line in proc.stderr:
            line = raw_line.rstrip("\r\n")
            if line:
                stderr_tail.append(line)
                if len(stderr_tail) > 120:
                    stderr_tail.pop(0)

    stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
    stderr_thread.start()

    last_reported = -1
    last_liveness_at = time.monotonic()
    last_size = -1
    last_log_at = time.monotonic()
    user_cancelled = False

    while proc.poll() is None:
        time.sleep(1.0)
        now = time.monotonic()

        if cancel_event is not None and cancel_event.is_set():
            _log("  cancel_event set, terminating ffmpeg")
            _terminate_ffmpeg_process(proc)
            user_cancelled = True
            break

        pct_from_progress = None
        # Parse progress from file
        if progress_cb and duration_sec and progress_file.exists():
            try:
                content = progress_file.read_text(errors="replace")
                out_time_sec = _parse_last_out_time(content)
                if out_time_sec is not None and out_time_sec > 0:
                    last_liveness_at = now
                    raw_pct = (out_time_sec / max(duration_sec, 0.1)) * 100
                    pct_from_progress = int(max(0, min(99, math.ceil(raw_pct))))
            except OSError:
                pass

        pct_from_wav = None
        # Output file growth as liveness signal + size-based progress when duration known
        if output_path.exists():
            try:
                current_size = output_path.stat().st_size
                if current_size > last_size:
                    last_size = current_size
                    last_liveness_at = now
                    if progress_cb and duration_sec is None:
                        pseudo = max(1, min(95, last_reported + 1))
                        if pseudo > last_reported:
                            progress_cb(pseudo)
                            last_reported = pseudo
                if progress_cb and duration_sec is not None and duration_sec > 0:
                    pct_from_wav = _extract_pct_from_wav_growth(current_size, duration_sec)
            except OSError:
                pass

        if progress_cb and duration_sec is not None and duration_sec > 0:
            candidates = [p for p in (pct_from_progress, pct_from_wav) if p is not None]
            if candidates:
                pct = max(candidates)
                if last_reported < 0:
                    if pct >= 0:
                        progress_cb(pct)
                        last_reported = pct
                elif pct >= last_reported + 2 or pct == 99:
                    progress_cb(pct)
                    last_reported = pct
        elif progress_cb and pct_from_progress is not None:
            pct = pct_from_progress
            if last_reported < 0:
                if pct >= 0:
                    progress_cb(pct)
                    last_reported = pct
            elif pct >= last_reported + 2 or pct == 99:
                progress_cb(pct)
                last_reported = pct

        # Heartbeat log
        if now - last_log_at >= 10.0:
            elapsed = now - extract_t0
            try:
                cur_size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0
            except OSError:
                cur_size_mb = 0
            pf_exists = progress_file.exists()
            _log(
                f"  heartbeat: elapsed={elapsed:.0f}s, last_pct={last_reported}, "
                f"output={cur_size_mb:.1f}MB, progress_file={'yes' if pf_exists else 'no'}"
            )
            last_log_at = now

        # Stall detection
        if now - last_liveness_at > effective_stall_timeout:
            _log(f"  STALL DETECTED: no liveness for {effective_stall_timeout}s, killing ffmpeg (PID {proc.pid})")
            proc.kill()
            raise RuntimeError(
                f"ffmpeg extraction stalled: no output for {effective_stall_timeout}s"
            )

    stderr_thread.join(timeout=2.0)
    return_code = proc.wait()
    total_elapsed = time.monotonic() - extract_t0

    # Cleanup progress file
    try:
        progress_file.unlink(missing_ok=True)
    except OSError:
        pass

    if user_cancelled:
        _log(f"  ffmpeg cancelled after {total_elapsed:.1f}s")
        raise RuntimeError("Cancelled")

    _log(f"  ffmpeg finished: rc={return_code}, elapsed={total_elapsed:.1f}s")

    if return_code != 0:
        stderr_text = "\n".join(stderr_tail)
        _log(f"  ffmpeg FAILED, tail:\n{stderr_text[:1000]}")
        raise RuntimeError(f"ffmpeg failed: {stderr_text[:500]}")

    if not output_path.exists():
        raise FileNotFoundError("ffmpeg produced no output")

    log_written_extracted_wav(output_path, label="extract_audio")

    if progress_cb:
        progress_cb(100)

    return output_path


def extract_audio_chunks(
    input_path: Path,
    output_dir: Path,
    chunk_sec: int,
    overlap_sec: float = 0.0,
    progress_cb=None,
    duration_sec: float | None = None,
    chunk_timeout_sec: int | None = None,
) -> list[dict]:
    """
    Extract source media to sequential WAV chunks without creating one huge WAV.

    Returns list of dicts:
      {
        "index": int,
        "path": Path,
        "start": float,
        "end": float,
      }
    """
    if chunk_sec <= 0:
        raise ValueError("chunk_sec must be positive")
    if overlap_sec < 0:
        raise ValueError("overlap_sec must be >= 0")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")

    if duration_sec is None:
        duration_sec = get_duration(input_path)
    if duration_sec <= 0:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[dict] = []
    start = 0.0
    idx = 0
    step = max(chunk_sec - overlap_sec, 1.0)
    total_chunks = max(1, int(math.ceil(duration_sec / step)))

    while start < duration_sec:
        end = min(start + chunk_sec, duration_sec)
        chunk_duration = max(end - start, 0.1)
        out_path = output_dir / f"chunk_{idx:04d}.wav"
        progress_file = output_dir / f".ffprogress_chunk_{idx:04d}.txt"
        cmd = [
            ffmpeg,
            "-y",
            "-threads",
            "0",
            "-noaccurate_seek",
            "-v",
            "error",
            "-nostats",
            "-progress",
            str(progress_file),
            "-ss",
            f"{start:.3f}",
            "-i",
            str(input_path),
            "-map",
            "a:0?",
            "-t",
            f"{chunk_duration:.3f}",
            "-vn",
            "-sn",
            "-dn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(out_path),
        ]
        effective_chunk_timeout = (
            max(60, int(chunk_timeout_sec))
            if chunk_timeout_sec is not None
            else int(min(14400, max(600, chunk_sec * 3 + 300)))
        )
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        stderr_tail: list[str] = []
        last_liveness_at = time.monotonic()
        last_size = -1
        last_chunk_pct = -1

        def _stderr_reader():
            if not proc.stderr:
                return
            for raw_line in proc.stderr:
                line = raw_line.rstrip("\r\n")
                if line:
                    stderr_tail.append(line)
                    if len(stderr_tail) > 60:
                        stderr_tail.pop(0)

        stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
        stderr_thread.start()

        while proc.poll() is None:
            time.sleep(1.0)
            now = time.monotonic()

            if progress_cb and progress_file.exists():
                try:
                    content = progress_file.read_text(errors="replace")
                    out_time_sec = _parse_last_out_time(content)
                    if out_time_sec is not None and out_time_sec > 0:
                        last_liveness_at = now
                        chunk_pct = int(max(0, min(99, math.ceil((out_time_sec / chunk_duration) * 100))))
                        if chunk_pct >= last_chunk_pct + 2 or chunk_pct == 99:
                            raw_global = ((idx + (chunk_pct / 100.0)) / total_chunks) * 100
                            global_pct = int(max(0, min(99, math.ceil(raw_global))))
                            progress_cb(global_pct)
                            last_chunk_pct = chunk_pct
                except OSError:
                    pass

            if out_path.exists():
                try:
                    current_size = out_path.stat().st_size
                    if current_size > last_size:
                        last_size = current_size
                        last_liveness_at = now
                except OSError:
                    pass

            if now - last_liveness_at > effective_chunk_timeout:
                proc.kill()
                raise RuntimeError(
                    f"ffmpeg chunk extract stalled: no output for {effective_chunk_timeout}s"
                )

        stderr_thread.join(timeout=1.0)
        return_code = proc.wait()
        try:
            progress_file.unlink(missing_ok=True)
        except OSError:
            pass

        if return_code != 0:
            stderr_text = "\n".join(stderr_tail)
            raise RuntimeError(f"ffmpeg chunk extract failed: {stderr_text[:500]}")

        if not out_path.exists():
            raise FileNotFoundError(f"Chunk not produced: {out_path}")

        chunks.append(
            {
                "index": idx,
                "path": out_path,
                "start": start,
                "end": end,
            }
        )
        idx += 1
        start += step

        if progress_cb:
            pct = int(max(0, min(100, ((idx) / total_chunks) * 100)))
            progress_cb(pct)

    return chunks


def extract_audio_slice(
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    chunk_timeout_sec: int | None = None,
) -> Path:
    """
    Extract a single time slice from source media into 16kHz mono WAV.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")

    effective_duration = max(float(duration_sec), 0.1)
    effective_timeout = (
        max(60, int(chunk_timeout_sec))
        if chunk_timeout_sec is not None
        else int(min(14400, max(600, effective_duration * 6 + 180)))
    )

    cmd = [
        ffmpeg,
        "-y",
        "-threads",
        "0",
        "-noaccurate_seek",
        "-ss",
        f"{max(float(start_sec), 0.0):.3f}",
        "-i",
        str(input_path),
        "-map",
        "a:0?",
        "-t",
        f"{effective_duration:.3f}",
        "-vn",
        "-sn",
        "-dn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=effective_timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg slice extract failed: {result.stderr[:500]}")
    if not output_path.exists():
        raise FileNotFoundError(f"Slice not produced: {output_path}")
    log_written_extracted_wav(
        output_path,
        label=f"extract_audio_slice [{start_sec:.1f}s–{start_sec + effective_duration:.1f}s]",
    )
    return output_path


def split_audio_chunks(
    wav_path: Path,
    output_dir: Path,
    chunk_sec: int,
    overlap_sec: float = 0.0,
    chunk_timeout_sec: int | None = None,
) -> list[dict]:
    """
    Split a WAV file into time-based chunks via ffmpeg.

    Returns list of dicts:
      {
        "index": int,
        "path": Path,
        "start": float,
        "end": float,
      }
    """
    if chunk_sec <= 0:
        raise ValueError("chunk_sec must be positive")
    if overlap_sec < 0:
        raise ValueError("overlap_sec must be >= 0")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")

    duration = get_duration(wav_path)
    if duration <= 0:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[dict] = []
    start = 0.0
    idx = 0
    step = max(chunk_sec - overlap_sec, 1.0)

    while start < duration:
        end = min(start + chunk_sec, duration)
        out_path = output_dir / f"chunk_{idx:04d}.wav"
        cmd = [
            ffmpeg,
            "-y",
            "-noaccurate_seek",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(wav_path),
            "-map",
            "a:0?",
            "-t",
            f"{max(end - start, 0.1):.3f}",
            "-vn",
            "-sn",
            "-dn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(out_path),
        ]
        effective_chunk_timeout = (
            max(60, int(chunk_timeout_sec))
            if chunk_timeout_sec is not None
            else int(min(14400, max(600, chunk_sec * 3 + 300)))
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_chunk_timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg split failed: {result.stderr[:500]}")
        if not out_path.exists():
            raise FileNotFoundError(f"Chunk not produced: {out_path}")

        chunks.append(
            {
                "index": idx,
                "path": out_path,
                "start": start,
                "end": end,
            }
        )
        idx += 1
        start += step

    return chunks
