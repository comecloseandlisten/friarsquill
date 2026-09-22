import sys
import time
from pathlib import Path


def _log(msg):
    print(f"[downloader] {msg}", file=sys.stderr, flush=True)


def _extract_media_title(info) -> str | None:
    """Best-effort title from yt-dlp info dict (handles some playlist shapes)."""
    if not isinstance(info, dict):
        return None
    if info.get("_type") == "playlist":
        entries = info.get("entries") or ()
        if entries and isinstance(entries[0], dict):
            nested = _extract_media_title(entries[0])
            if nested:
                return nested
    for key in ("title", "fulltitle", "track"):
        raw = info.get(key)
        if isinstance(raw, str):
            cleaned = " ".join(raw.split()).strip()
            if cleaned:
                return cleaned
    return None


def download_audio(url: str, output_dir: Path, progress_cb=None) -> tuple[Path, str | None]:
    import yt_dlp

    output_path = output_dir / "downloaded_audio"
    _log(f"download_audio: url={url[:120]}, output_dir={output_dir}")
    dl_start = time.monotonic()
    last_hook_status = [None]

    def hook(d):
        status = d["status"]
        if status != last_hook_status[0]:
            _log(f"  yt-dlp hook: status={status}")
            last_hook_status[0] = status

        if status == "downloading" and progress_cb:
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed")
            if total > 0:
                pct = int(downloaded / total * 100)
                progress_cb(pct)
                if pct % 20 == 0:
                    speed_str = f"{speed/1024/1024:.1f} MB/s" if speed else "?"
                    _log(f"  downloading: {pct}%, {downloaded/(1024*1024):.1f}/{total/(1024*1024):.1f} MB, speed={speed_str}")
            else:
                _log(f"  downloading: total_bytes unknown, downloaded={downloaded}")
        elif status == "finished":
            elapsed = time.monotonic() - dl_start
            filename = d.get("filename", "?")
            _log(f"  download finished in {elapsed:.1f}s, file={filename}")
            if progress_cb:
                progress_cb(100)
        elif status == "error":
            _log(f"  yt-dlp error in hook: {d}")

    def pp_hook(d):
        status = d.get("status", "?")
        postprocessor = d.get("postprocessor", "?")
        _log(f"  postprocessor hook: status={status}, pp={postprocessor}")

    # yt-dlp downloads best audio, then FFmpegExtractAudio converts to WAV.
    # Postprocessor args resample to 16kHz mono — exactly what Whisper needs,
    # so no separate extract_audio step is required in the pipeline.
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_path),
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "concurrent_fragment_downloads": 1,
        "logger": type("_", (), {"debug": lambda *a: None, "info": lambda *a: None, "warning": lambda *a: None, "error": lambda s, m: print(m, file=__import__('sys').stderr)})(),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        ],
        "postprocessor_args": {
            "extractaudio": ["-ar", "16000", "-ac", "1"],
        },
    }

    media_title: str | None = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            media_title = _extract_media_title(info)
            if media_title:
                _log(f"  media title: {media_title[:120]}")
    except Exception as e:
        _log(f"  yt-dlp FAILED: {e}")
        raise RuntimeError(f"Audio fetch failed: {e}") from e

    total_elapsed = time.monotonic() - dl_start
    _log(f"  yt-dlp total elapsed: {total_elapsed:.1f}s (includes postprocessors)")

    for ext in [".wav", ".m4a", ".webm", ".opus", ".ogg", ".mp3", ".mp4", ".mkv", ".aac"]:
        candidate = output_path.with_suffix(ext)
        if candidate.exists():
            try:
                size_mb = candidate.stat().st_size / (1024 * 1024)
                _log(f"  result: {candidate.name}, size={size_mb:.1f} MB")
            except Exception:
                pass
            return candidate, media_title

    files = list(output_dir.glob("downloaded_audio*"))
    if files:
        _log(f"  fallback result: {files[0]}, total candidates={len(files)}")
        return files[0], media_title

    raise FileNotFoundError("Download failed: no audio file produced")
