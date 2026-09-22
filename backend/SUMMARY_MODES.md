# Summary Modes Documentation

## Overview

The LocalVideoTranscriber now supports pluggable summary modes. Each mode provides a specialized approach to summarizing audio/video content tailored to different use cases.

## Available Modes

### 1. **notes** (Default)
**Purpose**: Clean, structured notes for learning and overview.

Generates:
- Overview of content
- Numbered list of key points
- Detailed notes organized by topics
- Important terms and quotes
- Conclusion/call to action

**Configuration**: No special config needed (empty `summary_mode_config`)

**Example**:
```json
{
  "summary_mode": "notes",
  "summary_mode_config": {}
}
```

---

### 2. **inquisition**
**Purpose**: Universal content evaluation by user-defined criteria. Evaluate any content — videos, calls, lectures, propaganda, compliance, etc.

Generates:
- Content score (X/10)
- Evaluation goal summary
- Checklist of required items with timestamps (if applicable)
- Red flags and warnings
- Scoring by criteria (X/10 each)
- Recommendations

**Configuration Required**:
```json
{
  "summary_mode": "inquisition",
  "summary_mode_config": {
    "EVALUATION_GOAL": "Evaluate how propagandistic this video is",
    "REQUIRED_ITEMS": [
      "factual claims with sources",
      "balanced presentation of viewpoints",
      "clear attribution of opinions"
    ],
    "RED_FLAGS": [
      "emotional manipulation without evidence",
      "dehumanizing language",
      "false dichotomies"
    ],
    "SCORING_CRITERIA": [
      "objectivity",
      "factual_accuracy",
      "source_quality",
      "balanced_representation"
    ]
  }
}
```

### 2b. **call_check** *(legacy, still supported)*
**Purpose**: Quality assurance for sales calls, interviews, and dialogs. Superseded by `inquisition` mode which is more flexible.

**Configuration Required**:
```json
{
  "summary_mode": "call_check",
  "summary_mode_config": {
    "REQUIRED_PHRASES": ["уточните бюджет", "расскажите о компании"],
    "RED_FLAGS": ["не знаю", "позвоните завтра"],
    "SCORING_CRITERIA": ["rapport", "needs_discovery", "closing"]
  }
}
```

**Format Note**: Lists are automatically formatted with bullet points in prompts.

---

### 3. **factcheck**
**Purpose**: Extract and verify factual claims in lectures, debates, news.

Generates:
- Table of extracted facts
- Verification status for each claim
- Source attribution if available
- Fact count summary

**Configuration**: No special config needed

**Example**:
```json
{
  "summary_mode": "factcheck",
  "summary_mode_config": {}
}
```

---

### 4. **tldr**
**Purpose**: Quick summary (max 150 words) for rapid content screening.

Generates:
- Single paragraph
- Maximum 150 words
- Only essential information

**Configuration**: No special config needed

**Example**:
```json
{
  "summary_mode": "tldr",
  "summary_mode_config": {}
}
```

---

## JSON-RPC API Usage

The backend accepts summary modes via the "process" method:

```json
{
  "method": "process",
  "id": 1,
  "params": {
    "source": "https://example.com/video.mp4",
    "config": {
      "whisper_model": "small",
      "llm_temperature": 0.1
    },
    "summary_mode": "call_check",
    "summary_mode_config": {
      "REQUIRED_PHRASES": ["budget", "timeline"],
      "RED_FLAGS": ["maybe", "unclear"],
      "SCORING_CRITERIA": ["rapport", "discovery"]
    }
  }
}
```

## Prompt Structure

Each mode has two prompt files in `backend/prompts/{mode}/`:
- **chunk.txt** — Applied during MAP phase (summarizes individual chunks)
- **final.txt** — Applied during REDUCE phase (synthesizes final summary)

### Variable Injection

Prompts use `{{PLACEHOLDER}}` syntax for:
- `{{TRANSCRIPT_CHUNK}}` — The current transcript chunk (always injected)
- `{{CHUNK_SUMMARIES}}` — Combined chunk summaries (always injected)
- Mode-specific variables (injected from `summary_mode_config`):
  - `{{EVALUATION_GOAL}}` (inquisition) — free-text evaluation purpose
  - `{{REQUIRED_ITEMS}}` (inquisition) — what to look for
  - `{{RED_FLAGS}}` (inquisition, call_check) — warnings/forbidden items
  - `{{SCORING_CRITERIA}}` (inquisition, call_check) — criteria for scoring
  - `{{REQUIRED_PHRASES}}` (call_check, legacy)

Lists are automatically formatted as bullet points with `- ` prefix.

## Backward Compatibility

- Old `prompts/chunk_summary.txt` and `prompts/final_summary.txt` are still supported
- If a mode is not found, the system falls back to "notes" mode
- Existing code without `summary_mode` specified uses "notes" by default

## Implementation Details

### Loading Logic (`_load_prompt_template`)
1. Try `prompts/{mode}/{phase}.txt`
2. Fall back to `prompts/notes/{phase}.txt`
3. Fall back to old `prompts/{phase}_summary.txt`
4. Raise error if none exist

### Variable Substitution (`_substitute_config_variables`)
- Iterates through `summary_mode_config` dictionary
- For each key-value pair:
  - Looks for `{{KEY}}` in the template
  - Converts lists to bullet-pointed strings
  - Replaces placeholder with value

### Model Loading
- Models are loaded once per summarization task
- Unloaded after completion with `gc.collect()` to free VRAM
- Consistent with existing architecture

## Example Usage Flow

1. **Frontend** sends JSON-RPC message with `summary_mode` and `summary_mode_config`
2. **main.py** forwards to `pipeline.run()`
3. **pipeline.py** creates Config with mode parameters
4. **summarizer.py** loads appropriate prompt templates
5. **Injects** config variables into prompts
6. **MAP phase** processes each chunk with mode-specific logic
7. **REDUCE phase** synthesizes final output
8. **Returns** markdown with mode-specific structure

## Testing

To test a specific mode locally:

```python
from config import Config
from summarizer import summarize

config = Config(
    summary_mode="call_check",
    summary_mode_config={
        "REQUIRED_PHRASES": ["budget", "timeline"],
        "RED_FLAGS": ["maybe", "unclear"],
        "SCORING_CRITERIA": ["rapport", "discovery"]
    }
)

summary = summarize(segments, config)
print(summary)
```

## Future Extensions

The mode system is designed to be easily extended:

1. Create new folder: `backend/prompts/new_mode/`
2. Add `chunk.txt` and `final.txt`
3. Document required variables in `summary_mode_config`
4. Update frontend to expose new mode option

No code changes required in Python backend (except possibly for validation).
