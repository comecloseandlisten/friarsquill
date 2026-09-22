# PLAN: Topic-Aware Hierarchical Summarization

## Problem

For long videos (1-3+ hours) the current MAP-REDUCE pipeline produces shallow summaries.
Chunks are cut mechanically at ~3000 tokens with no regard for topic boundaries — one topic
may span 5 chunks and the tree-reduce phase flattens everything into a generic overview.
The current review **format** is good; it just lacks **depth**.

## Solution: Two-Pass Boundary Detection + Per-Topic MAP-REDUCE

### Architecture Overview

```
                     Transcript (segments[])
                           │
                  ┌────────▼────────┐
            P1    │  BOUNDARY SCAN  │  sliding window ~2K tokens, step ~500
                  │  detect_boundary│  "is there a topic change here?"
                  │  .txt prompt    │  cheap LLM calls, short answers
                  └────────┬────────┘
                           │ [raw boundaries + topic names]
                  ┌────────▼────────┐
            P2    │   MERGE/SPLIT   │  deduplicate, enforce min topic size
                  │   (no LLM)      │  pure logic
                  └────────┬────────┘
                           │ [Topic 1: segs 0-42, Topic 2: segs 43-91, ...]
                  ┌────────▼────────┐
            P3    │    CALIBRATE    │  chunk-agent budget per topic
                  │    (no LLM)     │  proportional to segment count
                  └────────┬────────┘
           ┌───────────────┼───────────────┐
      ┌────▼────┐     ┌────▼────┐     ┌────▼────┐
  P4  │TOPIC 1  │     │TOPIC 2  │     │TOPIC N  │   parallel
      │ MAP     │     │ MAP     │     │ MAP     │   ← existing chunk.txt
      │ REDUCE  │     │ REDUCE  │     │ REDUCE  │   ← existing final.txt + topic ctx
      └────┬────┘     └────┬────┘     └────┬────┘
           │               │               │
           └───────────────┼───────────────┘
                  ┌────────▼────────┐
            P5    │    ASSEMBLE     │  NEW assemble.txt prompt
                  │  overview       │
                  │  + topic blocks │
                  │  + conclusions  │
                  └─────────────────┘
```

### Key Design Decisions

1. **No timestamps required** — boundary detection works on segment indices, not timecodes
2. **Small context window** — each sliding window is ~2K tokens, fits any GGUF model
3. **Existing prompts untouched** — chunk.txt and final.txt stay as-is; topic context
   is injected as a prefix wrapper in code
4. **Backwards compatible** — feature gated by `topic_segmentation: bool` config flag
   and auto-enabled above segment count threshold
5. **Same LLM instance** — boundary scan reuses the already-loaded model

---

## Phase Details

### P1: BOUNDARY SCAN (`_detect_topic_boundaries`)

**Input:** `segments[]`, loaded LLM, config
**Output:** `list[BoundaryResult]` — `{segment_index, topic_name, confidence}`

Algorithm:
1. Build sliding windows of ~2000 tokens with step ~500 tokens
2. For each window, send to LLM with `detect_boundary.txt` prompt
3. LLM responds with structured JSON: `{"boundary": true/false, "topic": "...", "confidence": 0.0-1.0, "position_hint": "quote near boundary"}`
4. Map `position_hint` back to segment index via text search in window segments
5. Filter by confidence threshold (default 0.6)

**Prompt design:** Simple question — "Is there a topic transition in this text fragment? If yes, quote 5-10 words near the boundary and name the new topic."

**Token budget per call:** ~2K input + ~100 output = very cheap

### P2: MERGE/SPLIT (`_merge_boundaries`)

**Input:** `list[BoundaryResult]`, `min_topic_segments=10`
**Output:** `list[TopicSpan]` — `{start_seg, end_seg, name}`

Pure logic, no LLM:
1. Sort boundaries by segment_index
2. Deduplicate: if two boundaries within ±5 segments, keep higher confidence
3. Build topic spans from boundaries
4. Merge tiny topics (< min_topic_segments) into neighbors
5. If zero boundaries found, fall back to single-topic (standard pipeline)

### P3: CALIBRATE (`_calibrate_topic_budgets`)

**Input:** `list[TopicSpan]`, config
**Output:** `list[TopicSpan]` with `chunk_size_tokens` per topic

Pure logic:
- Topics shorter than 1 chunk → skip internal MAP, just concatenate text
- Topics 1-3 chunks → standard chunk_size from config
- Topics 4+ chunks → may increase overlap for coherence
- Total budget: sum of all topic chunk counts ≈ what flat MAP-REDUCE would produce

### P4: PER-TOPIC MAP-REDUCE (`_summarize_topic`)

**Input:** topic segments, topic_name, neighbor names, config, LLM
**Output:** topic summary string

Reuses existing `_chunk_segments()` + `_tree_reduce()` with one addition:
- Injects topic context prefix before the `final.txt` prompt:

```
Ты суммаризируешь тему «{topic_name}» из длинного видео.
Соседние темы (для контекста):
- До: «{prev_topic}»
- После: «{next_topic}»

{ORIGINAL_FINAL_PROMPT}
```

chunk.txt used as-is — no changes.

### P5: ASSEMBLE (`_assemble_final`)

**Input:** list of `(topic_name, topic_summary)`, config, LLM
**Output:** final markdown string

New prompt `prompts/_shared/assemble.txt`:
- Receives all topic summaries
- Produces:
  1. **О чём видео** — overview block (like current format)
  2. **Topic blocks** — each topic's deep summary (already produced by P4)
  3. **Итог** — cross-cutting conclusions, connections between topics

This preserves the familiar review format but adds structured depth.

---

## Files to Create/Modify

### New files:
| File | Purpose |
|------|---------|
| `prompts/_shared/detect_boundary.txt` | Boundary detection prompt |
| `prompts/_shared/assemble.txt` | Final assembly prompt |

### Modified files:
| File | Changes |
|------|---------|
| `config.py` | Add `topic_segmentation`, `topic_min_segments`, `topic_boundary_confidence` |
| `summarizer.py` | Add 5 new functions, modify `summarize()` entry point |
| `pipeline.py` | Pass new config params, add progress events for topic phases |
| `SUMMARY_MODES.md` | Document topic segmentation feature |

### NOT modified:
| File | Reason |
|------|--------|
| `prompts/notes/chunk.txt` | Reused as-is |
| `prompts/notes/final.txt` | Reused as-is (context injected in code) |
| `prompts/tldr/*` | Reused as-is |
| `prompts/factcheck/*` | Reused as-is |
| `prompts/call_check/*` | Reused as-is |
| `formatter.py` | No changes needed — summary is still a string |

---

## Config Fields

```python
# Topic segmentation (hierarchical summarization for long videos)
topic_segmentation: bool = True          # enable topic-aware summarization
topic_auto_threshold: int = 50           # auto-enable if segments > this (when topic_segmentation=True)
topic_min_segments: int = 10             # minimum segments per topic (smaller merged with neighbor)
topic_boundary_confidence: float = 0.6   # minimum confidence to accept a boundary
topic_window_tokens: int = 2000          # sliding window size for boundary detection
topic_window_step_tokens: int = 500      # sliding window step
```

---

## Progress Events

| Phase | Progress Range | Message |
|-------|---------------|---------|
| Boundary scan | 10-25% | "Detecting topic boundaries... (window X/Y)" |
| Per-topic MAP | 25-75% | "Summarizing topic X/N: «topic_name»..." |
| Per-topic REDUCE | 75-85% | "Synthesizing topic X/N..." |
| Assembly | 85-95% | "Assembling final summary..." |
| Done | 100% | "Summarization complete" |

---

## Implementation Order

1. `config.py` — add new fields
2. `prompts/_shared/detect_boundary.txt` — write prompt
3. `prompts/_shared/assemble.txt` — write prompt
4. `summarizer.py` — implement in order:
   a. `_detect_topic_boundaries()`
   b. `_merge_boundaries()`
   c. `_calibrate_topic_budgets()`
   d. `_summarize_topic()`
   e. `_assemble_final()`
   f. Modify `summarize()` entry point
5. `pipeline.py` — wire config params
6. Test with long video

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| LLM returns invalid JSON in boundary scan | Regex fallback parser + retry once |
| Zero boundaries detected | Fall back to standard flat MAP-REDUCE |
| Too many boundaries (micro-topics) | min_topic_segments merge + max topics cap |
| Boundary detection slow on CPU | Windows are independent → can parallelize later |
| Topic context prefix too long | Cap neighbor names at 100 chars each |
