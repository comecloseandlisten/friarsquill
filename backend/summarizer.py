import gc
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from config import Config
from languages import (
    LANGUAGE_NAMES,
    SummaryAssemblyHeadings,
    get_summary_assembly_headings,
    language_enforcement_block,
    resolve_summary_language,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Whitelist of allowed summary modes
ALLOWED_MODES = {"notes", "call_check", "inquisition", "factcheck", "tldr", "bard"}

# Maximum length for config values to prevent DoS
MAX_CONFIG_VALUE_LENGTH = 10000

# Periodic stderr while blocked inside llama.cpp (native code often releases the GIL).
_LLM_HEARTBEAT_INTERVAL_SEC = 10.0


def _with_llm_heartbeat(label: str, fn, interval: float = _LLM_HEARTBEAT_INTERVAL_SEC):
    """
    Run ``fn()`` while a daemon thread prints heartbeat lines if the call blocks
    longer than ``interval`` seconds (helps diagnose "0% CPU" hangs vs slow work).
    """
    stop = threading.Event()
    started = time.monotonic()

    def _beat():
        while not stop.wait(interval):
            elapsed = time.monotonic() - started
            print(
                f"[summarizer] LLM heartbeat ({label}): {elapsed:.0f}s inside native generate "
                "(if this repeats with flat GPU load: fewer GPU layers in Rubrics, CUDA llama-cpp wheel, driver update)",
                file=sys.stderr,
                flush=True,
            )

    t = threading.Thread(target=_beat, name="summarizer-llm-heartbeat", daemon=True)
    t.start()
    try:
        return fn()
    finally:
        stop.set()
        t.join(timeout=0.5)


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


def _cap_summary_chunk_tokens(budget: int, config: Config) -> int:
    """Apply summary_chunk_max_tokens cap for MAP / intermediate reduce (0 = no cap)."""
    cap = getattr(config, "summary_chunk_max_tokens", 0) or 0
    if cap <= 0:
        return budget
    return min(budget, cap)


# DISABLED 2026-04-17 (Revision 2), still disabled 2026-04-18 (Revision 3):
# the actual root cause of the bard CoT leak was the prompt itself asking
# the model for numbered "Шаг 1 / Шаг 2 / Шаг 3" reasoning. Prompts have
# been rewritten and /no_think is injected in _llm_call; strippers stay
# out of the pipeline unless a real run proves they are still needed.
# Function kept intact as a re-enable option: add "untagged_cot" back to
# _HYGIENE_PIPELINE for the affected mode. Do NOT delete.
def _strip_untagged_leading_cot(text: str) -> str:
    """
    Strip prose chain-of-thought (no XML tags), e.g. 'Thinking Process:\\n1. ...'
    before the first Markdown heading line.  If CoT markers are detected but
    no clean heading is found, return empty string — the entire output is
    reasoning with no usable content.
    """
    if not text:
        return text
    stripped = text.lstrip()
    if len(stripped) < 40:
        return text
    # Normalize markdown bold so "1. **Analyze the Request:**" reads as
    # "1. analyze the request:" — makes marker matching work on dressed-up
    # thinking like "1. **Analyze the Request:**" or "**Step 1:**"
    head = re.sub(r"\*+|_+|`+", "", stripped[:1500]).lower()
    markers = (
        "thinking process",
        "the user wants",
        "the user asked",
        "okay, i need",
        "let me analyze",
        "let me consider",
        "analyze the request",
        "analyzing the request",
        "self-correction",
        "self correction",
        "i need to summarize",
        "my task is",
        "my goal is",
        "first, let me",
        "first, i need",
        "step 1:",
        "step 1 ",
        "1. understand",
        "1. identify",
        "1. analyze",
        "пользователь хочет",
        "пользователь просит",
        "ход размышлений",
        "разберём запрос",
        "моя задача",
        "мне нужно",
        "сначала нужно",
        "шаг 1",
    )
    if not any(m in head for m in markers):
        return text
    # Find the first heading whose text is NOT itself CoT-flavoured
    # ("## Analyze the Request" doesn't count — keep scanning).
    for m in re.finditer(r"(?m)^(#{1,3})\s+(.+?)\s*$", text):
        heading_text = re.sub(r"\*+|_+|`+", "", m.group(2)).strip().lower()
        if not any(heading_text.startswith(mk) or mk in heading_text[:60]
                   for mk in markers):
            if m.start() > 0:
                return text[m.start():].lstrip()
            return text
    # CoT detected but no clean heading → try to salvage content.
    # Strategy 1: content after a blank line (paragraph break)
    para_break = re.search(r"\n\s*\n", text)
    if para_break:
        remainder = text[para_break.end():].strip()
        if remainder and len(remainder) > 20:
            r_head = re.sub(r"\*+|_+|`+", "", remainder[:500]).lower()
            if not any(m in r_head[:200] for m in markers):
                return remainder

    # Strategy 2: scan lines, find last non-CoT lines (no blank-line separator)
    lines = text.split("\n")
    non_cot_lines: list[str] = []
    for line in reversed(lines):
        s = line.strip().lower()
        s_clean = re.sub(r"\*+|_+|`+", "", s)
        if not s:
            if non_cot_lines:
                break  # hit blank line above our content block
            continue
        if any(m in s_clean[:80] for m in markers) or re.match(r"^\d+\.\s", s_clean):
            if non_cot_lines:
                break  # hit CoT above our content block
            continue
        non_cot_lines.append(line)
    if non_cot_lines:
        non_cot_lines.reverse()
        salvaged = "\n".join(non_cot_lines).strip()
        if len(salvaged) > 20:
            return salvaged

    # Long text with no salvageable content → return empty (all CoT).
    # Short text → preserve as-is (might be a false positive on markers).
    if len(text) > 500:
        return ""
    return text


# DISABLED 2026-04-17 (Revision 2), still disabled 2026-04-18 (Revision 3):
# the "1. Analyze / 2. Draft / 3. Refine" leak pattern was caused by the
# bard/notes prompts explicitly instructing numbered step-by-step
# reasoning. Those prompts were rewritten in Revision 3; the stripper
# stays disabled to avoid false positives on legitimate numbered lists.
# Function kept intact as a re-enable option (add "numbered_analysis"
# back to _HYGIENE_PIPELINE for the affected mode). Do NOT delete.
def _strip_numbered_analysis(text: str) -> str:
    """
    Remove numbered analysis steps that thinking models (Qwen, DeepSeek, etc.)
    often leak as their internal reasoning process. Handles patterns like:

        1.  **Analyze the Request:**
            *   **Role:** ...
        2.  **Analyze the Transcript:**
            *   Segment 1: ...

    Also removes inline meta-commentary lines ("Let's refine...",
    "Actually, looking at...", "Wait, ...", "Re-reading Input:", etc.)
    that appear interleaved with content.
    """
    if not text:
        return text

    # Detect numbered-step CoT blocks:  "N.  **Title:**" or "N. Title:"
    # where the title contains analysis/planning vocabulary.
    # Line is a numbered planning step if it starts with "N. **<planning word>…".
    # Do not require the title to end right after ":" — models often continue on the
    # same line: "6. **Final Polish:** Combine into a coherent…".
    _NUMBERED_COT_RE = re.compile(
        r"(?m)^\s*\d+\.\s+\*{0,2}"
        r"(?:Analyz|Refin|Draft|Construct|Select|Polish|Evaluat|Assess|Review|Plan|Revis|"
        r"Determin|Identif|Struct|Content|Final|Outline|Brainstor|Verif|Organiz|Summari|"
        r"Synthe|Self)"
        r"[^#\n]*$"
    )

    # Lines that are clearly internal monologue, not content.
    _META_LINE_STARTS = (
        "let's ", "let me ", "actually,", "actually ", "wait,", "wait ",
        "re-reading", "rereading", "hmm,", "hmm ", "looking at",
        "i need to", "i should", "i will", "i'll", "i can",
        "however, to be", "however, looking", "to be safe",
        "better to", "or better,", "or just",
        "*selection of", "*drafting", "*refining", "*segment mapping",
        "*constructing", "*final polish",
        "drafting:", "*drafting:",
        "since the instruction",
        "давай", "на самом деле", "подождите", "хм,",
    )

    # If the text contains numbered analysis steps, strip them all (scan whole
    # text — CoT often appears after a long valid preamble, so [:2000] misses it).
    _scan = text if len(text) <= 65536 else text[:65536]
    if _NUMBERED_COT_RE.search(_scan):
        # Split text into blocks separated by numbered steps.
        # Keep only blocks that look like actual content.
        lines = text.split("\n")
        cleaned_lines: list[str] = []
        in_cot_block = False
        in_fence = False

        for line in lines:
            stripped_line = line.strip()
            lower_line = re.sub(r"\*+|_+|`+", "", stripped_line).lower().strip()

            # Detect start of numbered CoT step
            if _NUMBERED_COT_RE.match(line):
                in_cot_block = True
                in_fence = False
                continue

            # Detect indented continuation of CoT block (starts with * or spaces+*)
            if in_cot_block and stripped_line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_cot_block and in_fence:
                continue
            if in_cot_block and (
                stripped_line.startswith("*") or
                stripped_line.startswith("-   ") or
                not stripped_line  # blank line continues block
            ):
                continue
            # Deep-indented prose after bullets / fenced examples (still CoT)
            if in_cot_block and re.match(r"^\s{6,}\S", line):
                continue

            # Detect meta-commentary lines
            if any(lower_line.startswith(m) for m in _META_LINE_STARTS):
                continue

            # Reached non-CoT content → stop suppressing
            in_cot_block = False
            in_fence = False
            cleaned_lines.append(line)

        text = "\n".join(cleaned_lines).strip()

    return text


# DISABLED 2026-04-17 (Revision 2), still disabled 2026-04-18 (Revision 3):
# this stripper's false-positive rate against legitimate bullet-labelled
# content (e.g. bard's "**Цитата:** ...", "**Контекст:** ...") was the
# original motivation for its removal. Kept intact for reference only.
# Do NOT add `prompt_echo` back to _HYGIENE_PIPELINE for bard/notes.
def _strip_prompt_instruction_echo(text: str) -> str:
    """Remove leaked system-prompt rubric lines (## Role, **Task:** bullets, etc.).

    Also catches bullet-label prompt echoes where the model, instead of writing
    content, restates its own task as a labelled-bullet list, e.g.:

        *   **Topic:** Based on the chunks, it's about ...
        *   **Format:** Start immediately with `## Topic Title` ...
        *   **Language:** Russian.

    All three of those labels are meta-rubric, not content — the whole bullet
    line is stripped (including the prose after the colon, which is just the
    model's paraphrase of the prompt).

    Indented prose continuation lines directly under a stripped echo bullet
    are also removed (the model keeps elaborating the rubric across lines).
    """
    if not text:
        return text
    # Malformed headings like "## Role:** Note-taker" (prompt echo).
    _echo_heading = re.compile(r"(?i)^\s*#+\s*role\s*(\*\*|:\*+)\s*.*$")
    # Labels that are meta-rubric about the task itself, never real content.
    # Extended to cover common prompt-echo failure modes seen in Qwen/DeepSeek
    # small models on the topic_reduce prompt.
    _echo_label = re.compile(
        r"(?i)^\s*("
        r"role|task|goal|input(\s+text)?|output(\s+format)?|format|language|"
        r"tone|style|structure|subsections?|constraints?|instructions?|rules|"
        r"topic|example(s)?|notes?|settings?|requirements?|process"
        r")\s*:",
    )
    lines = text.split("\n")
    out_lines: list[str] = []
    skip_continuation = False  # drop indented lines that follow a stripped echo bullet

    for line in lines:
        stripped = line.strip()
        if not stripped:
            skip_continuation = False
            out_lines.append(line)
            continue
        if _echo_heading.match(stripped):
            skip_continuation = True
            continue
        # Drop formatting/bullet markers before matching the label.
        collapsed = re.sub(r"[*_`•]+", "", stripped)
        collapsed = re.sub(r"^[-*+]\s+", "", collapsed).strip()
        if _echo_label.match(collapsed):
            skip_continuation = True
            continue
        # Indented continuation of a just-stripped echo bullet:
        #   "    *   **Topic:** ..."       (stripped above)
        #   "        same-paragraph prose"  (drop this too)
        if skip_continuation and re.match(r"^\s{3,}\S", line):
            continue
        lo = re.sub(r"[*_`]+", "", stripped).lower()
        if "no thinking tags" in lo and len(stripped) < 240:
            continue
        skip_continuation = False
        out_lines.append(line)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Per-mode hygiene pipeline registry
# ---------------------------------------------------------------------------
_HYGIENE_PIPELINE = {
    # All modes: thinking-tag stripper first, then numbered English CoT
    # steps (``1. **Analyze the Request:**`` …) via ``_strip_numbered_analysis``.
    # Models still emit that pattern even with /no_think (see video_summary.md);
    # ``prompt_echo`` stays out of this registry to avoid bard/notes false positives.
    "notes":       ("thinking_tags", "numbered_analysis"),
    "tldr":        ("thinking_tags", "numbered_analysis"),
    "call_check":  ("thinking_tags", "numbered_analysis"),
    "factcheck":   ("thinking_tags", "numbered_analysis"),
    "inquisition": ("thinking_tags", "numbered_analysis"),
    "bard":        ("thinking_tags", "numbered_analysis"),
}

# Resolver table: name → callable. Populated after strippers are declared
# (``_strip_thinking_tags`` is defined below); attached at module-load time.
_STRIPPERS: dict = {}


def _apply_hygiene(text: str, mode: str) -> str:
    """
    Apply only the strippers listed in ``_HYGIENE_PIPELINE`` for the given
    mode. Unknown/empty modes fall back to ``thinking_tags`` then
    ``numbered_analysis`` (same as known modes, minus ``prompt_echo``).

    Args:
        text: Raw or partially-processed model output.
        mode: Summary mode (notes / tldr / call_check / factcheck / inquisition / bard).

    Returns:
        Text with mode-appropriate strippers applied in registry order.
    """
    if not text:
        return text
    steps = _HYGIENE_PIPELINE.get(
        (mode or "").lower().strip(),
        ("thinking_tags", "numbered_analysis"),
    )
    t = text
    for step in steps:
        fn = _STRIPPERS.get(step)
        if fn is None:
            continue
        t = fn(t)
        if t is None:
            # A stripper may legitimately return "" (all-CoT detected), but
            # never ``None``; treat None as empty string for safety.
            t = ""
    return t


def _finalize_summary_hygiene(text: str, mode: str = "") -> str:
    """
    Last-pass cleanup on fully assembled markdown (cached chunks + joins).

    Args:
        text: Assembled summary markdown.
        mode: Optional summary mode. If given, use the per-mode pipeline from
              ``_HYGIENE_PIPELINE`` (``thinking_tags`` then ``numbered_analysis``).
              ``prompt_echo`` is not in that registry; the no-mode branch below
              still runs it for legacy callers and unit tests.
    """
    if not text:
        return text
    if mode:
        return (_apply_hygiene(text, mode) or "").strip()
    # No mode (e.g. unit tests, legacy callers): thinking strip, numbered CoT,
    # then prompt-echo cleanup (stricter than the per-mode pipeline).
    t = _strip_thinking_tags(text) or ""
    t = _strip_numbered_analysis(t) or ""
    t = _strip_prompt_instruction_echo(t) or ""
    return t.strip()


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
      * Untagged "Thinking Process:" / "The user wants..." before first ## heading
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

    # 3) Dangling opening tag (no closing): drop from tag onward UNLESS a real
    #    markdown heading appears after (truncated CoT then real answer — bard/final).
    open_m = re.search(rf"<\s*({_THINK_TAGS})\s*>", cleaned, flags=re.IGNORECASE)
    if open_m:
        tag_name = open_m.group(1)
        tail = cleaned[open_m.end():]
        close_pat = rf"<\s*/\s*{re.escape(tag_name)}\s*>"
        if not re.search(close_pat, tail, flags=re.IGNORECASE | re.DOTALL):
            heading_m = re.search(r"(?:^|\n)\s*#{1,6}\s+\S", tail)
            if heading_m:
                cleaned = cleaned[: open_m.start()] + tail[heading_m.start() :]
            else:
                cleaned = cleaned[: open_m.start()]

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

    # 4b) Untagged "Thinking Process:" / "The user wants…" through the line
    #     before the first real markdown heading (even when there is no blank
    #     line — models often skip the double-newline preamble pattern).
    heading_start = re.search(r"(?m)^\s*#{1,6}\s+\S", cleaned)
    if heading_start and heading_start.start() > 0:
        prefix = cleaned[: heading_start.start()]
        pl = prefix.lstrip().lower()
        if pl.startswith("thinking process:") or pl.startswith("the user wants"):
            cleaned = cleaned[heading_start.start() :]

    # NOTE: _strip_untagged_leading_cot() intentionally NOT called here.
    # Revision 2/3 (PLAN-bard-fix.md) disabled it from the pipeline because
    # its marker list ("моя задача", "мне нужно", etc.) produced false
    # positives on legitimate bard/notes content and truncated the final
    # answer. It is still available as a utility and can be re-enabled by
    # adding "untagged_cot" to the relevant mode's _HYGIENE_PIPELINE entry.

    # 5) Strip leading/trailing whitespace artifacts left after removals
    cleaned = cleaned.strip()

    # 6) If the result is empty but original wasn't, return empty to signal
    #    the caller that the model produced only thinking (no usable content).
    return cleaned


# Wire stripper callables into the per-mode registry declared earlier.
# Done here (after all strippers are defined) so the registry can reference
# them without forward-declaration gymnastics.
_STRIPPERS.update({
    "thinking_tags":     _strip_thinking_tags,
    "untagged_cot":      _strip_untagged_leading_cot,
    "numbered_analysis": _strip_numbered_analysis,
    "prompt_echo":       _strip_prompt_instruction_echo,
})


def _detect_repetition_loop(
    text: str,
    min_phrase_len: int = 20,
    max_repeats: int = 3,
    mode: str = "",
    config: Optional[Config] = None,
) -> str:
    """
    Detect and truncate pathological repetition loops in generated text.

    Previous versions of this function treated ANY line repeated >3 times
    across the whole text as a loop. That shredded bard / inquisition output,
    where every moment has identical rubric lines like ``**Force:** ★★★★★``
    or ``**Why it catches attention:** …``. The new strategy only trips on
    true degenerate loops (the same line 4+ times *in a row*) while still
    catching pathological regex-style repetitions.

    Args:
        text: Generated text to check.
        min_phrase_len: Minimum phrase length for Strategy 2 regex detection
            (kept for signature compatibility; not used directly anymore).
        max_repeats: Legacy parameter (kept for signature compatibility).
        mode: Optional summary mode. For ``bard`` and ``inquisition`` the
            function short-circuits and returns the text unchanged — their
            output is *designed* to look repetitive. The ``llm_repeat_penalty``
            at the llama-cpp level already guards against real token loops.
        config: Optional Config instance. If ``config.repetition_guard`` is
            False, the whole function is bypassed.

    Returns:
        Cleaned text, or the original text if no loop is detected / guard
        is disabled.
    """
    if not text:
        return text

    # Mode-level short-circuit — BEFORE reading config, so users who disable
    # the guard for other modes still get the fix for bard/inquisition.
    mode_norm = (mode or "").lower().strip()
    if mode_norm in ("bard", "inquisition"):
        return text

    # Config-level gate (default True).
    if config is not None and not getattr(config, "repetition_guard", True):
        return text

    # Strategy 1: detect CONSECUTIVE runs of the exact same non-blank line.
    # 4+ in a row (ignoring blank separators) = pathological loop; anything
    # less is legitimate structural repetition (bullet labels, headings).
    lines = text.split("\n")
    truncate_at = len(lines)
    run_key: str | None = None
    run_start = -1
    run_count = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            # Blank line does NOT reset the run — models sometimes emit
            # "loop\n\nloop\n\nloop…" with doubled newlines.
            continue
        if stripped == run_key:
            run_count += 1
            if run_count >= 4:
                truncate_at = run_start
                break
        else:
            run_key = stripped
            run_start = i
            run_count = 1

    if truncate_at < len(lines):
        text = "\n".join(lines[:max(truncate_at, 1)])

    # Strategy 2: detect repeated multi-word phrases via regex. Thresholds
    # raised vs. legacy: phrase length ≥40 chars, ≥4 consecutive repeats.
    # Real loops look like this; bard's ``**Force:** ★★★★★`` (27 chars) is
    # now comfortably below the floor.
    pattern = re.compile(r"(.{40,}?)\1{3,}", re.DOTALL)
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


def _safe_prompt_read(path: Path) -> str | None:
    """Read ``path`` only if it resolves inside PROMPTS_DIR; else None."""
    try:
        path.resolve().relative_to(PROMPTS_DIR.resolve())
    except ValueError:
        _log_security("Path traversal attempt: resolved path outside PROMPTS_DIR")
        return None
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _load_prompt_template(mode: str, phase: str, lang_code: str = "") -> str:
    """
    Load prompt template for a given mode, phase, and target language.

    Language-variant selection (most specific first):
        1. ``{phase}.{lang_code}.txt`` — explicit per-language override
        2. ``{phase}.en.txt``          — used for any non-Russian target when
                                         a language-specific file is missing
                                         (prompt bodies in this repo are
                                         Russian, so an English variant is
                                         the strongest default for any other
                                         target language)
        3. ``{phase}.txt``             — original (Russian) default

    Falls back to 'notes' mode if the requested mode is not found, preserving
    the same variant-selection order.

    Args:
        mode: summary mode (notes, call_check, factcheck, tldr, bard, inquisition)
        phase: 'chunk' | 'final' | 'batch_chunk' | 'batch_final' | 'reduce_intermediate'
        lang_code: ISO code of the target summary language (e.g. 'ru', 'en', 'de').
                   Empty string reproduces the legacy Russian-only behaviour.

    Returns:
        Prompt template text
    """
    # Validate and sanitize mode
    mode = _validate_mode(mode)

    # Validate phase
    if phase not in ("chunk", "final", "batch_chunk", "batch_final", "reduce_intermediate"):
        raise ValueError(f"Invalid phase: {phase}")

    code = (lang_code or "").lower().strip()
    mode_dir = PROMPTS_DIR / mode

    # Build candidate list in preference order
    def _candidates_for(dir_: Path) -> list[Path]:
        cands: list[Path] = []
        if code:
            cands.append(dir_ / f"{phase}.{code}.txt")
        if code and code != "ru":
            cands.append(dir_ / f"{phase}.en.txt")
        cands.append(dir_ / f"{phase}.txt")
        return cands

    for candidate in _candidates_for(mode_dir):
        content = _safe_prompt_read(candidate)
        if content is not None:
            return content

    # Fallback to 'notes' mode if requested mode not found
    for candidate in _candidates_for(PROMPTS_DIR / "notes"):
        content = _safe_prompt_read(candidate)
        if content is not None:
            return content

    # Last resort: look for old-style prompts (backward compatibility)
    old_file = PROMPTS_DIR / f"{phase}_summary.txt"
    if old_file.exists():
        return old_file.read_text(encoding="utf-8")

    raise FileNotFoundError(f"Prompt template not found for mode='{mode}', phase='{phase}'")


# ---------------------------------------------------------------------------
# Inquisition mode — programmatic prompt builder.
#
# Inquisition has four user-supplied fields: EVALUATION_GOAL (free text),
# REQUIRED_ITEMS ("green flags"), RED_FLAGS, and SCORING_CRITERIA (lists).
# Per user requirement (2026-04-19): if ALL four are empty, we refuse to run.
# Otherwise the prompt must only include analysis sections that correspond to
# fields the user actually provided — no phantom "Required items — NOT FOUND"
# walls when the user didn't ask about required items, and no scoring table
# when no criteria were supplied.
#
# Output language: Russian only when the target language is Russian; all other
# targets use the English variant. The language_enforcement_block prepended at
# dispatch time pushes the model to the final target language in every case.
# ---------------------------------------------------------------------------


class InquisitionConfigError(ValueError):
    """Raised when inquisition mode is selected but no analysis field is given."""


def _has_inquisition_value(v) -> bool:
    """True iff the given config field carries meaningful user content."""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple)):
        return any(isinstance(x, str) and x.strip() for x in v)
    return bool(str(v).strip())


def _format_inquisition_list(value) -> str:
    """Render a list/str inquisition field as a sanitised bullet list."""
    if isinstance(value, (list, tuple)):
        return "\n".join(
            f"- {x.strip()}" for x in value if isinstance(x, str) and x.strip()
        )
    return (str(value) if value is not None else "").strip()


def _inquisition_fields(cfg: dict) -> dict:
    """Extract and normalise the four inquisition config fields."""
    cfg = cfg if isinstance(cfg, dict) else {}
    raw_goal = cfg.get("EVALUATION_GOAL") or cfg.get("evaluation_goal") or ""
    goal = raw_goal.strip() if isinstance(raw_goal, str) else ""
    return {
        "goal": goal,
        "items": cfg.get("REQUIRED_ITEMS") or cfg.get("required_items") or [],
        "reds": cfg.get("RED_FLAGS") or cfg.get("red_flags") or [],
        "criteria": cfg.get("SCORING_CRITERIA") or cfg.get("scoring_criteria") or [],
    }


def _inquisition_has_any_field(cfg: dict) -> bool:
    """True iff at least one of the four analysis fields is populated."""
    f = _inquisition_fields(cfg)
    return any(
        _has_inquisition_value(f[k]) for k in ("goal", "items", "reds", "criteria")
    )


def _build_inquisition_prompt(
    phase: str,
    cfg: dict,
    target_language_name: str,
    target_language_code: str,
) -> str:
    """
    Build the inquisition prompt for ``phase`` (``chunk`` or ``final``)
    from the user-supplied config. Only emits analysis sections that
    correspond to fields the user actually provided.

    Raises ``InquisitionConfigError`` if all four fields are empty — without
    any of them, there is nothing meaningful to analyse and the pipeline
    must not silently fall back to a generic summary.
    """
    if phase not in ("chunk", "final"):
        raise ValueError(f"Unsupported inquisition phase: {phase!r}")

    fields = _inquisition_fields(cfg)
    goal = fields["goal"]
    items_raw = fields["items"]
    reds_raw = fields["reds"]
    criteria_raw = fields["criteria"]

    has_goal = _has_inquisition_value(goal)
    has_items = _has_inquisition_value(items_raw)
    has_reds = _has_inquisition_value(reds_raw)
    has_criteria = _has_inquisition_value(criteria_raw)

    if not (has_goal or has_items or has_reds or has_criteria):
        raise InquisitionConfigError(
            "Inquisition mode requires at least one of: evaluation goal, "
            "required items, red flags, or scoring criteria."
        )

    ru = (target_language_code or "").lower().strip() == "ru"

    items_txt = _format_inquisition_list(items_raw) if has_items else ""
    reds_txt = _format_inquisition_list(reds_raw) if has_reds else ""
    criteria_txt = _format_inquisition_list(criteria_raw) if has_criteria else ""

    if ru:
        return _build_inquisition_prompt_ru(
            phase,
            goal, has_goal,
            items_txt, has_items,
            reds_txt, has_reds,
            criteria_txt, has_criteria,
            target_language_name,
        )
    return _build_inquisition_prompt_en(
        phase,
        goal, has_goal,
        items_txt, has_items,
        reds_txt, has_reds,
        criteria_txt, has_criteria,
        target_language_name,
    )


def _build_inquisition_prompt_en(
    phase: str,
    goal: str, has_goal: bool,
    items_txt: str, has_items: bool,
    reds_txt: str, has_reds: bool,
    criteria_txt: str, has_criteria: bool,
    target_language_name: str,
) -> str:
    lines: list[str] = [
        "You are a top-tier expert analyst. Run an EXHAUSTIVE, not selective, "
        "analysis of the material — cover everything the user asked for, and only "
        "what the user asked for.",
        "",
    ]
    if has_goal:
        lines += ["**Evaluation goal:**", goal, ""]
    if has_items:
        lines += ["**Required items to find (green flags):**", items_txt, ""]
    if has_reds:
        lines += ["**Red flags / warnings:**", reds_txt, ""]
    if phase == "final" and has_criteria:
        lines += ["**Scoring criteria:**", criteria_txt, ""]

    if phase == "chunk":
        lines += ["**Transcript chunk:**", "{{TRANSCRIPT_CHUNK}}", ""]
    else:
        lines += ["**Per-chunk analyses:**", "{{CHUNK_SUMMARIES}}", ""]

    # Analysis requirements — always emitted, but phrased to match scope
    lines += [
        "ANALYSIS REQUIREMENTS:",
        "",
        "1. FULL TIMELINE COVERAGE. Walk through the material from start to end. "
        "Findings must not cluster only in one part — search across all time spans.",
        "2. EXHAUSTIVE LIST. List EVERY occurrence — not just \"representative examples\". "
        "Each occurrence gets its own bullet with its own [M:SS] timecode.",
        "3. EXACT TIMECODES. Use concrete [M:SS] markers taken from the transcript, "
        "never approximations. If one item repeats — list ALL of its timecodes.",
        "4. VERBATIM QUOTES. Every finding carries a short (5-15 word) verbatim quote in quotes.",
        "5. NO FABRICATION. If an item or flag is not present, write NOT FOUND for that item. "
        "Do not invent or stretch.",
        "",
        "OUTPUT FORMAT:",
        "",
    ]

    section_blocks: list[str] = []

    if phase == "chunk":
        if has_items:
            section_blocks.append(
                "### Required items (ALL findings)\n"
                "For EACH item in the list above:\n"
                "- \"item name\": [M:SS] \"quote\" — brief explanation\n"
                "- \"item name\": [M:SS] \"quote\" — brief explanation\n"
                "  (list ALL occurrences, not just one)\n"
                "- \"item name\": NOT FOUND"
            )
        if has_reds:
            section_blocks.append(
                "### Red flags (ALL findings)\n"
                "For EACH red flag detected:\n"
                "- \"flag type\": [M:SS] \"quote\" — explanation of the problem\n"
                "- \"flag type\": [M:SS] \"quote\" — explanation of the problem\n"
                "  (list ALL occurrences)"
            )
        if has_goal and not (has_items or has_reds):
            # Only a free-form goal was given — do open-ended goal-oriented findings.
            section_blocks.append(
                "### Findings relevant to the evaluation goal\n"
                "- [M:SS] \"quote\" — observation tied to the goal above\n"
                "  (list ALL relevant moments, in chronological order)"
            )
        section_blocks.append(
            "### Additional observations relevant to the evaluation goal\n"
            "- Rhetorical devices and structural features with timecodes\n"
            "- Patterns repeating across the whole chunk\n"
            "- How tone / argumentation evolves from start to end of the chunk"
        )
        stat_parts: list[str] = []
        if has_items:
            stat_parts.append("- Required items found: N")
        if has_reds:
            stat_parts.append("- Red flags found: N")
        stat_parts.append("- Temporal coverage of findings: [M:SS] — [M:SS]")
        section_blocks.append("### Chunk stats\n" + "\n".join(stat_parts))
    else:  # final
        header_lines = ["## Content evaluation", ""]
        if has_goal:
            header_lines += ["**Evaluation goal:** [repeat the goal verbatim]"]
        if has_criteria:
            header_lines += ["**Overall score:** X.X/10"]
        header_lines += [
            "**Temporal coverage of analysis:** [M:SS] — [M:SS] (full duration of the material)",
            "**Finding density:** high / medium / low (with a short note)",
            "",
            "### Executive summary",
            "3-5 sentences: what this material is, its dominant rhetorical / substantive "
            "strategy, which findings matter most.",
        ]
        section_blocks.append("\n".join(header_lines))

        if has_items:
            section_blocks.append(
                "### Required items — all findings\n"
                "For EACH item from the required list:\n\n"
                "**✅ \"item name\"** (found N times):\n"
                "- [M:SS] \"verbatim quote\" — brief explanation\n"
                "- [M:SS] \"verbatim quote\" — brief explanation\n"
                "  (list ALL occurrences from every chunk)\n\n"
                "**❌ \"item name\"** — NOT FOUND"
            )
        if has_reds:
            section_blocks.append(
                "### Red flags — all findings\n"
                "For EACH type of red flag:\n\n"
                "**⚠️ \"flag type\"** (found N times):\n"
                "- [M:SS] \"verbatim quote\" — explanation of the problem and its impact\n"
                "- [M:SS] \"verbatim quote\" — explanation\n"
                "  (list ALL occurrences)\n\n"
                "If no flags of this type are present: **✅ \"flag type\"** — not found."
            )
        if has_goal and not (has_items or has_reds):
            section_blocks.append(
                "### Findings relevant to the evaluation goal\n"
                "- [M:SS] \"verbatim quote\" — observation tied to the goal\n"
                "  (list ALL relevant moments in chronological order across chunks)"
            )
        section_blocks.append(
            "### Rhetorical / structural patterns\n"
            "Patterns spanning multiple chunks:\n"
            "- Pattern name — [M:SS], [M:SS], [M:SS] — description\n"
            "- Pattern name — [M:SS], [M:SS] — description"
        )
        if has_criteria:
            section_blocks.append(
                "### Scoring by criterion\n"
                "For EACH criterion from the list:\n\n"
                "**criterion_name: X/10**\n"
                "- 2-3 sentences of justification\n"
                "- Supporting timecodes: [M:SS], [M:SS], [M:SS]"
            )
        section_blocks.append(
            "### Evolution of the material\n"
            "How tone / argumentation / intensity shifts from start to end:\n"
            "- Start [0:00—N:NN]: characterisation\n"
            "- Middle [N:NN—M:MM]: characterisation\n"
            "- End [M:MM—end]: characterisation"
        )
        section_blocks.append(
            "### Recommendations\n"
            "3-5 concrete recommendations grounded in the findings above — each "
            "tied to specific timecodes."
        )
        section_blocks.append(
            "### Analysis limitations\n"
            "State honestly if:\n"
            "- Findings cluster in one part (and why)\n"
            "- Some " + (
                "required items" if has_items else "requested checks"
            ) + " could not be verified\n"
            "- Sections of the material were not covered"
        )
        section_blocks.append(
            "### Final verdict\n"
            "One dense paragraph (5-8 sentences): key conclusions, strengths and "
            "weaknesses, the central evaluative thesis."
        )

    lines.append("\n\n".join(section_blocks))
    lines.append("")
    lines.append(
        f"CRITICAL: Output ONLY the final result. Do NOT include thinking, reasoning, "
        f"<think>/<thinking>/<reasoning> tags, or inner monologue. Start directly with "
        f"the content."
    )
    lines.append(
        f"IMPORTANT: Write your entire response in {target_language_name}. Do not mix languages."
    )
    return "\n".join(lines)


def _build_inquisition_prompt_ru(
    phase: str,
    goal: str, has_goal: bool,
    items_txt: str, has_items: bool,
    reds_txt: str, has_reds: bool,
    criteria_txt: str, has_criteria: bool,
    target_language_name: str,
) -> str:
    lines: list[str] = [
        "Вы — эксперт-аналитик высшего уровня. Проведите ИСЧЕРПЫВАЮЩИЙ, а не "
        "выборочный анализ материала — покройте всё, о чём попросил пользователь, "
        "и только это.",
        "",
    ]
    if has_goal:
        lines += ["**Цель оценки:**", goal, ""]
    if has_items:
        lines += ["**Что искать (обязательные элементы / зелёные флаги):**", items_txt, ""]
    if has_reds:
        lines += ["**Красные флаги / предупреждения:**", reds_txt, ""]
    if phase == "final" and has_criteria:
        lines += ["**Критерии оценки:**", criteria_txt, ""]

    if phase == "chunk":
        lines += ["**Фрагмент транскрипта:**", "{{TRANSCRIPT_CHUNK}}", ""]
    else:
        lines += ["**Суммаризированные анализы фрагментов:**", "{{CHUNK_SUMMARIES}}", ""]

    lines += [
        "ТРЕБОВАНИЯ К АНАЛИЗУ:",
        "",
        "1. ПОЛНОЕ ПОКРЫТИЕ ТАЙМЛАЙНА. Пройдитесь по материалу от начала до конца. "
        "Находки не должны концентрироваться только в одной части — ищите во всех "
        "временных промежутках.",
        "2. ИСЧЕРПЫВАЮЩИЙ СПИСОК. Перечисляйте ВСЕ вхождения — не ограничиваясь "
        "«представительными примерами». Каждое вхождение — отдельным пунктом со "
        "своим таймкодом [M:SS].",
        "3. ТОЧНЫЕ ТАЙМКОДЫ. Используйте конкретные метки [M:SS] из транскрипта, "
        "без приблизительности. Если один элемент повторяется — перечислите ВСЕ "
        "его таймкоды.",
        "4. ДОСЛОВНЫЕ ЦИТАТЫ. Каждая находка — с короткой (5-15 слов) дословной "
        "цитатой в кавычках.",
        "5. БЕЗ ДОМЫСЛОВ. Если элемента или флага нет в материале — пишите "
        "«НЕ ОБНАРУЖЕНО». Не выдумывайте и не натягивайте.",
        "",
        "ФОРМАТ ВЫВОДА:",
        "",
    ]

    section_blocks: list[str] = []

    if phase == "chunk":
        if has_items:
            section_blocks.append(
                "### Обязательные элементы (ВСЕ находки)\n"
                "Для КАЖДОГО элемента из списка выше:\n"
                "- \"название элемента\": [M:SS] \"цитата\" — краткое пояснение\n"
                "- \"название элемента\": [M:SS] \"цитата\" — краткое пояснение\n"
                "  (перечислите ВСЕ вхождения, а не одно)\n"
                "- \"название элемента\": НЕ ОБНАРУЖЕНО"
            )
        if has_reds:
            section_blocks.append(
                "### Красные флаги (ВСЕ находки)\n"
                "Для КАЖДОГО обнаруженного красного флага:\n"
                "- \"тип флага\": [M:SS] \"цитата\" — объяснение проблемы\n"
                "- \"тип флага\": [M:SS] \"цитата\" — объяснение проблемы\n"
                "  (перечислите ВСЕ вхождения)"
            )
        if has_goal and not (has_items or has_reds):
            section_blocks.append(
                "### Находки по цели оценки\n"
                "- [M:SS] \"цитата\" — наблюдение, связанное с целью выше\n"
                "  (перечислите ВСЕ релевантные моменты в хронологическом порядке)"
            )
        section_blocks.append(
            "### Дополнительные наблюдения по цели оценки\n"
            "- Риторические приёмы и структурные особенности с таймкодами\n"
            "- Паттерны, повторяющиеся через весь фрагмент\n"
            "- Эволюция тона/аргументации от начала к концу фрагмента"
        )
        stat_parts: list[str] = []
        if has_items:
            stat_parts.append("- Всего найдено обязательных элементов: N")
        if has_reds:
            stat_parts.append("- Всего найдено красных флагов: N")
        stat_parts.append("- Временной охват находок: [M:SS] — [M:SS]")
        section_blocks.append("### Статистика фрагмента\n" + "\n".join(stat_parts))
    else:
        header_lines = ["## Оценка контента", ""]
        if has_goal:
            header_lines += ["**Цель оценки:** [дословно повторите цель оценки]"]
        if has_criteria:
            header_lines += ["**Итоговая оценка:** X.X/10"]
        header_lines += [
            "**Временной охват анализа:** [M:SS] — [M:SS] (полная длительность материала)",
            "**Плотность находок:** высокая / средняя / низкая (с пояснением)",
            "",
            "### Краткое резюме оценки",
            "3-5 предложений: что это за материал, какова его основная риторическая/"
            "содержательная стратегия, какие самые значимые находки.",
        ]
        section_blocks.append("\n".join(header_lines))

        if has_items:
            section_blocks.append(
                "### Обязательные элементы — все находки\n"
                "Для КАЖДОГО элемента из списка требуемых:\n\n"
                "**✅ \"название элемента\"** (найдено N раз):\n"
                "- [M:SS] \"дословная цитата\" — краткое пояснение\n"
                "- [M:SS] \"дословная цитата\" — краткое пояснение\n"
                "  (перечислите ВСЕ вхождения из всех фрагментов)\n\n"
                "**❌ \"название элемента\"** — НЕ ОБНАРУЖЕНО"
            )
        if has_reds:
            section_blocks.append(
                "### Красные флаги — все находки\n"
                "Для КАЖДОГО типа красного флага:\n\n"
                "**⚠️ \"тип флага\"** (найдено N раз):\n"
                "- [M:SS] \"дословная цитата\" — объяснение проблемы и её воздействия\n"
                "- [M:SS] \"дословная цитата\" — объяснение\n"
                "  (перечислите ВСЕ вхождения)\n\n"
                "Если флагов данного типа нет: **✅ \"тип флага\"** — не обнаружено."
            )
        if has_goal and not (has_items or has_reds):
            section_blocks.append(
                "### Находки по цели оценки\n"
                "- [M:SS] \"дословная цитата\" — наблюдение, связанное с целью\n"
                "  (перечислите ВСЕ релевантные моменты в хронологическом порядке по всем фрагментам)"
            )
        section_blocks.append(
            "### Риторические/структурные паттерны\n"
            "Паттерны, прослеживающиеся через несколько фрагментов:\n"
            "- Название паттерна — [M:SS], [M:SS], [M:SS] — описание\n"
            "- Название паттерна — [M:SS], [M:SS] — описание"
        )
        if has_criteria:
            section_blocks.append(
                "### Оценка по критериям\n"
                "Для КАЖДОГО критерия из списка:\n\n"
                "**Название_критерия: X/10**\n"
                "- Обоснование в 2-3 предложениях\n"
                "- Подтверждающие таймкоды: [M:SS], [M:SS], [M:SS]"
            )
        section_blocks.append(
            "### Эволюция материала\n"
            "Как меняется тон/аргументация/интенсивность от начала к концу:\n"
            "- Начало [0:00—N:NN]: характеристика\n"
            "- Середина [N:NN—M:MM]: характеристика\n"
            "- Конец [M:MM—конец]: характеристика"
        )
        section_blocks.append(
            "### Рекомендации\n"
            "3-5 конкретных рекомендаций на основе находок выше. Каждая — с "
            "привязкой к конкретным таймкодам."
        )
        section_blocks.append(
            "### Ограничения анализа\n"
            "Честно укажите, если:\n"
            "- Находки сконцентрированы в одной части (и почему)\n"
            "- Некоторые " + (
                "обязательные элементы" if has_items else "запрошенные проверки"
            ) + " не удалось проверить\n"
            "- Части материала не покрыты анализом"
        )
        section_blocks.append(
            "### Итоговый вывод\n"
            "Один плотный абзац (5-8 предложений): ключевые выводы, сильные и "
            "слабые стороны, главная оценочная мысль."
        )

    lines.append("\n\n".join(section_blocks))
    lines.append("")
    lines.append(
        "CRITICAL: Output ONLY the final result. Do NOT include thinking, reasoning, "
        "<think>/<thinking>/<reasoning> tags, or inner monologue. Start directly with "
        "the content."
    )
    lines.append(
        f"IMPORTANT: Write your entire response in {target_language_name}. Do not mix languages."
    )
    return "\n".join(lines)


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
        "evaluation_goal", "required_items",  # Inquisition mode variables (lowercase)
        "EVALUATION_GOAL", "REQUIRED_ITEMS", "RED_FLAGS", "SCORING_CRITERIA",  # Inquisition (uppercase from frontend)
        "HIGHLIGHT_FOCUS", "HIGHLIGHT_COUNT",
        "INCLUDE_TIMESTAMPS", "INCLUDE_QUOTES",  # Bard mode variables
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


def _format_timecode(seconds: float) -> str:
    """Format seconds as [H:MM:SS] or [M:SS] — matches bard prompt expectations."""
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"[{h}:{m:02d}:{s:02d}]"
    return f"[{m}:{s:02d}]"


def _chunk_segments(
    segments: list[dict],
    chunk_size: int = 1500,
    overlap: int = 200,
    include_timestamps: bool = False,
) -> list[str]:
    """Split segments into text chunks of ~chunk_size tokens with overlap.

    When ``include_timestamps`` is True, each segment line is prefixed with a
    ``[M:SS]`` (or ``[H:MM:SS]`` for long videos) marker so that downstream
    summary prompts (notably bard mode) can cite real timecodes instead of
    hallucinating them. Default False keeps existing modes unchanged.
    """
    chunks = []
    current_chunk = []
    current_tokens = 0

    for seg in segments:
        text = (seg.get("text") or "").strip()
        if include_timestamps:
            ts = _format_timecode(seg.get("start", 0))
            line = f"{ts} {text}" if text else ts
        else:
            line = text
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


def _format_chunk_summaries(summaries: list[str]) -> str:
    return "\n\n---\n\n".join([f"## Chunk {i + 1}\n{s}" for i, s in enumerate(summaries)])


# Module-level latch so the disable_thinking banner is logged once per
# process, not on every _llm_call invocation.
_NO_THINK_LOGGED = False


def _config_suggests_qwen_native_no_think(config: Config) -> bool:
    """
    Heuristic: Qwen3 GGUF chat templates honor the ``/no_think`` user-turn switch.
    Other families ignore it or echo it. Match on path / file / repo / preset.
    """
    parts = [
        str(getattr(config, "llm_model_path", None) or ""),
        str(getattr(config, "llm_model_file", "") or ""),
        str(getattr(config, "llm_model_repo", "") or ""),
        str(getattr(config, "llm_model_preset", None) or ""),
    ]
    return "qwen" in " ".join(parts).lower()


def _system_supplement_disable_thinking(system_msg: str) -> str:
    """Extra system-line for non-Qwen models when disable_thinking is on."""
    hint = (system_msg or "").lower()
    if "отвечай" in hint or "важно:" in hint or "только на" in hint:
        extra = (
            " Не используй теги <thinking>, <think>, <reasoning> и не выводи "
            "ход рассуждений — только готовый результат в запрошенном формате."
        )
    else:
        extra = (
            " Do not use <thinking>, <think>, or <reasoning> tags and do not "
            "output chain-of-thought — only the final answer in the requested format."
        )
    return (system_msg or "").rstrip() + extra


def _invoke_chat_completion_dispatch(
    llm,
    messages: list,
    *,
    temperature: float,
    max_tokens: int,
    repeat_penalty: float,
    jinja_template_extras: dict | None,
    stream: bool = False,
):
    """
    Run chat completion the same way ``Llama.create_chat_completion`` does, but
    allow extra kwargs (e.g. ``enable_thinking=False``) to reach the Jinja
    formatter — ``create_chat_completion`` does not forward ``**kwargs`` upstream.

    For non-``Llama`` instances (tests using ``MagicMock``), fall back to
    ``create_chat_completion`` so existing mocks keep working.

    When ``stream=True``, returns the handler iterator (OpenAI-style chunk dicts);
    when ``stream=False``, returns a single completion dict.
    """
    try:
        from unittest.mock import MagicMock as _MagicMock  # noqa: WPS433
    except ImportError:
        _MagicMock = type(None)  # type: ignore

    try:
        from llama_cpp import Llama as _LlamaCls  # noqa: WPS433
    except ImportError:
        _LlamaCls = None  # type: ignore

    # Tests patch ``Llama`` with ``MagicMock``; never route those through the
    # internal handler (needs a real ``llama_cpp.Llama`` instance).
    if (
        _LlamaCls is None
        or isinstance(llm, _MagicMock)
        or not isinstance(llm, _LlamaCls)
    ):
        kwargs = dict(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            repeat_penalty=repeat_penalty,
            stream=stream,
        )
        if jinja_template_extras:
            try:
                return llm.create_chat_completion(**kwargs, **jinja_template_extras)
            except Exception as exc:
                print(
                    f"[summarizer] mock path: jinja extras {jinja_template_extras!r} rejected "
                    f"({exc!r}); retrying without",
                    file=sys.stderr,
                    flush=True,
                )
                return llm.create_chat_completion(**kwargs)
        return llm.create_chat_completion(**kwargs)

    import llama_cpp.llama_chat_format as llama_chat_format  # noqa: WPS433

    handler = (
        llm.chat_handler
        or llm._chat_handlers.get(llm.chat_format)
        or llama_chat_format.get_chat_completion_handler(llm.chat_format)
    )
    hc: dict = dict(
        llama=llm,
        messages=messages,
        functions=None,
        function_call=None,
        tools=None,
        tool_choice=None,
        temperature=temperature,
        top_p=0.95,
        top_k=40,
        min_p=0.05,
        typical_p=1.0,
        stream=stream,
        stop=[],
        seed=None,
        response_format=None,
        max_tokens=max_tokens,
        presence_penalty=0.0,
        frequency_penalty=0.0,
        repeat_penalty=repeat_penalty,
        tfs_z=1.0,
        mirostat_mode=0,
        mirostat_tau=5.0,
        mirostat_eta=0.1,
        model=None,
        logits_processor=None,
        grammar=None,
        logit_bias=None,
        logprobs=None,
        top_logprobs=None,
    )
    if jinja_template_extras:
        hc.update(jinja_template_extras)
        try:
            return handler(**hc)
        except Exception as exc:
            # Older / non-Qwen templates may not declare ``enable_thinking``.
            print(
                f"[summarizer] chat handler extras {jinja_template_extras!r} rejected "
                f"({exc!r}); retrying without",
                file=sys.stderr,
                flush=True,
            )
            for k in jinja_template_extras:
                hc.pop(k, None)
            return handler(**hc)
    return handler(**hc)


def _llm_call(llm, prompt: str, config: Config, system_msg: str = "", max_tokens: int = 4096) -> str:
    """
    Single LLM call with optional chain-of-thought suppression and hygiene.

    Flow:
      1. Build messages. If ``config.disable_thinking`` is True (default) and
         the configured model looks Qwen-family, append ``/no_think`` to the
         user turn (soft switch) **and** pass ``enable_thinking=False`` into the
         GGUF Jinja chat formatter (hard switch — see llama.cpp Qwen3 template
         kwargs). Otherwise append an anti-CoT line to the system message for
         non-Qwen models.
      2. Dispatch chat completion (handler path for real ``Llama``, mock path
         unchanged).
      3. Run hygiene (``_strip_thinking_tags``) and ``_detect_repetition_loop``.

    See ``docs/PLAN-bard-fix.md`` for ``/no_think``; llama.cpp discussion on
    ``enable_thinking`` / ``chat_template_kwargs``.
    """
    global _NO_THINK_LOGGED

    mode = getattr(config, "summary_mode", "") or ""
    disable_thinking = bool(getattr(config, "disable_thinking", True))
    use_qwen_no_think = disable_thinking and _config_suggests_qwen_native_no_think(config)

    if disable_thinking and not _NO_THINK_LOGGED:
        if use_qwen_no_think:
            print(
                "[summarizer] disable_thinking=True — Qwen-style model: /no_think on user turn "
                "and enable_thinking=false for GGUF Jinja template",
                file=sys.stderr, flush=True,
            )
        else:
            print(
                "[summarizer] disable_thinking=True — non-Qwen model: system anti-CoT supplement "
                "(no /no_think in user text)",
                file=sys.stderr, flush=True,
            )
        _NO_THINK_LOGGED = True

    user_content = prompt.rstrip()
    effective_system = system_msg
    if disable_thinking:
        if use_qwen_no_think:
            user_content = user_content + "\n\n/no_think"
        else:
            effective_system = _system_supplement_disable_thinking(system_msg)

    messages = []
    if effective_system:
        messages.append({"role": "system", "content": effective_system})
    messages.append({"role": "user", "content": user_content})

    # Clear llama.cpp KV / decode state between completions. Without this,
    # sequential calls on the same Llama instance (MAP chunks, re-summarize
    # runs) can leak prior context and re-enable chain-of-thought behaviour.
    reset_fn = getattr(llm, "reset", None)
    if callable(reset_fn):
        try:
            reset_fn()
        except Exception as exc:
            print(f"[summarizer] llm.reset() failed (continuing): {exc}", file=sys.stderr, flush=True)

    jinja_extras = None
    if disable_thinking and use_qwen_no_think:
        jinja_extras = {"enable_thinking": False}

    print(
        f"[summarizer] LLM chat_completion: entering native generate "
        f"(max_tokens={max_tokens}, messages={len(messages)})",
        file=sys.stderr,
        flush=True,
    )
    response = _with_llm_heartbeat(
        "chat_completion",
        lambda: _invoke_chat_completion_dispatch(
            llm,
            messages,
            temperature=config.llm_temperature,
            max_tokens=max_tokens,
            repeat_penalty=config.llm_repeat_penalty,
            jinja_template_extras=jinja_extras,
        ),
    )
    print("[summarizer] LLM chat_completion: native generate returned", file=sys.stderr, flush=True)
    raw_model_output = response["choices"][0]["message"]["content"] or ""
    before_len = len(raw_model_output)

    cleaned = _apply_hygiene(raw_model_output, mode) or ""
    after_len = len(cleaned)
    if before_len != after_len:
        print(
            f"[summarizer] Stripped thinking: {before_len} → {after_len} chars "
            f"(mode={mode!r})",
            file=sys.stderr, flush=True,
        )

    return _detect_repetition_loop(cleaned, mode=mode, config=config)


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
    intermediate_template: str | None = None,
    target_language_code: str = "",
) -> str:
    """
    Hierarchical tree-reduce: if combined summaries exceed the context window,
    split into groups, reduce each group, then recursively reduce the results.
    """
    combined = _format_chunk_summaries(chunk_summaries)
    prompt = reduce_template.replace("{{CHUNK_SUMMARIES}}", combined)
    prompt = _substitute_config_variables(prompt, config.summary_mode_config)
    prompt = prompt.replace("{target_language}", target_language)
    prompt = language_enforcement_block(target_language, target_language_code) + prompt

    est_tokens = _estimate_tokens(prompt)
    # Splitting decision: reserve space so the *prompt* does not eat the whole
    # window — max_tokens=-1 only fills what remains; bard/final.txt needs
    # thousands of tokens for a full ballad + verdict.
    mode_lower = (getattr(config, "summary_mode", "") or "").lower().strip()
    if mode_lower == "bard":
        gen_reserve = max(3072, int(n_ctx * 0.28))
        max_input = max(1, n_ctx - gen_reserve)
    else:
        max_input = max(1, n_ctx - 2048)

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
    tmpl = intermediate_template or reduce_template
    for gi, group in enumerate(groups):
        g_combined = _format_chunk_summaries(group)
        g_prompt = tmpl.replace("{{CHUNK_SUMMARIES}}", g_combined)
        g_prompt = _substitute_config_variables(g_prompt, config.summary_mode_config)
        g_prompt = g_prompt.replace("{target_language}", target_language)
        g_prompt = language_enforcement_block(target_language, target_language_code) + g_prompt

        from calibrate import calibrate_generation_budget
        g_budget = calibrate_generation_budget(n_ctx, _estimate_tokens(g_prompt))
        g_budget = _cap_summary_chunk_tokens(g_budget, config)
        # Intermediate bard merges are compact; use remaining context for
        # generation so tree levels are not truncated mid-candidate-list.
        gen_tokens = -1 if intermediate_template else g_budget
        g_summary = _llm_call(llm, g_prompt, config, system_msg, max_tokens=gen_tokens)
        intermediate.append(g_summary)

        if progress_cb:
            progress_cb(
                75 + int(20 * (gi + 1) / len(groups) / (depth + 2)),
                f"Reducing group {gi + 1}/{len(groups)} (level {depth + 1})...",
            )

    return _tree_reduce(
        llm, intermediate, reduce_template, config, system_msg,
        target_language, n_ctx, progress_cb, depth + 1, intermediate_template,
        target_language_code=target_language_code,
    )


def _run_chunk_summary(llm, prompt: str, config: Config, system_msg: str = "", n_ctx: int = 8192) -> str:
    from calibrate import calibrate_generation_budget
    budget = calibrate_generation_budget(n_ctx, _estimate_tokens(prompt))
    budget = _cap_summary_chunk_tokens(budget, config)
    return _llm_call(llm, prompt, config, system_msg, max_tokens=budget)


# ---------------------------------------------------------------------------
# Topic segmentation: vocabulary-shift boundary detection (ZERO LLM calls),
# merging, calibration, per-topic MAP-REDUCE, assembly
# ---------------------------------------------------------------------------

def _shared_prompts_dir() -> Path:
    """Subdir for modular topic prompts; resolves at call time so tests can patch PROMPTS_DIR."""
    return PROMPTS_DIR / "_shared"


def _shared_prompt_path(name: str, lang_code: str) -> Path:
    """
    Pick the best-matching shared prompt file for the target language.

    Resolution order (same convention as :func:`_load_prompt_template`):
        1. ``{name}.{lang_code}.txt``
        2. ``{name}.en.txt``  (non-Russian targets only)
        3. ``{name}.txt``     (Russian default, always-present fallback)
    """
    base = _shared_prompts_dir()
    code = (lang_code or "").lower().strip()
    if code:
        specific = base / f"{name}.{code}.txt"
        if specific.exists():
            return specific
    if code and code != "ru":
        english = base / f"{name}.en.txt"
        if english.exists():
            return english
    return base / f"{name}.txt"

# Regex for extracting meaningful words (3+ chars, Unicode-aware)
_WORD_RE_TOPIC = re.compile(r'\b\w{3,}\b', re.UNICODE)

# Common stopwords (Russian + English) — excluded from vocabulary analysis
_STOPWORDS = frozenset({
    # Russian — short words & particles
    "и", "в", "на", "не", "с", "а", "но", "да", "от", "по", "за", "к",
    "из", "у", "о", "же", "то", "бы", "ли", "ну", "он", "мы", "вы", "ты", "я",
    # Russian — pronouns, conjunctions, common verbs/adverbs
    "что", "как", "это", "так", "все", "для", "его", "она", "они", "мне",
    "мой", "моя", "тут", "там", "вот", "уже", "ещё", "или", "если", "тоже",
    "только", "может", "будет", "очень", "когда", "потому", "потом", "просто",
    "себя", "свой", "того", "чтобы", "этого", "есть", "было", "были", "быть",
    "который", "которая", "которые", "которое", "какой", "какая", "какие",
    "нужно", "можно", "надо", "более", "менее", "между", "через", "после",
    "перед", "около", "кроме", "самый", "самая", "самое", "каждый", "каждая",
    "другой", "другая", "другие", "такой", "такая", "такие", "ничего",
    "говорит", "говорю", "делать", "сделать", "знаю", "знает", "думаю",
    # English — articles, prepositions, pronouns, common verbs
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "must", "i", "you", "he",
    "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
    "your", "his", "its", "our", "their", "this", "that", "these", "those",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "up",
    "about", "into", "over", "after", "and", "but", "or", "not", "no",
    "so", "if", "then", "than", "too", "very", "just", "also", "as",
    "what", "when", "where", "who", "which", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such",
    "only", "know", "like", "think", "thing", "things", "really",
    "going", "well", "right", "actually", "because", "there", "here",
    "much", "many", "even", "still", "back", "get", "got", "say", "said",
    "one", "two", "first", "new", "way", "now", "people", "time",
})


def _extract_word_set(text: str) -> set[str]:
    """Extract set of meaningful words (3+ chars, no stopwords)."""
    return set(_WORD_RE_TOPIC.findall(text.lower())) - _STOPWORDS




def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity coefficient between two word sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


_COT_LINE_MARKERS = (
    "analyze", "self-correction", "self correction",
    "the user", "the task", "my task", "my goal",
    "first, let me", "first, i", "step 1", "step 2", "step 3",
    "understand the", "identify the", "extract the",
    "thinking", "reasoning",
    "пользователь", "моя задача", "мне нужно",
    "сначала", "шаг 1", "шаг 2", "разберём",
    "ход размышлений", "анализ запроса",
)


def _looks_like_cot(line: str) -> bool:
    """Return True if a line looks like chain-of-thought reasoning, not content."""
    s = line.strip().lower()
    # Strip leading markdown noise: numbers, bullets, bold markers
    s = re.sub(r"^[\d\.\)\*\-\s_]+", "", s)
    s = s.lstrip("*_ ").rstrip("*_: ")
    if not s:
        return True
    return any(s.startswith(m) or m in s[:60] for m in _COT_LINE_MARKERS)


def _extract_first_heading(text: str) -> str:
    """
    Extract the topic heading from a chunk summary.

    Strategy: scan ALL lines looking for the first real ``## Heading``.
    Ignores chain-of-thought reasoning lines (numbered steps, "Analyze the
    Request", etc.) that thinking models sometimes leak before the heading.
    Falls back to the first content-looking non-CoT line only if no heading
    exists anywhere in the text.
    """
    if not text:
        return ""
    lines = text.split("\n")

    # Pass 1: find first ## heading anywhere
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", stripped)
        if m:
            candidate = m.group(1).strip().strip("*_ ")
            # Reject CoT-styled "headings" like "## Analyze the Request"
            if candidate and not _looks_like_cot(candidate):
                return candidate

    # Pass 2: first non-CoT, non-empty line
    for line in lines:
        stripped = line.strip()
        if not stripped or _looks_like_cot(stripped):
            continue
        # Strip markdown noise
        cleaned = re.sub(r"^[\d\.\)\*\-\s_#]+", "", stripped).strip("*_ ")
        if len(cleaned) >= 5:
            return cleaned[:120]
    return ""


def _group_chunks_by_title(
    chunk_summaries: list[str],
    headings: list[str],
    assembly: SummaryAssemblyHeadings,
    similarity_threshold: float = 0.2,
) -> list[tuple[str, list[str]]]:
    """
    Group consecutive chunk summaries whose HEADINGS share enough vocabulary.

    Why headings (not raw transcript): the model already extracted the topic
    of each chunk as its own H2 heading — those 3-6 words are semantically
    dense signal, far cleaner than the hundreds of filler words in raw
    transcript vocabulary.

    Adjacent chunks merge into one group when the Jaccard similarity between
    the current chunk's heading and the GROWING accumulated heading-vocab of
    the running group meets ``similarity_threshold``. This lets long runs
    of thematically-linked chunks stay together even as the phrasing drifts.

    Returns a list of (group_name, [chunk_summary, ...]) tuples. The name
    is taken from the first chunk's heading in that group.
    """
    if not chunk_summaries:
        return []

    word_sets = [_extract_word_set(h) for h in headings]
    # Accumulator list: (name, chunks, accumulated_vocab)
    accum: list[tuple[str, list[str], set[str]]] = []

    # Seed with first chunk
    first_name = headings[0].strip() or assembly.beginning
    accum.append((first_name, [chunk_summaries[0]], set(word_sets[0])))

    for i in range(1, len(chunk_summaries)):
        current_set = word_sets[i]
        last_name, last_chunks, last_vocab = accum[-1]

        # If current chunk has no meaningful heading words, default-merge
        if not current_set:
            last_chunks.append(chunk_summaries[i])
            accum[-1] = (last_name, last_chunks, last_vocab)
            continue

        sim = _jaccard_similarity(current_set, last_vocab)

        if sim >= similarity_threshold:
            last_chunks.append(chunk_summaries[i])
            accum[-1] = (last_name, last_chunks, last_vocab | current_set)
        else:
            new_name = headings[i].strip() or assembly.part(len(accum) + 1)
            accum.append((new_name, [chunk_summaries[i]], set(current_set)))

    return [(name, chunks) for name, chunks, _ in accum]


_ATX_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def _prose_only_for_wrapped_section(text: str) -> str:
    """
    Text for programmatic ``##`` wrappers (overview / conclusion LLM calls).

    Prompts forbid headings in those outputs; models still append ``## …``
    sections. Discard from the first ATX heading line onward.
    """
    if not text:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        if _ATX_HEADING_LINE.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _assemble_final(
    topic_summaries: list[tuple[str, str]],
    llm,
    config: Config,
    system_msg: str,
    target_language: str,
    assembly: SummaryAssemblyHeadings,
    n_ctx: int,
    progress_cb=None,
    target_language_code: str = "",
) -> str:
    """
    Assemble final summary from per-topic blocks.

    Strategy: topic blocks pass through AS-IS (no LLM rewrite needed).
    LLM generates ONLY the overview and conclusion — two small, focused
    calls that always fit in the context window. Avoids the truncation
    problem where stuffing all topic summaries into one prompt left no
    room for output.

    Each topic block in ``topic_summaries`` is expected to already begin
    with its own ``## Heading`` (from topic_reduce.txt). If it doesn't,
    we prepend ``## {name}`` as a fallback.

    topic_summaries: list of (topic_name, topic_block_text)
    """
    # Build brief topic descriptions for LLM context (names + first ~200 chars)
    topic_briefs: list[str] = []
    for name, summary in topic_summaries:
        first_line = ""
        if summary:
            for line in summary.strip().split("\n"):
                stripped = line.strip().lstrip("#").strip()
                if len(stripped) > 20:
                    first_line = stripped[:200]
                    break
        topic_briefs.append(f"- {name}: {first_line}")
    briefs_text = "\n".join(topic_briefs)

    # --- LLM call 1: Overview ---
    if progress_cb:
        progress_cb(88, "Writing overview...")

    overview_file = _shared_prompt_path("overview", target_language_code)
    if overview_file.exists():
        overview_prompt = overview_file.read_text(encoding="utf-8")
        overview_prompt = overview_prompt.replace("{{TOPIC_BRIEFS}}", briefs_text)
        overview_prompt = overview_prompt.replace("{target_language}", target_language)
    else:
        # Language-neutral fallback: hardcoded Russian here would force RU output
        # on non-RU targets. Let the enforcement header (prepended below) set the
        # output language.
        overview_prompt = (
            f"Write a 3-5 sentence overview of the video. Topics:\n{briefs_text}\n"
            f"Write in {target_language}. Prose only, no headings."
        )
    overview_prompt = (
        language_enforcement_block(target_language, target_language_code) + overview_prompt
    )
    # Unlimited generation: thinking models consume 50-70 % of tokens on
    # <think> blocks; strip early so heading detection sees real body.
    _overview_raw = _llm_call(llm, overview_prompt, config, system_msg, max_tokens=-1) or ""
    overview = _prose_only_for_wrapped_section((_strip_thinking_tags(_overview_raw) or "").strip())

    # --- LLM call 2: Conclusion ---
    if progress_cb:
        progress_cb(93, "Writing conclusion...")

    conclusion_file = _shared_prompt_path("conclusion", target_language_code)
    if conclusion_file.exists():
        conclusion_prompt = conclusion_file.read_text(encoding="utf-8")
        conclusion_prompt = conclusion_prompt.replace("{{TOPIC_BRIEFS}}", briefs_text)
        conclusion_prompt = conclusion_prompt.replace("{target_language}", target_language)
    else:
        # Language-neutral fallback (same reasoning as overview_prompt above).
        conclusion_prompt = (
            f"Write a one-paragraph conclusion of the video. Topics:\n{briefs_text}\n"
            f"Write in {target_language}. Prose only, no headings."
        )
    conclusion_prompt = (
        language_enforcement_block(target_language, target_language_code) + conclusion_prompt
    )
    _conclusion_raw = _llm_call(llm, conclusion_prompt, config, system_msg, max_tokens=-1) or ""
    conclusion = _prose_only_for_wrapped_section((_strip_thinking_tags(_conclusion_raw) or "").strip())

    # --- Programmatic assembly ---
    parts = [f"## {assembly.overview}\n\n{overview}"]
    for name, summary in topic_summaries:
        block = (summary or "").strip()
        if block.startswith("#"):
            # topic_reduce.txt already emitted a heading — use as-is
            parts.append(block)
        else:
            parts.append(f"## {name}\n\n{block}")
    parts.append(f"## {assembly.conclusion}\n\n{conclusion}")

    if progress_cb:
        progress_cb(96, "Assembly complete")

    return _finalize_summary_hygiene(
        "\n\n".join(parts),
        mode=getattr(config, "summary_mode", "") or "",
    )


def _summarize_with_topics(
    chunk_summaries: list[str],
    config: Config,
    llm,
    n_ctx: int,
    system_msg: str,
    target_language: str,
    target_language_code: str,
    progress_cb=None,
) -> str:
    """
    Modular summarization: overview + topic blocks + conclusion.

    ALWAYS produces the three-part structure regardless of video length.
    For short videos (1-2 chunks) grouping is skipped, but each topic still
    runs ``topic_reduce`` so the body is synthesized (not raw MAP text).

    Input: already-computed per-chunk summaries (MAP-phase output).
    Pipeline:
      1. Extract the ``##`` heading from each chunk summary
      2. Group consecutive chunks with similar heading vocabulary
         (Jaccard over heading word sets)
      3. Per-group REDUCE using ``prompts/_shared/topic_reduce.txt``
         (always, including single-chunk groups)
      4. Assemble: overview + topic blocks AS-IS + conclusion
    """
    total_chunks = len(chunk_summaries)

    # Load topic_reduce prompt template (per-topic synthesis, all group sizes)
    topic_reduce_file = _shared_prompt_path("topic_reduce", target_language_code)
    if not topic_reduce_file.exists():
        print(
            f"[summarizer] ERROR: {topic_reduce_file} missing, cannot use modular pipeline",
            file=sys.stderr, flush=True,
        )
        return None
    topic_reduce_template = topic_reduce_file.read_text(encoding="utf-8")
    assembly = get_summary_assembly_headings(target_language_code)

    # Short videos (1-2 chunks): skip grouping, use each chunk as its own topic
    if total_chunks < 3:
        print(
            f"[summarizer] Short video ({total_chunks} chunks), using modular assembly without grouping",
            file=sys.stderr, flush=True,
        )
        groups = []
        for i, s in enumerate(chunk_summaries):
            heading = _extract_first_heading(s)
            name = heading if heading else assembly.part(i + 1)
            groups.append((name, [s]))
    else:
        # Phase 1: Extract headings from chunk summaries
        headings = [_extract_first_heading(s) for s in chunk_summaries]
        for i, h in enumerate(headings):
            print(f"[summarizer]   chunk {i + 1} heading: {h!r}",
                  file=sys.stderr, flush=True)

        # Phase 2: Group adjacent chunks by heading-vocabulary similarity
        groups = _group_chunks_by_title(
            chunk_summaries, headings, assembly=assembly,
        )
        print(
            f"[summarizer] Grouped {total_chunks} chunks into {len(groups)} topics: "
            f"{', '.join(f'{name!r}({len(ch)})' for name, ch in groups)}",
            file=sys.stderr, flush=True,
        )

    # Phase 3: Per-group REDUCE — always run topic_reduce (even for a single
    # chunk) so the body matches legacy «final» quality: one synthesis pass per
    # topic instead of pasting raw MAP output.
    topic_summaries: list[tuple[str, str]] = []
    total_groups = len(groups)
    for gi, (group_name, group_chunks) in enumerate(groups):
        if progress_cb:
            pct = int(75 + 10 * gi / max(1, total_groups))
            progress_cb(pct,
                f"Synthesizing topic {gi + 1}/{total_groups}...")

        print(
            f"[summarizer] Topic {gi + 1}/{total_groups}: {group_name!r} "
            f"({len(group_chunks)} chunks)",
            file=sys.stderr, flush=True,
        )

        topic_block = _tree_reduce(
            llm, group_chunks, topic_reduce_template, config,
            system_msg, target_language, n_ctx,
            target_language_code=target_language_code,
        )
        # Quality gate: if the model returned prompt-echo / pure CoT that got
        # stripped down to nothing, fall back to the raw chunk summaries so the
        # topic section still has real content instead of an empty header.
        _stripped_preview = (topic_block or "").strip()
        if len(_stripped_preview) < 40 or _stripped_preview.count(" ") < 5:
            print(
                f"[summarizer] WARN: topic {group_name!r} reduce produced "
                f"empty/trivial output after stripping "
                f"({len(_stripped_preview)} chars). "
                "Falling back to raw chunk summaries.",
                file=sys.stderr, flush=True,
            )
            _mode = getattr(config, "summary_mode", "") or ""
            fallback_body = "\n\n".join(
                (_finalize_summary_hygiene(c, mode=_mode) or "").strip()
                for c in group_chunks
                if c and c.strip()
            ).strip()
            topic_block = f"## {group_name}\n\n{fallback_body}" if fallback_body else ""
        topic_summaries.append((group_name, topic_block))

    # Phase 4: Assemble overview + topic blocks AS-IS + conclusion
    if progress_cb:
        progress_cb(88, "Assembling final summary...")

    final = _assemble_final(
        topic_summaries, llm, config, system_msg,
        target_language, assembly, n_ctx, progress_cb,
        target_language_code=target_language_code,
    )

    if progress_cb:
        progress_cb(100, "Summarization complete")

    return final




def summarize(
    segments: list[dict],
    config: Config,
    progress_cb=None,
    batch_mode: bool = False,
    transcript_language: str | None = None,
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
        print(
            f"[summarizer] Loading model: {Path(model_path).name} "
            f"(gpu_layers={gpu_layers}, n_ctx={resolved_n_ctx}, cuda={'yes' if cuda_available else 'no'})",
            file=sys.stderr,
            flush=True,
        )
        print(
            "[summarizer] Llama() mmap/weight load starting — if log stops here, hang is before first generate",
            file=sys.stderr,
            flush=True,
        )
        _t_llama = time.monotonic()
        llm = Llama(**llm_kwargs)
        print(
            f"[summarizer] Llama() load finished in {time.monotonic() - _t_llama:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        try:
            import llama_cpp as _lcv
            _v = getattr(_lcv, "__version__", "?")
            print(f"[summarizer] llama_cpp_python version: {_v}", file=sys.stderr, flush=True)
        except Exception:
            pass
        print(
            f"[summarizer] Llama kwargs: n_gpu_layers={llm_kwargs.get('n_gpu_layers')} "
            f"n_ctx={llm_kwargs.get('n_ctx')} cuda_runtime_available={cuda_available}",
            file=sys.stderr,
            flush=True,
        )
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

            if mode == "inquisition":
                # Inquisition mode is fully programmatic: the four user-supplied
                # analysis fields dictate which sections appear. The static
                # inquisition/*.txt files are kept only as legacy reference.
                mode_cfg = getattr(config, "summary_mode_config", None) or {}
                if not _inquisition_has_any_field(mode_cfg):
                    msg = (
                        "Inquisition mode needs at least ONE of: evaluation goal, "
                        "required items, red flags, or scoring criteria. All four "
                        "fields are empty — nothing to analyse."
                    )
                    print(f"[summarizer] ERROR: {msg}", file=sys.stderr, flush=True)
                    return f"_Error: {msg}_"
                chunk_prompt_template = _build_inquisition_prompt(
                    "chunk", mode_cfg, target_language_name, target_language_code,
                )
                final_prompt_template = _build_inquisition_prompt(
                    "final", mode_cfg, target_language_name, target_language_code,
                )
            elif batch_mode:
                try:
                    chunk_prompt_template = _load_prompt_template(mode, "batch_chunk", target_language_code)
                    final_prompt_template = _load_prompt_template(mode, "batch_final", target_language_code)
                except FileNotFoundError:
                    chunk_prompt_template = _load_prompt_template(mode, "chunk", target_language_code)
                    final_prompt_template = _load_prompt_template(mode, "final", target_language_code)
            else:
                chunk_prompt_template = _load_prompt_template(mode, "chunk", target_language_code)
                final_prompt_template = _load_prompt_template(mode, "final", target_language_code)
        except InquisitionConfigError as e:
            print(f"[summarizer] ERROR: {e}", file=sys.stderr, flush=True)
            return f"_Error: {e}_"
        except (FileNotFoundError, ValueError) as e:
            return "_Error loading prompt templates. Please check your configuration._"
        print(f"[summarizer] Prompt templates loaded OK", file=sys.stderr, flush=True)

        bard_reduce_intermediate: str | None = None
        if mode == "bard":
            try:
                bard_reduce_intermediate = _load_prompt_template(mode, "reduce_intermediate", target_language_code)
            except FileNotFoundError:
                bard_reduce_intermediate = None

        # System message in the TARGET language to avoid confusing small models.
        # Russian system msg + "English" target → model mixes languages.
        if target_language_code == "ru":
            system_msg = (
                f"Отвечай только на {target_language_name}. "
                "ВАЖНО: в выводе — ТОЛЬКО готовый результат. "
                "Не включай в вывод ход мыслей, рассуждения, теги <think>/<thinking>/<reasoning>, "
                "внутренний монолог или пояснения к процессу. Начинай сразу с результата."
            )
        else:
            system_msg = (
                f"Respond ONLY in {target_language_name}. "
                "IMPORTANT: output ONLY the final result. "
                "Do NOT include chain-of-thought, reasoning, <think>/<thinking>/<reasoning> tags, "
                "internal monologue, or meta-commentary about the process. "
                "Start immediately with the result."
            )

        # MAP phase: summarize each chunk.
        # For bard mode the chunk prompt explicitly asks for ``[M:SS]`` timecodes,
        # so the transcript lines must carry real segment start times —
        # otherwise the model hallucinates timecodes (bug reported 2026-04-18).
        # Cheapest fix: always include timecodes in bard chunks; other modes
        # stay unchanged.
        # Inquisition mode has the same [M:SS] requirement throughout its
        # chunk/final prompts, so include timestamps there too.
        include_timestamps = (mode in ("bard", "inquisition"))
        chunks = _chunk_segments(
            segments,
            config.chunk_size_tokens,
            config.chunk_overlap_tokens,
            include_timestamps=include_timestamps,
        )
        if not chunks:
            return "_No content to summarize._"

        total_chunks = len(chunks)
        print(f"[summarizer] {total_chunks} chunks prepared, starting MAP phase...", file=sys.stderr, flush=True)

        prompts: dict[int, str] = {}
        for i, chunk in enumerate(chunks):
            prompt = chunk_prompt_template.replace("{{TRANSCRIPT_CHUNK}}", chunk)
            prompt = _substitute_config_variables(prompt, config.summary_mode_config)
            prompt = prompt.replace("{target_language}", target_language_name)
            prompt = language_enforcement_block(target_language_name, target_language_code) + prompt
            prompts[i] = prompt

        max_workers = max(1, int(config.summary_max_concurrency))
        if not _owns_llm and max_workers > 1:
            print("[summarizer] WARNING: parallel workers disabled with shared LLM (each worker loads a full model copy)", file=sys.stderr, flush=True)
            max_workers = 1
        chunk_summaries_indexed: dict[int, str] = {}

        if total_chunks > 0:
            if max_workers == 1:
                for pos, i in enumerate(range(total_chunks), 1):
                    if progress_cb:
                        progress_cb(
                            int(10 + 60 * pos / total_chunks),
                            f"Summarizing chunk {i + 1}/{total_chunks}...",
                        )
                    print(
                        f"[summarizer] Chunk {i+1}/{total_chunks}: UI progress emitted "
                        f"({int(10 + 60 * pos / total_chunks)}% MAP); next: _run_chunk_summary",
                        file=sys.stderr,
                        flush=True,
                    )
                    print(f"[summarizer] Chunk {i+1}/{total_chunks}: sending to LLM ({_estimate_tokens(prompts[i])} est. tokens)...", file=sys.stderr, flush=True)
                    chunk_summaries_indexed[i] = _run_chunk_summary(llm, prompts[i], config, system_msg, n_ctx=resolved_n_ctx)
                    print(f"[summarizer] Chunk {i+1}/{total_chunks}: done", file=sys.stderr, flush=True)
            else:
                def _parallel_worker(prompt_text: str) -> str:
                    print(
                        "[summarizer] parallel MAP worker: Llama() load starting...",
                        file=sys.stderr,
                        flush=True,
                    )
                    _t_w = time.monotonic()
                    local_llm = Llama(**llm_kwargs)
                    print(
                        f"[summarizer] parallel MAP worker: Llama() load done in {time.monotonic() - _t_w:.1f}s",
                        file=sys.stderr,
                        flush=True,
                    )
                    try:
                        return _run_chunk_summary(local_llm, prompt_text, config, system_msg, n_ctx=resolved_n_ctx)
                    finally:
                        del local_llm
                        gc.collect()

                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    future_to_idx = {
                        pool.submit(_parallel_worker, prompts[i]): i for i in range(total_chunks)
                    }
                    completed = 0
                    for future in as_completed(future_to_idx):
                        idx = future_to_idx[future]
                        chunk_summaries_indexed[idx] = future.result()
                        completed += 1
                        if progress_cb:
                            progress_cb(
                                int(10 + 60 * completed / total_chunks),
                                f"Summarizing chunk {idx + 1}/{total_chunks}...",
                            )

        chunk_summaries = [chunk_summaries_indexed[i] for i in range(total_chunks)]

        # --- Quality gate: detect empty/garbage chunk summaries ---
        # If CoT stripping left a chunk empty, the model produced only
        # reasoning.  Fall back to raw transcript for that chunk so the
        # REDUCE phase has *something* real to work with.
        garbage_count = 0
        for i, cs in enumerate(chunk_summaries):
            if not cs or len(cs.strip()) < 30:
                garbage_count += 1
                chunk_summaries[i] = chunks[i]  # raw transcript fallback
                print(
                    f"[summarizer] WARNING: chunk {i+1} summary is empty/garbage "
                    f"(model produced only CoT), using raw transcript as fallback",
                    file=sys.stderr, flush=True,
                )
        if garbage_count > 0:
            print(
                f"[summarizer] Quality gate: {garbage_count}/{total_chunks} chunks "
                f"failed (model produced only reasoning), using raw transcript fallback",
                file=sys.stderr, flush=True,
            )

        # --- Post-MAP: modular assembly (overview + topics + conclusion) ---
        # Always use modular pipeline for non-batch mode — it produces
        # focused LLM calls that work reliably even with small local models.
        # The monolithic final.txt caused instruction-leakage on short videos.
        # Modes with custom final.txt formats (inquisition, bard) bypass
        # the modular pipeline and use their own final prompt template.
        _modular_bypass_modes = {"inquisition", "bard"}
        use_modular = config.topic_segmentation and not batch_mode and mode not in _modular_bypass_modes
        if use_modular:
            print(
                f"[summarizer] Modular pipeline: {total_chunks} chunk summaries, "
                f"{len(segments)} segments",
                file=sys.stderr, flush=True,
            )
            topic_result = _summarize_with_topics(
                chunk_summaries, config, llm, resolved_n_ctx,
                system_msg, target_language_name, target_language_code,
                progress_cb,
            )
            if topic_result is not None:
                if progress_cb:
                    progress_cb(100, "Summarization complete")
                return topic_result
            # None → topic_reduce.txt missing, fall through to legacy path
            print("[summarizer] Modular pipeline unavailable, using legacy REDUCE",
                  file=sys.stderr, flush=True)

        # REDUCE phase (legacy fallback — only for batch_mode or missing prompts)
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
            intermediate_template=bard_reduce_intermediate if mode == "bard" else None,
            target_language_code=target_language_code,
        )

        if progress_cb:
            progress_cb(100, "Summarization complete")

        return _finalize_summary_hygiene(final_summary, mode=mode)


    finally:
        if _owns_llm:
            try:
                del llm
            except (NameError, UnboundLocalError):
                pass
            gc.collect()
