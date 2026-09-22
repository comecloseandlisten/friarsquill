import gc
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from config import Config
from languages import resolve_summary_language, LANGUAGE_NAMES

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Whitelist of allowed summary modes
ALLOWED_MODES = {"notes", "call_check", "factcheck", "tldr"}

# Maximum length for config values to prevent DoS
MAX_CONFIG_VALUE_LENGTH = 10000


def _detect_gpu_info() -> dict:
    """
    Detect CUDA GPU name and total VRAM in MB.
    Returns {"cuda": bool, "device": str, "vram_mb": int}.

    Delegates to ``calibrate.detect_gpu_info`` (single source of truth).
    Kept here for backward compatibility with main.py and diarizer.py imports.
    """
    from calibrate import detect_gpu_info
    return detect_gpu_info()


def _auto_context_length(model_size_mb: int, vram_mb: int, gpu_layers: int) -> int:
    """
    Calculate optimal n_ctx based on model size and available VRAM/RAM.

    Delegates to ``calibrate.calibrate_llm_context`` (single source of truth).
    Kept here for backward compatibility with diarizer.py imports and tests.
    """
    from calibrate import calibrate_llm_context
    return calibrate_llm_context(model_size_mb, vram_mb, gpu_layers)


def _log_security(msg):
    """Log security-related messages to stderr."""
    print(f"[summarizer_security] {msg}", file=sys.stderr, flush=True)


def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~3 chars per token for mixed lang content."""
    return len(text) // 3


def _strip_thinking_tags(text: str) -> str:
    """
    Remove internal reasoning / chain-of-thought that some models (Qwen, DeepSeek,
    etc.) emit before the actual answer. Thinking content is "system guts" and
    must never leak into the user-facing report.

    Handles:
      * <think>...</think>, <thinking>...</thinking>, <thought>...</thought>
      * <reflection>...</reflection>, <inner_monologue>...</inner_monologue>
      * Dangling opening tag with no closing (truncated thinking)
      * Dangling closing tag with no opening (thinking started before any marker)
      * Markdown-style "Thinking:" / "Reasoning:" / "Размышление:" preambles
      * Mixed-language markers (Chinese 思考, Russian Размышление)
      * Multiple consecutive thinking blocks
    """
    if not text:
        return text

    cleaned = text

    # Tag names that denote internal reasoning (English + transliterated)
    _THINK_TAGS = r"think|thinking|thought|reasoning|reflection|inner_monologue|scratchpad"

    # 1) Remove ALL complete <think>...</think>-style blocks (greedy across multiple)
    #    Use a loop to handle nested or consecutive blocks reliably.
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = re.sub(
            rf"<\s*({_THINK_TAGS})\s*>.*?<\s*/\s*\1\s*>",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )

    # 2) Dangling closing tag (no opening): keep only what comes AFTER the last closing tag.
    closing_match = None
    for m in re.finditer(rf"<\s*/\s*({_THINK_TAGS})\s*>", cleaned, flags=re.IGNORECASE):
        closing_match = m
    if closing_match:
        cleaned = cleaned[closing_match.end():]

    # 3) Dangling opening tag (no closing): drop everything from the tag onwards.
    cleaned = re.sub(
        rf"<\s*({_THINK_TAGS})\s*>.*",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 4) Markdown-style preambles: "## Thinking:", "Reasoning:", "Размышление:",
    #    "思考过程:", etc., followed by a blank line and the real answer.
    preamble_match = re.match(
        r"\s*(?:#+\s*)?"
        r"(?:thinking|reasoning|thought process|internal|"
        r"размышлени[еяй]|рассуждени[еяй]|ход мысл[ией]|"
        r"思考过程|思考|推理)"
        r"[:\-\s].*?\n\s*\n",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if preamble_match and len(cleaned) - preamble_match.end() > 50:
        cleaned = cleaned[preamble_match.end():]

    # 5) Strip leading/trailing whitespace artifacts left after removals
    cleaned = cleaned.strip()

    # 6) If the result is empty but original wasn't, return empty to signal
    #    the caller that the model produced only thinking (no usable content).
    return cleaned


def _detect_repetition_loop(text: str, min_phrase_len: int = 20, max_repeats: int = 3) -> str:
    """
    Detect and truncate repetition loops in generated text.
    If a phrase of min_phrase_len+ chars repeats more than max_repeats times,
    truncate the text at the start of the repetition.

    Args:
        text: Generated text to check
        min_phrase_len: Minimum phrase length to consider as repetition
        max_repeats: Maximum allowed repeats before truncation

    Returns:
        Cleaned text with repetition loops removed
    """
    if not text or len(text) < min_phrase_len * max_repeats:
        return text

    # Strategy 1: detect repeated lines
    lines = text.split("\n")
    seen_counts: dict[str, int] = {}
    truncate_at = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if len(stripped) < 10:
            continue
        seen_counts[stripped] = seen_counts.get(stripped, 0) + 1
        if seen_counts[stripped] > max_repeats:
            # Find where the repetition started
            truncate_at = i - max_repeats
            break

    if truncate_at < len(lines):
        text = "\n".join(lines[:max(truncate_at, 1)])

    # Strategy 2: detect repeated multi-word phrases via regex
    # Look for any substring of 20+ chars that repeats 3+ times
    pattern = re.compile(r"(.{20,}?)\1{2,}", re.DOTALL)
    match = pattern.search(text)
    if match:
        text = text[:match.start()] + match.group(1)

    return text.strip()


def _validate_mode(mode: str) -> str:
    """
    Validate and sanitize summary mode to prevent path traversal.

    Args:
        mode: Requested summary mode

    Returns:
        Validated mode string

    Raises:
        ValueError: If mode is invalid
    """
    if not mode or not isinstance(mode, str):
        _log_security(f"Invalid mode type: {type(mode)}")
        raise ValueError("Invalid mode")

    # Check for path traversal attempts
    if ".." in mode or "/" in mode or "\\" in mode or mode.startswith("."):
        _log_security(f"Path traversal attempt detected in mode: {repr(mode)}")
        raise ValueError("Invalid mode: path traversal detected")

    # Normalize to lowercase for comparison
    mode_lower = mode.lower().strip()

    # Check whitelist
    if mode_lower not in ALLOWED_MODES:
        _log_security(f"Unknown mode requested: {repr(mode)}, falling back to 'notes'")
        return "notes"

    return mode_lower


def _load_prompt_template(mode: str, phase: str) -> str:
    """
    Load prompt template for a given mode and phase (chunk or final).
    Falls back to 'notes' mode if the requested mode is not found.

    Args:
        mode: summary mode (notes, call_check, factcheck, tldr)
        phase: 'chunk' or 'final'

    Returns:
        Prompt template text
    """
    # Validate and sanitize mode
    mode = _validate_mode(mode)

    # Validate phase
    if phase not in ("chunk", "final", "batch_chunk", "batch_final"):
        raise ValueError(f"Invalid phase: {phase}")

    mode_dir = PROMPTS_DIR / mode
    prompt_file = mode_dir / f"{phase}.txt"

    # Verify the resolved path is within PROMPTS_DIR (path traversal check)
    try:
        prompt_file.resolve().relative_to(PROMPTS_DIR.resolve())
    except ValueError:
        _log_security(f"Path traversal attempt: resolved path outside PROMPTS_DIR")
        raise FileNotFoundError(f"Prompt template not found for mode='{mode}', phase='{phase}'")

    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")

    # Fallback to 'notes' mode if requested mode not found
    fallback_file = PROMPTS_DIR / "notes" / f"{phase}.txt"
    if fallback_file.exists():
        return fallback_file.read_text(encoding="utf-8")

    # Last resort: look for old-style prompts (backward compatibility)
    old_file = PROMPTS_DIR / f"{phase}_summary.txt"
    if old_file.exists():
        return old_file.read_text(encoding="utf-8")

    raise FileNotFoundError(f"Prompt template not found for mode='{mode}', phase='{phase}'")


def _validate_config_value(key: str, value) -> str:
    """
    Validate and sanitize a config value to prevent injection attacks.

    Args:
        key: Config key name (for validation purposes)
        value: Config value

    Returns:
        Sanitized string representation of the value

    Raises:
        ValueError: If value is invalid or too large
    """
    # Check for None and empty values
    if value is None:
        return ""

    # Convert to string
    if isinstance(value, (list, tuple)):
        # Validate each item in the list
        validated_items = []
        for item in value:
            if not isinstance(item, str):
                item = str(item)
            if len(item) > MAX_CONFIG_VALUE_LENGTH:
                _log_security(f"Config value too long for key '{key}': {len(item)} > {MAX_CONFIG_VALUE_LENGTH}")
                raise ValueError(f"Config value too long for key '{key}'")
            validated_items.append(item)
        # Convert to formatted string
        return "\n".join(f"- {item}" for item in validated_items)
    else:
        value_str = str(value)

    # Check length
    if len(value_str) > MAX_CONFIG_VALUE_LENGTH:
        _log_security(f"Config value too long for key '{key}': {len(value_str)} > {MAX_CONFIG_VALUE_LENGTH}")
        raise ValueError(f"Config value too long for key '{key}'")

    return value_str


def _substitute_config_variables(template: str, config_dict: dict) -> str:
    """
    Substitute {{VARIABLE}} placeholders in template with values from config_dict.
    Values are validated and sanitized to prevent injection attacks.

    Args:
        template: Prompt template with {{PLACEHOLDER}} syntax
        config_dict: Dictionary of variables to substitute

    Returns:
        Template with substitutions applied
    """
    result = template

    # Validate that config_dict is a dict
    if not isinstance(config_dict, dict):
        _log_security(f"Invalid config_dict type: {type(config_dict)}")
        return result

    # Define whitelisted config keys per mode (flexible, but logged)
    allowed_keys_general = {
        "required_phrases", "red_flags", "scoring_criteria",
        "evaluation_goal", "required_items",  # Inquisition mode variables
        "transcript_chunk", "chunk_summaries"  # Standard template variables
    }

    for key, value in config_dict.items():
        # Log non-standard keys (informational, not blocking)
        if key not in allowed_keys_general:
            _log_security(f"Non-standard config key used: '{key}'")

        placeholder = f"{{{{{key}}}}}"

        # Only substitute if placeholder exists in template (prevent injection via unused keys)
        if placeholder not in template:
            _log_security(f"Config key '{key}' not used in template, skipping substitution")
            continue

        # Validate and sanitize the value
        try:
            value_str = _validate_config_value(key, value)
        except ValueError as e:
            _log_security(f"Config validation failed for key '{key}': {str(e)}")
            # Use empty string for failed validations
            value_str = ""

        result = result.replace(placeholder, value_str)

    return result


def _chunk_segments(segments: list[dict], chunk_size: int = 1500, overlap: int = 200) -> list[str]:
    """Split segments into text chunks of ~chunk_size tokens with overlap."""
    chunks = []
    current_chunk = []
    current_tokens = 0

    for seg in segments:
        line = seg["text"]
        line_tokens = _estimate_tokens(line)

        if current_tokens + line_tokens > chunk_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            # Keep overlap
            overlap_lines = []
            overlap_tokens = 0
            for prev_line in reversed(current_chunk):
                lt = _estimate_tokens(prev_line)
                if overlap_tokens + lt > overlap:
                    break
                overlap_lines.insert(0, prev_line)
                overlap_tokens += lt
            current_chunk = overlap_lines
            current_tokens = overlap_tokens

        current_chunk.append(line)
        current_tokens += line_tokens

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def _find_local_gguf() -> Optional[str]:
    """
    Auto-detect a local .gguf model file in the project root.
    Prefers Q4_K_M quantization if multiple files are found.
    Returns the path as a string, or None if no local model found.
    """
    project_root = Path(__file__).parent.parent
    gguf_files = sorted(project_root.glob("*.gguf"))
    if not gguf_files:
        return None

    # Prefer Q4_K_M, then Q6_K, then Q8_0, then any other
    preference = ["q4_k_m", "q6_k", "q8_0"]
    for pref in preference:
        for f in gguf_files:
            if pref in f.name.lower():
                print(f"[summarizer] Auto-detected local model: {f.name}", file=sys.stderr, flush=True)
                return str(f)

    # Fallback: return the smallest file (likely the most quantized)
    smallest = min(gguf_files, key=lambda f: f.stat().st_size)
    print(f"[summarizer] Auto-detected local model (smallest): {smallest.name}", file=sys.stderr, flush=True)
    return str(smallest)


def _build_llm_kwargs(model_path: str, config: Config, cuda_available: bool, n_ctx: int = 0) -> dict:
    gpu_layers = config.llm_gpu_layers if cuda_available else 0
    return {
        "model_path": model_path,
        "n_ctx": n_ctx or config.llm_context_length or 8192,
        "n_gpu_layers": gpu_layers,
        "n_threads": max(1, int(config.llm_n_threads)),
        "verbose": False,
    }


def _build_summary_checkpoint_path(config: Config, checkpoint_key: str) -> Path:
    checkpoint_dir = Path(config.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    return checkpoint_dir / f"summary_{checkpoint_key}.json"


def _load_summary_checkpoint(path: Path, expected_chunks: int) -> dict[int, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("total_chunks") != expected_chunks:
            return {}
        return {int(k): v for k, v in data.get("chunks", {}).items()}
    except Exception:
        return {}


def _save_summary_checkpoint(path: Path, total_chunks: int, chunks: dict[int, str]) -> None:
    data = {
        "total_chunks": total_chunks,
        "chunks": {str(k): v for k, v in sorted(chunks.items())},
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _format_chunk_summaries(summaries: list[str]) -> str:
    return "\n\n---\n\n".join([f"## Chunk {i + 1}\n{s}" for i, s in enumerate(summaries)])


def _llm_call(llm, prompt: str, config: Config, system_msg: str = "", max_tokens: int = 4096) -> str:
    """Single LLM call with thinking-tag stripping and repetition detection."""
    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": prompt})
    # #region agent log
    _t0 = __import__('time').time()
    # #endregion
    response = llm.create_chat_completion(
        messages=messages,
        temperature=config.llm_temperature,
        max_tokens=max_tokens,
        repeat_penalty=config.llm_repeat_penalty,
    )
    raw = response["choices"][0]["message"]["content"]
    # #region agent log
    _elapsed = __import__('time').time() - _t0
    _lp = __import__('pathlib').Path(__file__).parent.parent / "debug-ff5c79.log"
    open(_lp,"a",encoding="utf-8").write(json.dumps({"sessionId":"ff5c79","location":"summarizer.py:_llm_call","message":"llm_call_result","data":{"elapsed_sec":round(_elapsed,2),"max_tokens":max_tokens,"prompt_est_tokens":_estimate_tokens(prompt),"raw_len":len(raw) if raw else 0,"raw_start":repr((raw or "")[:200]),"raw_end":repr((raw or "")[-100:])},"timestamp":int(__import__('time').time()*1000),"hypothesisId":"H3-H4"},ensure_ascii=False)+"\n")
    # #endregion
    before_len = len(raw) if raw else 0
    raw = _strip_thinking_tags(raw)
    after_len = len(raw) if raw else 0
    if before_len != after_len:
        print(f"[summarizer] Stripped thinking: {before_len} → {after_len} chars", file=sys.stderr, flush=True)
        # #region agent log
        _lp2 = __import__('pathlib').Path(__file__).parent.parent / "debug-ff5c79.log"
        open(_lp2,"a",encoding="utf-8").write(json.dumps({"sessionId":"ff5c79","location":"summarizer.py:_llm_call_strip","message":"thinking_stripped","data":{"before":before_len,"after":after_len,"result_start":repr(raw[:200]) if raw else "empty"},"timestamp":int(__import__('time').time()*1000),"hypothesisId":"H3"},ensure_ascii=False)+"\n")
        # #endregion
    elif raw and "<" in raw[:200]:
        print(f"[summarizer] WARNING: possible unstripped tags: {repr(raw[:200])}", file=sys.stderr, flush=True)

    # If strip left us with empty/trivially short text, the model only produced
    # thinking.  Log a warning; the caller will see a very short result but at
    # least no leaked reasoning.
    if raw is not None and len(raw.strip()) < 20 and before_len > 100:
        print(
            f"[summarizer] WARNING: after stripping thinking tags, result is "
            f"nearly empty ({len(raw.strip())} chars from {before_len}). "
            f"Model may have produced only reasoning.",
            file=sys.stderr, flush=True,
        )

    return _detect_repetition_loop(raw)


def _tree_reduce(
    llm,
    chunk_summaries: list[str],
    reduce_template: str,
    config: Config,
    system_msg: str,
    target_language: str,
    n_ctx: int,
    progress_cb=None,
    depth: int = 0,
) -> str:
    """
    Hierarchical tree-reduce: if combined summaries exceed the context window,
    split into groups, reduce each group, then recursively reduce the results.
    """
    combined = _format_chunk_summaries(chunk_summaries)
    prompt = reduce_template.replace("{{CHUNK_SUMMARIES}}", combined)
    prompt = _substitute_config_variables(prompt, config.summary_mode_config)
    prompt = prompt.replace("{target_language}", target_language)

    est_tokens = _estimate_tokens(prompt)
    # Splitting decision: use modest reserve so summaries fit in context.
    # This is SEPARATE from gen_budget which controls actual output length.
    max_input = max(1, n_ctx - 2048)
    # #region agent log
    _lp = __import__('pathlib').Path(__file__).parent.parent / "debug-ff5c79.log"
    open(_lp,"a",encoding="utf-8").write(json.dumps({"sessionId":"ff5c79","location":"summarizer.py:_tree_reduce","message":"reduce_params","data":{"depth":depth,"n_ctx":n_ctx,"est_tokens":est_tokens,"max_input":max_input,"num_summaries":len(chunk_summaries),"summary_lengths":[len(s) for s in chunk_summaries]},"timestamp":int(__import__('time').time()*1000),"hypothesisId":"H1"},ensure_ascii=False)+"\n")
    # #endregion

    if est_tokens <= max_input or len(chunk_summaries) <= 1:
        # Unlimited generation: -1 tells llama-cpp to fill the entire
        # remaining context window.  Models that think internally (Qwen,
        # DeepSeek) consume 50-70 % of tokens on <think> blocks, so any
        # hard cap risks truncating the actual summary.
        # _strip_thinking_tags removes reasoning afterwards.
        tag = "Final" if depth == 0 else f"Depth-{depth}"
        print(
            f"[summarizer] {tag} reduce: {len(chunk_summaries)} summaries, "
            f"~{est_tokens} tokens, max_tokens=unlimited (n_ctx={n_ctx})",
            file=sys.stderr, flush=True,
        )
        if progress_cb:
            progress_cb(75 + min(20, depth * 5), "Synthesizing final summary...")
        return _llm_call(llm, prompt, config, system_msg, max_tokens=-1)

    # Doesn't fit — calculate group size
    template_tokens = _estimate_tokens(
        reduce_template.replace("{{CHUNK_SUMMARIES}}", "")
    )
    available = max_input - template_tokens
    avg_per_summary = max(1, _estimate_tokens(combined) // len(chunk_summaries))
    per_group = max(2, available // avg_per_summary)

    groups = [
        chunk_summaries[i : i + per_group]
        for i in range(0, len(chunk_summaries), per_group)
    ]
    print(
        f"[summarizer] Tree-reduce depth {depth}: {len(chunk_summaries)} summaries "
        f"→ {len(groups)} groups of ~{per_group} (~{est_tokens} tokens, limit {max_input})",
        file=sys.stderr, flush=True,
    )

    intermediate = []
    for gi, group in enumerate(groups):
        g_combined = _format_chunk_summaries(group)
        g_prompt = reduce_template.replace("{{CHUNK_SUMMARIES}}", g_combined)
        g_prompt = _substitute_config_variables(g_prompt, config.summary_mode_config)
        g_prompt = g_prompt.replace("{target_language}", target_language)

        from calibrate import calibrate_generation_budget
        g_budget = calibrate_generation_budget(n_ctx, _estimate_tokens(g_prompt))
        g_summary = _llm_call(llm, g_prompt, config, system_msg, max_tokens=g_budget)
        intermediate.append(g_summary)

        if progress_cb:
            progress_cb(
                75 + int(20 * (gi + 1) / len(groups) / (depth + 2)),
                f"Reducing group {gi + 1}/{len(groups)} (level {depth + 1})...",
            )

    return _tree_reduce(
        llm, intermediate, reduce_template, config, system_msg,
        target_language, n_ctx, progress_cb, depth + 1,
    )


def _run_chunk_summary(llm, prompt: str, config: Config, system_msg: str = "", n_ctx: int = 8192) -> str:
    from calibrate import calibrate_generation_budget
    budget = calibrate_generation_budget(n_ctx, _estimate_tokens(prompt))
    return _llm_call(llm, prompt, config, system_msg, max_tokens=budget)


def summarize(
    segments: list[dict],
    config: Config,
    progress_cb=None,
    batch_mode: bool = False,
    transcript_language: str | None = None,
    checkpoint_key: str | None = None,
    llm=None,
) -> str:
    from llama_cpp import Llama

    _owns_llm = llm is None

    # Load model: preset override > explicit path > local auto-detect > HuggingFace download
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
        model_path = _find_local_gguf()
    if not model_path:
        from huggingface_hub import hf_hub_download
        model_path = hf_hub_download(
            repo_id=model_repo,
            filename=model_file,
        )

    try:
        import llama_cpp
        cuda_available = bool(llama_cpp.llama_supports_gpu_offload())
    except Exception:
        cuda_available = False

    gpu_layers = config.llm_gpu_layers if cuda_available else 0
    if not cuda_available and config.llm_gpu_layers != 0:
        print("[summarizer] WARNING: GPU offload not available in llama-cpp-python, running on CPU only", file=sys.stderr, flush=True)

    model_size_mb = os.path.getsize(model_path) // (1024 * 1024)
    if config.llm_context_length > 0:
        resolved_n_ctx = config.llm_context_length
        print(f"[summarizer] n_ctx={resolved_n_ctx} (explicit from config)", file=sys.stderr, flush=True)
    else:
        from calibrate import detect_gpu_info, calibrate_llm_context
        gpu_info = detect_gpu_info()
        resolved_n_ctx = calibrate_llm_context(model_size_mb, gpu_info["vram_mb"], gpu_layers)
        if gpu_info["vram_mb"] > 0:
            print(
                f"[summarizer] Auto n_ctx={resolved_n_ctx} "
                f"(VRAM: {gpu_info['vram_mb']} MB, model: {model_size_mb} MB, "
                f"device: {gpu_info['device']})",
                file=sys.stderr, flush=True,
            )
        else:
            print(
                f"[summarizer] Auto n_ctx={resolved_n_ctx} (CPU mode, model: {model_size_mb} MB)",
                file=sys.stderr, flush=True,
            )

    llm_kwargs = _build_llm_kwargs(model_path, config, cuda_available, n_ctx=resolved_n_ctx)

    if _owns_llm:
        print(f"[summarizer] Loading model: {Path(model_path).name} (gpu_layers={gpu_layers}, n_ctx={resolved_n_ctx}, cuda={'yes' if cuda_available else 'no'})", file=sys.stderr, flush=True)
        llm = Llama(**llm_kwargs)
    else:
        print(f"[summarizer] Reusing shared LLM (n_ctx={resolved_n_ctx})", file=sys.stderr, flush=True)

    try:
        # Resolve target summary language
        target_language_code, target_language_name = resolve_summary_language(
            config.summary_language, transcript_language
        )
        source_hint = "auto" if config.summary_language == "auto" else "explicit"
        print(f"[summarizer] target language: {target_language_name} ({target_language_code}) [source={source_hint}]", file=sys.stderr, flush=True)

        # Load prompt templates based on summary_mode
        mode = config.summary_mode or "notes"
        print(f"[summarizer] Loading prompt templates for mode={mode}...", file=sys.stderr, flush=True)
        try:
            mode = _validate_mode(mode)

            if batch_mode:
                try:
                    chunk_prompt_template = _load_prompt_template(mode, "batch_chunk")
                    final_prompt_template = _load_prompt_template(mode, "batch_final")
                except FileNotFoundError:
                    chunk_prompt_template = _load_prompt_template(mode, "chunk")
                    final_prompt_template = _load_prompt_template(mode, "final")
            else:
                chunk_prompt_template = _load_prompt_template(mode, "chunk")
                final_prompt_template = _load_prompt_template(mode, "final")
        except (FileNotFoundError, ValueError) as e:
            return "_Error loading prompt templates. Please check your configuration._"
        print(f"[summarizer] Prompt templates loaded OK", file=sys.stderr, flush=True)

        system_msg = (
            f"Отвечай только на {target_language_name}. "
            "ВАЖНО: в выводе — ТОЛЬКО готовый конспект. "
            "Не включай в вывод ход мыслей, рассуждения, теги <think>/<thinking>/<reasoning>, "
            "внутренний монолог или пояснения к процессу. Начинай сразу с конспекта."
        )

        # MAP phase: summarize each chunk
        chunks = _chunk_segments(segments, config.chunk_size_tokens, config.chunk_overlap_tokens)
        if not chunks:
            return "_No content to summarize._"

        total_chunks = len(chunks)
        print(f"[summarizer] {total_chunks} chunks prepared, starting MAP phase...", file=sys.stderr, flush=True)
        checkpoint_cache: dict[int, str] = {}
        checkpoint_path: Path | None = None
        if config.enable_checkpointing and checkpoint_key:
            checkpoint_path = _build_summary_checkpoint_path(config, checkpoint_key)
            checkpoint_cache = _load_summary_checkpoint(checkpoint_path, total_chunks)
        # #region agent log
        _lp = Path(__file__).parent.parent / "debug-ff5c79.log"
        _dbg_log = lambda msg, data: open(_lp,"a",encoding="utf-8").write(json.dumps({"sessionId":"ff5c79","location":"summarizer.py:checkpoint","message":msg,"data":data,"timestamp":int(__import__('time').time()*1000),"hypothesisId":"H2"},ensure_ascii=False)+"\n")
        _dbg_log("checkpoint_info", {"cache_size": len(checkpoint_cache), "checkpoint_key": checkpoint_key, "checkpointing_enabled": config.enable_checkpointing, "cached_keys": list(checkpoint_cache.keys())[:5]})
        # #endregion

        prompts: dict[int, str] = {}
        for i, chunk in enumerate(chunks):
            prompt = chunk_prompt_template.replace("{{TRANSCRIPT_CHUNK}}", chunk)
            prompt = _substitute_config_variables(prompt, config.summary_mode_config)
            prompt = prompt.replace("{target_language}", target_language_name)
            prompts[i] = prompt

        max_workers = max(1, int(config.summary_max_concurrency))
        if not _owns_llm and max_workers > 1:
            print("[summarizer] WARNING: parallel workers disabled with shared LLM (each worker loads a full model copy)", file=sys.stderr, flush=True)
            max_workers = 1
        chunk_summaries_indexed: dict[int, str] = dict(checkpoint_cache)
        pending_indexes = [i for i in range(total_chunks) if i not in chunk_summaries_indexed]
        # #region agent log
        open(_lp,"a",encoding="utf-8").write(json.dumps({"sessionId":"ff5c79","location":"summarizer.py:map_phase","message":"map_start","data":{"total_chunks":total_chunks,"pending":len(pending_indexes),"cached":len(checkpoint_cache),"chunk_sizes":[len(c) for c in chunks],"prompt_est_tokens":[_estimate_tokens(prompts[i]) for i in range(total_chunks)],"n_ctx":resolved_n_ctx,"chunk_size_config":config.chunk_size_tokens},"timestamp":int(__import__('time').time()*1000),"hypothesisId":"H2-H5"},ensure_ascii=False)+"\n")
        # #endregion

        if pending_indexes:
            cached_count = len(checkpoint_cache)
            if max_workers == 1:
                for pos, i in enumerate(pending_indexes, 1):
                    if progress_cb:
                        progress_cb(
                            int(10 + 60 * (cached_count + pos) / total_chunks),
                            f"Summarizing chunk {i + 1}/{total_chunks}...",
                        )
                    print(f"[summarizer] Chunk {i+1}/{total_chunks}: sending to LLM ({_estimate_tokens(prompts[i])} est. tokens)...", file=sys.stderr, flush=True)
                    chunk_summaries_indexed[i] = _run_chunk_summary(llm, prompts[i], config, system_msg, n_ctx=resolved_n_ctx)
                    print(f"[summarizer] Chunk {i+1}/{total_chunks}: done", file=sys.stderr, flush=True)
                    if checkpoint_path:
                        _save_summary_checkpoint(checkpoint_path, total_chunks, chunk_summaries_indexed)
            else:
                def _parallel_worker(prompt_text: str) -> str:
                    local_llm = Llama(**llm_kwargs)
                    try:
                        return _run_chunk_summary(local_llm, prompt_text, config, system_msg, n_ctx=resolved_n_ctx)
                    finally:
                        del local_llm
                        gc.collect()

                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    future_to_idx = {
                        pool.submit(_parallel_worker, prompts[i]): i for i in pending_indexes
                    }
                    completed = 0
                    for future in as_completed(future_to_idx):
                        idx = future_to_idx[future]
                        chunk_summaries_indexed[idx] = future.result()
                        completed += 1
                        if checkpoint_path:
                            _save_summary_checkpoint(checkpoint_path, total_chunks, chunk_summaries_indexed)
                        if progress_cb:
                            progress_cb(
                                int(10 + 60 * (cached_count + completed) / total_chunks),
                                f"Summarizing chunk {idx + 1}/{total_chunks}...",
                            )

        chunk_summaries = [chunk_summaries_indexed[i] for i in range(total_chunks)]

        # REDUCE phase
        if progress_cb:
            progress_cb(75, "Synthesizing final summary...")

        final_summary = _tree_reduce(
            llm,
            chunk_summaries,
            final_prompt_template,
            config,
            system_msg,
            target_language_name,
            resolved_n_ctx,
            progress_cb,
        )

        if progress_cb:
            progress_cb(100, "Summarization complete")

        return final_summary

    finally:
        if _owns_llm:
            del llm
            gc.collect()
