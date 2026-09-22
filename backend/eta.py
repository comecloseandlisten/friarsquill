"""
ETA (Estimated Time of Arrival) module for audio/video processing.

Provides:
- Static RT factors (whisper model × device) and LLM timing defaults
- Dynamic calibration via perf_stats.json (running averages)
- ETA estimation before and during processing
- Formatted human-readable time strings
"""

import json
import math
import os
import sys
from pathlib import Path
from typing import Optional

from config import Config


# RT factors: (whisper_model, device) -> fraction of real-time
RT_FACTORS = {
    ("tiny", "cuda"): 0.03,
    ("tiny", "cpu"): 0.15,
    ("base", "cuda"): 0.04,
    ("base", "cpu"): 0.20,
    ("small", "cuda"): 0.06,
    ("small", "cpu"): 0.35,
    ("medium", "cuda"): 0.12,
    ("medium", "cpu"): 0.80,
    ("large-v3", "cuda"): 0.25,
    ("large-v3", "cpu"): 1.80,
    ("large-v3-turbo", "cuda"): 0.10,
    ("large-v3-turbo", "cpu"): 0.70,
}

# LLM chunk processing time defaults (seconds per chunk)
LLM_CHUNK_TIME_CUDA = 12.0
LLM_CHUNK_TIME_CPU = 60.0
LLM_FINAL_TIME = 1.3  # multiplier for final summary pass

# Multilingual processing multiplier (only if multilingual active and not fast-path)
MULTILINGUAL_MULTIPLIER = 1.4

# LLM-based diarization multiplier (adds ~15% to transcription time for LLM speaker analysis)
DIARIZATION_LLM_MULTIPLIER = 1.15

# Rough estimate: 150 tokens per minute of speech
TOKENS_PER_MINUTE_SPEECH = 150


def _get_perf_stats_path() -> Path:
    """Get path to performance stats JSON file."""
    home = os.path.expanduser("~")
    perf_dir = Path(home) / ".localvideotranscriber"
    return perf_dir / "perf_stats.json"


def load_stats() -> dict:
    """Load performance statistics from file."""
    path = _get_perf_stats_path()
    if not path.exists():
        return {
            "transcribe": {},
            "summarize": {},
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[eta] Failed to load perf_stats: {e}", file=sys.stderr, flush=True)
        return {
            "transcribe": {},
            "summarize": {},
        }


def save_stats(stats: dict) -> None:
    """Save performance statistics to file (best-effort)."""
    try:
        path = _get_perf_stats_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        print(f"[eta] Failed to save perf_stats: {e}", file=sys.stderr, flush=True)


def record_run(kind: str, key: str, observed_value: float) -> None:
    """
    Record a runtime observation and update running average.

    Args:
        kind: "transcribe" or "summarize"
        key: model_device key like "small_cuda" or "qwen7b_cuda"
        observed_value: measured time in seconds
    """
    stats = load_stats()

    if kind not in stats:
        stats[kind] = {}

    if key not in stats[kind]:
        stats[kind][key] = {"count": 0, "avg_rt": 0.0}

    entry = stats[kind][key]
    count = entry.get("count", 0)
    avg = entry.get("avg_rt", 0.0)

    # Exponential moving average with cap at 20 for faster adaptation
    count = min(count + 1, 20)
    if count <= 2:
        weight = 0.7
        avg = weight * observed_value + (1 - weight) * avg if avg > 0 else observed_value
    else:
        avg = (avg * (count - 1) + observed_value) / count

    entry["count"] = count
    entry["avg_rt"] = avg

    save_stats(stats)


def estimate(config: Config, duration_sec: float, device_resolved: str) -> dict:
    """
    Estimate processing time for given configuration and audio duration.

    Args:
        config: Config object
        duration_sec: Audio duration in seconds
        device_resolved: Resolved device string ("cuda" or "cpu")

    Returns:
        Dict with:
        {
            "transcribe_sec": float,
            "summarize_sec": float,
            "diarize_sec": float,
            "total_sec": float,
            "source": "static" | "calibrated",
        }
    """
    # Normalize device
    if device_resolved not in ("cuda", "cpu"):
        device_resolved = "cpu"

    # Normalize whisper model
    whisper_model = config.whisper_model.lower()

    # 1. TRANSCRIPTION TIME
    rt_factor = RT_FACTORS.get((whisper_model, device_resolved))

    if rt_factor is None:
        # Fallback to conservative default
        print(
            f"[eta] Unknown model {whisper_model}/{device_resolved}, using conservative default",
            file=sys.stderr,
            flush=True,
        )
        rt_factor = 1.0 if device_resolved == "cpu" else 0.2

    transcribe_sec = duration_sec * rt_factor
    transcribe_source = "static"

    # Check for calibrated value
    stats = load_stats()
    cal_key = f"{whisper_model}_{device_resolved}"
    if cal_key in stats.get("transcribe", {}):
        cal_entry = stats["transcribe"][cal_key]
        if cal_entry.get("count", 0) >= 1:
            transcribe_sec = duration_sec * cal_entry["avg_rt"]
            transcribe_source = "calibrated"

    # Apply multilingual multiplier (only if multilingual active and language not forced)
    multilingual_active = (
        config.multilingual in ("auto", "true") and
        config.language is None  # fast-path disabled
    )
    if multilingual_active:
        transcribe_sec *= MULTILINGUAL_MULTIPLIER

    # Chunking overhead for very long videos.
    if duration_sec >= config.long_video_threshold_sec:
        # Small fixed overhead for chunk split/merge.
        n_audio_chunks = max(1, math.ceil(duration_sec / max(config.long_video_chunk_sec, 1)))
        transcribe_sec *= 1.05
        transcribe_sec += n_audio_chunks * 2.0

    # 2. DIARIZATION TIME (LLM-based, runs after transcription)
    diarize_sec = 0.0
    if config.diarize:
        diarize_sec = transcribe_sec * (DIARIZATION_LLM_MULTIPLIER - 1.0)

    # 3. SUMMARIZATION TIME
    # When no_summary is set, skip summarization entirely
    if getattr(config, "no_summary", False):
        summarize_sec = 0.0
        summarize_source = "static"
        # Skip all summarization calculations
        total_sec = transcribe_sec + diarize_sec
        return {
            "transcribe_sec": transcribe_sec,
            "summarize_sec": 0.0,
            "diarize_sec": diarize_sec,
            "total_sec": total_sec,
            "source": "calibrated" if transcribe_source == "calibrated" else "static",
        }

    summarize_sec = 0.0

    # Estimate chunk count: 150 tokens/min speech, chunk_size from config
    n_chunks = max(
        1,
        math.ceil((duration_sec / 60.0) * TOKENS_PER_MINUTE_SPEECH / config.chunk_size_tokens),
    )

    llm_chunk_time = LLM_CHUNK_TIME_CUDA if device_resolved == "cuda" else LLM_CHUNK_TIME_CPU
    concurrency = max(1, int(config.summary_max_concurrency))
    summarize_sec = (n_chunks * llm_chunk_time) / concurrency + LLM_FINAL_TIME * llm_chunk_time
    summarize_source = "static"

    # Check for calibrated LLM value
    # Build LLM key from config
    llm_key_parts = []
    if config.llm_model_path:
        llm_key_parts.append(Path(config.llm_model_path).stem)
    else:
        # Use repo/file combo
        repo_name = config.llm_model_repo.split("/")[-1].lower()
        file_name = config.llm_model_file.split(".")[0].lower()
        llm_key_parts.append(f"{repo_name}_{file_name}")

    llm_key = f"{'_'.join(llm_key_parts)}_{device_resolved}".replace("-", "_")

    if llm_key in stats.get("summarize", {}):
        llm_entry = stats["summarize"][llm_key]
        if llm_entry.get("count", 0) >= 1:
            cal_sec_per_chunk = llm_entry.get("avg_rt", llm_chunk_time)
            summarize_sec = n_chunks * cal_sec_per_chunk + LLM_FINAL_TIME * cal_sec_per_chunk
            summarize_source = "calibrated"

    # 4. TOTAL
    total_sec = transcribe_sec + diarize_sec + summarize_sec

    return {
        "transcribe_sec": transcribe_sec,
        "summarize_sec": summarize_sec,
        "diarize_sec": diarize_sec,
        "total_sec": total_sec,
        "source": "calibrated" if transcribe_source == "calibrated" or summarize_source == "calibrated" else "static",
    }


def format_eta(seconds: float) -> str:
    """
    Format seconds into human-readable time string.

    Examples:
        3661 -> "1h 1m"
        125 -> "2m 5s"
        45 -> "45s"

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string
    """
    if seconds < 0:
        return "0s"

    hours = int(seconds // 3600)
    remaining = seconds % 3600
    minutes = int(remaining // 60)
    secs = int(remaining % 60)

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)
