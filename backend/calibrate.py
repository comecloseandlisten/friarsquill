"""
Calibrate module — hardware-aware LLM parameter selection.

Provides a single entry point for resolving LLM context length and
(in the future) model recommendations based on detected hardware.

The core formula is identical to the original _auto_context_length logic
in summarizer.py; this module centralises it so that callers (summarizer,
future UI hints, etc.) share a single source of truth.
"""

import subprocess
import sys
from typing import Optional


def detect_gpu_info() -> dict:
    """
    Detect CUDA GPU name and total VRAM in MB.

    Returns:
        {"cuda": bool, "device": str, "vram_mb": int}
    """
    result = {"cuda": False, "device": "", "vram_mb": 0}

    # Attempt 1: pynvml (often bundled with CUDA toolkit)
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8")
        pynvml.nvmlShutdown()
        result["cuda"] = True
        result["device"] = name
        result["vram_mb"] = int(mem.total / (1024 * 1024))
        return result
    except Exception:
        pass

    # Attempt 2: nvidia-smi
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            timeout=5, stderr=subprocess.DEVNULL,
        ).decode("utf-8").strip()
        if out:
            parts = out.split("\n")[0].split(",")
            result["cuda"] = True
            result["device"] = parts[0].strip()
            result["vram_mb"] = int(parts[1].strip())
            return result
    except Exception:
        pass

    return result


def calibrate_llm_context(
    model_size_mb: int,
    vram_mb: int,
    gpu_layers: int,
) -> int:
    """
    Calculate optimal n_ctx based on model file size and available VRAM/RAM.

    This is the single source of truth for n_ctx auto-calculation.
    The formula is identical to the original ``_auto_context_length``
    that shipped before the calibrate refactoring.

    Args:
        model_size_mb: GGUF file size in megabytes.
        vram_mb: Total GPU VRAM in megabytes (0 when GPU absent).
        gpu_layers: Number of model layers offloaded to GPU.
            0 = CPU only, -1 = all layers on GPU.

    Returns:
        Resolved n_ctx value (always a multiple of 1024, 4096 ≤ n ≤ 131072).
    """
    if gpu_layers == 0 or vram_mb == 0:
        return 8192  # CPU: balance quality vs speed, tree-reduce compensates

    # Model weights go to VRAM when gpu_layers != 0
    available_mb = vram_mb - model_size_mb - 512  # overhead buffer
    if available_mb <= 0:
        return 4096

    # KV cache per token estimate by model weight class
    if model_size_mb < 3000:
        kv_kb = 50      # ~3B params
    elif model_size_mb < 5000:
        kv_kb = 100     # ~7B params
    elif model_size_mb < 7000:
        kv_kb = 130     # ~9B params
    elif model_size_mb < 10000:
        kv_kb = 180     # ~13B params
    else:
        kv_kb = 250     # larger

    # Use 50% of remaining VRAM for KV cache; keep the rest for compute buffers.
    kv_budget_mb = max(512, available_mb * 0.5)
    max_ctx = int(kv_budget_mb * 1024 / kv_kb)
    ctx = min(131072, max(4096, max_ctx))
    return (ctx // 1024) * 1024  # round to nearest 1024


def calibrate_generation_budget(n_ctx: int, prompt_est_tokens: int) -> int:
    """
    Calculate max_tokens for a non-final (chunk / intermediate) LLM call.

    The budget is derived from calibrated n_ctx so it adapts to actual
    hardware.  A cap of ``n_ctx // 2`` keeps chunk agents focused and
    prevents them from rambling on large contexts — the heavy lifting
    belongs to the final reduce which runs with max_tokens=-1.

    Concrete examples by hardware tier:
        CPU  (n_ctx= 8192) → cap 4096, typical budget ~4096
        8GB  (n_ctx=12800) → cap 6400, typical budget ~6400
        24GB (n_ctx=94208) → cap 47104, typical budget ~47104

    Args:
        n_ctx: Context window size (from ``calibrate_llm_context``).
        prompt_est_tokens: Estimated prompt size in tokens.

    Returns:
        Generation budget in tokens (≥ 1024).
    """
    available = n_ctx - prompt_est_tokens - 128  # overhead for framing
    cap = n_ctx // 2  # non-final calls: half the context window
    return max(1024, min(available, cap))


def calibrate_recommendations(
    gpu_info: Optional[dict] = None,
) -> dict:
    """
    Return hardware-aware recommendations (stub for future expansion).

    Currently returns only ``resolved_n_ctx`` hints; can be extended
    to suggest Whisper / LLM model tiers based on VRAM budget.

    Args:
        gpu_info: Output of ``detect_gpu_info()``.  If None, will be
            auto-detected.

    Returns:
        Dict with at least ``{"gpu_info": {...}}``.  Future keys may
        include ``"recommended_whisper"``, ``"recommended_llm_preset"``, etc.
    """
    if gpu_info is None:
        gpu_info = detect_gpu_info()

    return {
        "gpu_info": gpu_info,
    }
