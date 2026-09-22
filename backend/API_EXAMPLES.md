# JSON-RPC API Examples for Summary Modes

## Basic Request Format

All requests to the backend use JSON-RPC via stdin/stdout:

```json
{
  "method": "process",
  "id": <message_id>,
  "params": {
    "source": "<url_or_path>",
    "config": { ... },
    "summary_mode": "mode_name",
    "summary_mode_config": { ... }
  }
}
```

## Example 1: Notes Mode (Default)

**Scenario**: User wants a structured study/learning guide from a lecture.

```json
{
  "method": "process",
  "id": 1,
  "params": {
    "source": "https://example.com/lecture.mp4",
    "config": {
      "whisper_model": "small",
      "compute_type": "auto",
      "language": null,
      "llm_temperature": 0.1,
      "chunk_size_tokens": 1500
    },
    "summary_mode": "notes",
    "summary_mode_config": {}
  }
}
```

**Response**: Structured notes with overview, key points, detailed notes, and conclusion.

---

## Example 2: Call Check Mode

**Scenario**: QA team wants to verify a sales call quality and check compliance.

```json
{
  "method": "process",
  "id": 2,
  "params": {
    "source": "/recordings/sales_call_2026-04-05.wav",
    "config": {
      "whisper_model": "small",
      "language": "ru",
      "llm_temperature": 0.1,
      "chunk_size_tokens": 1500
    },
    "summary_mode": "call_check",
    "summary_mode_config": {
      "REQUIRED_PHRASES": [
        "уточните ваш бюджет",
        "расскажите о вашей компании",
        "определим следующий шаг",
        "когда вы готовы к внедрению"
      ],
      "RED_FLAGS": [
        "не знаю",
        "позвоните завтра",
        "я уточню у руководства",
        "может быть",
        "мне нужно подумать"
      ],
      "SCORING_CRITERIA": [
        "rapport building",
        "needs discovery",
        "solution alignment",
        "objection handling",
        "closing"
      ]
    }
  }
}
```

**Response**: QA report with:
- Overall score (e.g., 7.5/10)
- Checklist of required phrases (✅/❌ with timestamps)
- Red flag alerts (⚠️)
- Criterion scores
- Recommendations

---

## Example 3: Factcheck Mode

**Scenario**: Journalist wants to verify claims in a debate or interview.

```json
{
  "method": "process",
  "id": 3,
  "params": {
    "source": "https://example.com/debate.mp4",
    "config": {
      "whisper_model": "small",
      "language": "ru",
      "llm_temperature": 0.1
    },
    "summary_mode": "factcheck",
    "summary_mode_config": {}
  }
}
```

**Response**: Markdown table with columns:
- Утверждение (Claim)
- Таймкод (Timestamp)
- Верифицируемость (Verification level: ✅ High / ⚠️ Needs Check)
- Примечание (Notes/Source)

---

## Example 4: TLDR Mode

**Scenario**: User wants a quick 1-paragraph summary for screening.

```json
{
  "method": "process",
  "id": 4,
  "params": {
    "source": "https://example.com/webinar.mp4",
    "config": {
      "whisper_model": "small",
      "llm_temperature": 0.05
    },
    "summary_mode": "tldr",
    "summary_mode_config": {}
  }
}
```

**Response**: Single paragraph (max 150 words) summarizing the entire content.

---

## Example 5: Multilingual Transcription

**Scenario**: Video with mixed Russian and English content.

```json
{
  "method": "process",
  "id": 5,
  "params": {
    "source": "https://example.com/mixed_lang_content.mp4",
    "config": {
      "whisper_model": "small",
      "multilingual": "auto",
      "multilingual_threshold": 0.8,
      "language": null
    },
    "summary_mode": "notes",
    "summary_mode_config": {}
  }
}
```

**How it works**:
1. `multilingual: "auto"` enables automatic detection
2. Backend detects language in 30-second windows with 15-second overlap
3. If >1 language found with confidence ≥ 0.8: enables adaptive multilingual transcription
4. Each segment includes `language` field (e.g., "ru", "en")
5. Falls back to segment-based transcription if native mode confidence is low

**Options**:
- `multilingual: "true"` — always enable multilingual mode (no detection)
- `multilingual: "false"` — disable multilingual, use single language
- `multilingual: "auto"` — auto-detect (default)
- `multilingual_threshold: 0.8` — language detection confidence threshold (0.0-1.0)

---

## Example 6: Mixed Config with All Options

**Scenario**: Comprehensive request with all available configuration options.

```json
{
  "method": "process",
  "id": 6,
  "params": {
    "source": "https://example.com/content.mp4",
    "output_dir": "/custom/output/path",
    "config": {
      "whisper_model": "medium",
      "compute_type": "float16",
      "language": null,
      "device": "cuda",
      "beam_size": 5,
      "vad_filter": true,
      "multilingual": "auto",
      "multilingual_threshold": 0.8,
      "llm_model_path": "/path/to/model.gguf",
      "llm_context_length": 4096,
      "llm_temperature": 0.1,
      "chunk_size_tokens": 1500,
      "chunk_overlap_tokens": 200,
      "include_full_transcript": true
    },
    "summary_mode": "call_check",
    "summary_mode_config": {
      "REQUIRED_PHRASES": [
        "фраза 1",
        "фраза 2"
      ],
      "RED_FLAGS": [
        "стоп-слово 1",
        "стоп-слово 2"
      ],
      "SCORING_CRITERIA": [
        "критерий 1",
        "критерий 2"
      ]
    }
  }
}
```

---

## Response Format

All responses follow the same JSON-RPC format:

### Success Response
```json
{
  "id": 1,
  "type": "result",
  "markdown": "# Generated Markdown Summary\n...",
  "output_path": "/path/to/output/summary.md"
}
```

### Progress Update (during processing)
```json
{
  "id": 1,
  "type": "progress",
  "stage": "summarize",
  "percent": 45,
  "message": "Summarizing chunk 3/8..."
}
```

### Error Response
```json
{
  "id": 1,
  "type": "error",
  "message": "Error description here"
}
```

---

## Frontend Integration Checklist

When integrating with the Electron frontend:

- [ ] Accept `summary_mode` selection from user (radio/dropdown: notes, call_check, factcheck, tldr)
- [ ] For `call_check` mode, show form to input:
  - [ ] Required phrases (textarea with one per line)
  - [ ] Red flags (textarea with one per line)
  - [ ] Scoring criteria (textarea with one per line)
- [ ] For other modes, disable mode-specific config fields
- [ ] Include mode selection in JSON sent to backend
- [ ] Parse and display responses appropriate to the mode

---

## Python Backend Testing

To test directly in Python:

```python
import json

# Test call_check mode
request = {
    "method": "process",
    "id": 1,
    "params": {
        "source": "test.mp3",
        "config": {"whisper_model": "small"},
        "summary_mode": "call_check",
        "summary_mode_config": {
            "REQUIRED_PHRASES": ["hello"],
            "RED_FLAGS": ["sorry"],
            "SCORING_CRITERIA": ["clarity"]
        }
    }
}

json_str = json.dumps(request, ensure_ascii=False)
print(json_str)
```

Then pipe to the backend:
```bash
echo '{"method":"process","id":1,"params":{"source":"test.mp3","config":{},"summary_mode":"notes","summary_mode_config":{}}}' | python3 backend/main.py
```
