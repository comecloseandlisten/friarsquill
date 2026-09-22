"""
LLM-based speaker diarization.

Analyzes transcribed text semantically to identify distinct speakers
using the same LLM (Qwen) that powers summarization.
"""
import gc
import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)


PROMPTS_DIR = Path(__file__).parent / "prompts"


def _log(msg):
    print(f"[diarizer] {msg}", file=sys.stderr, flush=True)


def _load_llm(config: Config):
    """Load the LLM using the same resolution logic as summarizer."""
    from llama_cpp import Llama

    model_path = None
    model_repo = config.llm_model_repo
    model_file = config.llm_model_file

    if config.llm_model_preset:
        from models_registry import resolve_llm_preset
        project_root = Path(__file__).parent.parent
        resolved = resolve_llm_preset(config.llm_model_preset, str(project_root))
        if resolved["path"]:
            model_path = resolved["path"]
        if resolved["repo"]:
            model_repo = resolved["repo"]
        if resolved["file"]:
            model_file = resolved["file"]

    if not model_path:
        model_path = config.llm_model_path
    if not model_path:
        project_root = Path(__file__).parent.parent
        gguf_files = sorted(project_root.glob("*.gguf"))
        for pref in ("q4_k_m", "q6_k", "q8_0"):
            for f in gguf_files:
                if pref in f.name.lower():
                    model_path = str(f)
                    break
            if model_path:
                break
        if not model_path and gguf_files:
            model_path = str(min(gguf_files, key=lambda f: f.stat().st_size))
    if not model_path:
        from huggingface_hub import hf_hub_download
        model_path = hf_hub_download(repo_id=model_repo, filename=model_file)

    try:
        import llama_cpp
        cuda_available = bool(llama_cpp.llama_supports_gpu_offload())
    except Exception:
        cuda_available = False

    gpu_layers = config.llm_gpu_layers if cuda_available else 0

    if config.llm_context_length > 0:
        resolved_n_ctx = config.llm_context_length
    else:
        from summarizer import _detect_gpu_info, _auto_context_length
        import os
        model_size_mb = os.path.getsize(model_path) // (1024 * 1024)
        gpu_info = _detect_gpu_info()
        resolved_n_ctx = _auto_context_length(model_size_mb, gpu_info["vram_mb"], gpu_layers)

    _log(f"Loading LLM: {Path(model_path).name} (gpu_layers={gpu_layers}, n_ctx={resolved_n_ctx})")

    llm = Llama(
        model_path=model_path,
        n_ctx=resolved_n_ctx,
        n_gpu_layers=gpu_layers,
        n_threads=max(1, int(config.llm_n_threads)),
        verbose=False,
    )
    return llm


def _format_lines_for_llm(segments: list[dict], start_idx: int = 0) -> str:
    """Format segments as numbered lines for LLM input."""
    lines = []
    for i, seg in enumerate(segments):
        line_num = start_idx + i + 1
        lines.append(f"{line_num}. [{seg['start']:.1f}s] {seg['text']}")
    return "\n".join(lines)


def _parse_speaker_assignments(response: str, num_lines: int) -> dict[int, str]:
    """
    Parse LLM response into {line_number: "SPEAKER_XX"} mapping.

    Handles two-phase output (SPEAKERS: + ASSIGNMENTS: blocks) by
    focusing on lines after ASSIGNMENTS: if present, otherwise scanning
    the entire response.

    Accepts labels like S1, S2, SQ and formats:
      1:S1  /  1: S1  /  1 - S1  /  1. S1  /  1:SQ
    """
    text = response
    assignments_marker = re.search(r"^ASSIGNMENTS\s*:", text, re.MULTILINE | re.IGNORECASE)
    if assignments_marker:
        text = text[assignments_marker.end():]

    assignments = {}
    for match in re.finditer(r"(\d+)\s*[:\-\.]\s*S(\d+|Q)", text):
        line_num = int(match.group(1))
        label = match.group(2)
        if 1 <= line_num <= num_lines:
            if label == "Q":
                assignments[line_num] = "SPEAKER_QUOTED"
            else:
                speaker_num = int(label)
                assignments[line_num] = f"SPEAKER_{speaker_num - 1:02d}"

    if not assignments and num_lines > 0:
        logger.warning(
            "LLM returned zero valid speaker assignments for %d lines", num_lines
        )

    return assignments


def _load_diarize_prompt() -> str:
    prompt_file = PROMPTS_DIR / "diarize.txt"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Diarize prompt not found: {prompt_file}")


def diarize_with_llm(
    segments: list[dict],
    config: Config,
    progress_cb=None,
    transcript_language: str | None = None,
    llm=None,
) -> list[dict]:
    """
    Analyze transcript semantically to identify speakers using the selected LLM.

    Processes segments in chunks, asks the LLM to assign speaker numbers,
    then merges results back into the segment list.

    Args:
        segments: List of transcript segment dicts (start, end, text, language)
        config: Config with LLM settings
        progress_cb: Optional (percent, message) callback
        transcript_language: Detected transcript language code
        llm: Optional pre-loaded Llama instance (caller manages lifecycle)

    Returns:
        The same segments list with 'speaker' field added to each segment.
    """
    if not segments:
        return segments

    if progress_cb:
        progress_cb(0, "Loading LLM for speaker detection...")

    try:
        prompt_template = _load_diarize_prompt()
    except FileNotFoundError:
        _log("Diarize prompt template not found, skipping diarization")
        return segments

    _owns_llm = llm is None
    try:
        if _owns_llm:
            llm = _load_llm(config)
        chunk_size = 60
        overlap = 5
        total = len(segments)
        all_assignments: dict[int, str] = {}

        n_chunks = max(1, (total + chunk_size - overlap - 1) // (chunk_size - overlap))
        _log(f"Diarizing {total} segments in {n_chunks} chunk(s)")

        for chunk_idx in range(n_chunks):
            start = chunk_idx * (chunk_size - overlap)
            end = min(start + chunk_size, total)
            chunk_segments = segments[start:end]

            if not chunk_segments:
                break

            lines_text = _format_lines_for_llm(chunk_segments, start_idx=start)
            prompt = prompt_template.replace("{{LINES}}", lines_text)

            if progress_cb:
                pct = int(10 + 80 * chunk_idx / n_chunks)
                progress_cb(pct, f"Detecting speakers... chunk {chunk_idx + 1}/{n_chunks}")

            _log(f"Chunk {chunk_idx + 1}/{n_chunks}: lines {start + 1}-{end}")

            response = llm.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a professional media-content analyst. "
                            "You identify distinct speakers in video transcripts by analyzing "
                            "semantic cues: dialogue patterns, vocabulary register, topic flow, "
                            "and signs of quoted/embedded external content. "
                            "Follow the output format exactly."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
                repeat_penalty=1.0,
            )
            raw = response["choices"][0]["message"]["content"] or ""
            _log(f"  LLM response length: {len(raw)} chars")

            chunk_assignments = _parse_speaker_assignments(raw, end)

            for line_num, speaker in chunk_assignments.items():
                if line_num not in all_assignments or chunk_idx > 0 and line_num > start + overlap:
                    all_assignments[line_num] = speaker

        _log(f"Speaker assignments: {len(all_assignments)}/{total} segments assigned")

        default_speaker = "SPEAKER_00"
        for i, seg in enumerate(segments):
            line_num = i + 1
            seg["speaker"] = all_assignments.get(line_num, default_speaker)

        speakers_found = set(seg.get("speaker", default_speaker) for seg in segments)
        _log(f"Diarization complete: {len(speakers_found)} unique speakers")

        if progress_cb:
            progress_cb(95, f"Speaker detection complete: {len(speakers_found)} speakers")

    except Exception as e:
        _log(f"LLM diarization failed: {e}")
        for seg in segments:
            seg["speaker"] = "SPEAKER_00"

    finally:
        if _owns_llm and llm is not None:
            del llm
            gc.collect()

    return segments
