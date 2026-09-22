# Batch Mode — JSON-RPC API Reference

**Complete API specification for batch processing in LocalVideoTranscriber**

---

## Method: `process_batch`

Process multiple video sources and generate a consolidated report.

### Request Format

```json
{
  "method": "process_batch",
  "id": <integer>,
  "params": {
    "sources": ["<url_or_path_1>", "<url_or_path_2>", ...],
    "config": {
      "whisper_model": "small",
      "compute_type": "auto",
      "language": null,
      "device": "auto",
      "beam_size": 5,
      "vad_filter": true,
      "multilingual": "auto",
      "multilingual_threshold": 0.8,
      "llm_model_path": null,
      "llm_model_repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
      "llm_model_file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
      "llm_context_length": 4096,
      "llm_temperature": 0.1,
      "chunk_size_tokens": 1500,
      "chunk_overlap_tokens": 200,
      "summary_mode": "notes",
      "summary_mode_config": {},
      "diarize": false,
      "num_speakers": null,
      "speaker_names": {},
      "diarize_backend": "energy",
      "output_dir": "./output",
      "include_full_transcript": true
    },
    "summary_mode": "notes",
    "summary_mode_config": {},
    "multilingual": "auto",
    "multilingual_threshold": 0.8,
    "diarize": false,
    "num_speakers": null,
    "speaker_names": {},
    "diarize_backend": "energy",
    "output_dir": "./output"
  }
}
```

### Parameters

#### Required

- **`sources`** `array[string]`
  - List of video sources to process
  - Can be URLs (http://, https://) or local file paths
  - Must contain at least 1 source
  - Example: `["https://example.com/video1.mp4", "/home/user/video2.mp4"]`

- **`config`** `object`
  - Base configuration object (same as `process` method)
  - All fields have defaults, can be partial

#### Optional Override Parameters

These override the corresponding values in `config`:

- **`summary_mode`** `string`
  - Override: `config.summary_mode`
  - Options: `"notes"`, `"call_check"`, `"factcheck"`, `"tldr"`

- **`summary_mode_config`** `object`
  - Override: `config.summary_mode_config`
  - Mode-specific configuration dict

- **`multilingual`** `string`
  - Override: `config.multilingual`
  - Options: `"auto"`, `"true"`, `"false"`

- **`multilingual_threshold`** `number` (0.0-1.0)
  - Override: `config.multilingual_threshold`

- **`diarize`** `boolean`
  - Override: `config.diarize`

- **`num_speakers`** `integer | null`
  - Override: `config.num_speakers`

- **`speaker_names`** `object`
  - Override: `config.speaker_names`
  - Map: `{"SPEAKER_00": "Name1", "SPEAKER_01": "Name2"}`

- **`diarize_backend`** `string`
  - Override: `config.diarize_backend`
  - Options: `"energy"`, `"pyannote"`

- **`output_dir`** `string`
  - Override: `config.output_dir`
  - Directory where batch report is saved

---

## Response Messages

### Success Response

```json
{
  "id": 1,
  "type": "result",
  "markdown": "<full markdown report as string>",
  "output_path": "/absolute/path/to/batch_report_20260405_143022.md",
  "videos_processed": 3
}
```

**Fields:**

- **`id`** `integer` — Matches request ID
- **`type`** `"result"` — Indicates success
- **`markdown`** `string` — Full formatted markdown report
- **`output_path`** `string` — Absolute path where report was saved
- **`videos_processed`** `integer` — Number of successfully processed videos

### Error Response

```json
{
  "id": 1,
  "type": "error",
  "message": "<error description>"
}
```

**Fields:**

- **`id`** `integer` — Matches request ID
- **`type`** `"error"` — Indicates error
- **`message`** `string` — Error description

**Common errors:**
- `"No sources provided for batch processing"`
- `"Cancelled"`
- `"[1/3] Error: Failed to download file (404)"`
- Various Whisper/transcription errors

---

## Progress Messages

Streamed during processing (before final result):

```json
{
  "id": 1,
  "type": "progress",
  "stage": "batch",
  "percent": 35,
  "message": "[2/5] Transcribing... 45s / 120s"
}
```

**Fields:**

- **`id`** `integer` — Matches request ID
- **`type`** `"progress"` — Indicates progress update
- **`stage`** `"batch"` — Always "batch" for batch requests
- **`percent`** `integer` (0-100) — Overall progress percentage
- **`message`** `string` — Human-readable status message

**Message patterns:**

```
[1/3] Preparing source...
[1/3] Downloading... 25%
[1/3] Extracting audio...
[1/3] Detecting speakers...
[1/3] Loading transcription model...
[1/3] Transcribing... 30s / 120s
[1/3] Video processed: 450 segments
[2/3] Preparing source...
[2/3] Downloading... 50%
...
Loading summarization model...
Synthesizing final summary...
Generating markdown...
Batch processing complete!
```

---

## Configuration Object Details

### Transcription Settings

```json
{
  "whisper_model": "tiny|base|small|medium|large",
  "compute_type": "auto|int8|float16|float32",
  "language": null,
  "device": "auto|cpu|cuda",
  "beam_size": 5,
  "vad_filter": true
}
```

### Multilingual Settings

```json
{
  "multilingual": "auto|true|false",
  "multilingual_threshold": 0.8
}
```

### Summarization Settings

```json
{
  "llm_model_path": null,
  "llm_model_repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
  "llm_model_file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
  "llm_context_length": 4096,
  "llm_temperature": 0.1,
  "chunk_size_tokens": 1500,
  "chunk_overlap_tokens": 200,
  "summary_mode": "notes|call_check|factcheck|tldr",
  "summary_mode_config": {}
}
```

### Diarization Settings

```json
{
  "diarize": true,
  "num_speakers": null,
  "speaker_names": {
    "SPEAKER_00": "Speaker 1",
    "SPEAKER_01": "Speaker 2"
  },
  "diarize_backend": "energy|pyannote"
}
```

### Output Settings

```json
{
  "output_dir": "./output",
  "include_full_transcript": true
}
```

---

## Summary Mode Specific Configurations

### `call_check` Configuration

```json
{
  "summary_mode": "call_check",
  "summary_mode_config": {
    "required_phrases": [
      "confirm budget",
      "tell me about your company",
      "next steps",
      "decision timeline"
    ],
    "red_flags": [
      "I don't know",
      "call back tomorrow",
      "I'll check with management",
      "that's not my responsibility"
    ],
    "scoring_criteria": [
      "rapport_building",
      "needs_discovery",
      "objection_handling",
      "closing"
    ]
  }
}
```

### `notes` Configuration

```json
{
  "summary_mode": "notes",
  "summary_mode_config": {}
}
```

### `factcheck` Configuration

```json
{
  "summary_mode": "factcheck",
  "summary_mode_config": {}
}
```

### `tldr` Configuration

```json
{
  "summary_mode": "tldr",
  "summary_mode_config": {
    "max_words": 150
  }
}
```

---

## Output Format

### File Location

```
{output_dir}/batch_report_{YYYYMMDD}_{HHMMSS}.md
```

Example: `batch_report_20260405_143022.md`

### Markdown Structure

```markdown
# Batch Transcription Report

**Videos processed:** 3
**Total duration:** 45:30
**Total words:** 12,450

---

## Overall Summary

[Combined analysis of all videos with cross-video insights]

---

## Video 1: [Title]

**Source:** `[URL or path]`
**Duration:** [hh:mm:ss]
**Words:** [count]
**Segments:** [count]

### Transcript

[Speaker-labeled transcript with timestamps]

---

## Video 2: [Title]

...
```

---

## Examples

### Example 1: Simple Batch Processing

**Request:**
```json
{
  "method": "process_batch",
  "id": 1,
  "params": {
    "sources": [
      "/home/user/call1.mp4",
      "/home/user/call2.mp4"
    ],
    "config": {
      "whisper_model": "small",
      "summary_mode": "notes"
    },
    "output_dir": "./output"
  }
}
```

**Response:**
```json
{
  "id": 1,
  "type": "result",
  "markdown": "# Batch Report\n\n**Videos processed:** 2\n...",
  "output_path": "/home/user/output/batch_report_20260405_143022.md",
  "videos_processed": 2
}
```

### Example 2: Sales Call Review with Diarization

**Request:**
```json
{
  "method": "process_batch",
  "id": 2,
  "params": {
    "sources": [
      "https://example.com/call_monday.mp4",
      "https://example.com/call_wednesday.mp4",
      "https://example.com/call_friday.mp4"
    ],
    "config": {
      "whisper_model": "small"
    },
    "summary_mode": "call_check",
    "summary_mode_config": {
      "required_phrases": ["confirm budget", "next steps"],
      "red_flags": ["I don't know", "call back later"],
      "scoring_criteria": ["rapport", "needs_discovery", "closing"]
    },
    "diarize": true,
    "num_speakers": 2,
    "speaker_names": {
      "SPEAKER_00": "Manager",
      "SPEAKER_01": "Client"
    },
    "output_dir": "./output"
  }
}
```

**Progress messages:**
```json
{"id": 2, "type": "progress", "stage": "batch", "percent": 5, "message": "[1/3] Preparing source..."}
{"id": 2, "type": "progress", "stage": "batch", "percent": 8, "message": "[1/3] Downloading... 50%"}
{"id": 2, "type": "progress", "stage": "batch", "percent": 15, "message": "[1/3] Detecting speakers..."}
{"id": 2, "type": "progress", "stage": "batch", "percent": 25, "message": "[1/3] Transcribing... 60s / 120s"}
{"id": 2, "type": "progress", "stage": "batch", "percent": 35, "message": "[2/3] Preparing source..."}
...
{"id": 2, "type": "progress", "stage": "batch", "percent": 70, "message": "Loading summarization model..."}
{"id": 2, "type": "progress", "stage": "batch", "percent": 85, "message": "Generating markdown..."}
{"id": 2, "type": "result", "markdown": "...", "output_path": "...", "videos_processed": 3}
```

### Example 3: Multilingual Conference Batch

**Request:**
```json
{
  "method": "process_batch",
  "id": 3,
  "params": {
    "sources": [
      "https://conf.video/opening_ru_en.mp4",
      "https://conf.video/panel_ru_en.mp4"
    ],
    "multilingual": "true",
    "multilingual_threshold": 0.7,
    "summary_mode": "notes",
    "diarize": true,
    "diarize_backend": "pyannote"
  }
}
```

---

## Error Scenarios

### Scenario 1: One Video Fails

```json
[
  {"id": 1, "type": "progress", "percent": 35, "message": "[1/3] Downloaded successfully"},
  {"id": 1, "type": "progress", "percent": 40, "message": "[2/3] Error: Failed to extract audio (corruption)"},
  {"id": 1, "type": "progress", "percent": 65, "message": "[3/3] Downloaded successfully"},
  {"id": 1, "type": "progress", "percent": 70, "message": "Loading summarization model..."},
  {
    "id": 1,
    "type": "result",
    "markdown": "# Batch Report\n\n**Videos processed:** 2\n\nNote: 1 video failed (see log)\n...",
    "output_path": "...",
    "videos_processed": 2
  }
]
```

### Scenario 2: All Videos Fail

```json
{
  "id": 1,
  "type": "error",
  "message": "No videos successfully processed"
}
```

### Scenario 3: No Sources Provided

```json
{
  "id": 1,
  "type": "error",
  "message": "No sources provided for batch processing"
}
```

### Scenario 4: User Cancellation

```json
[
  {"id": 1, "type": "progress", "percent": 45, "message": "[2/5] Transcribing..."},
  {"id": 1, "type": "error", "message": "Cancelled"}
]
```

---

## Implementation Details for Frontend

### JavaScript/Electron Example

```javascript
const { spawn } = require('child_process');
const path = require('path');

class TranscriberBackend {
  constructor(pythonPath) {
    this.process = spawn('python', [
      path.join(__dirname, 'backend', 'main.py')
    ]);
    this.requestId = 0;
    this.handlers = new Map();

    this.process.stdout.on('data', (data) => {
      this.handleMessage(JSON.parse(data.toString()));
    });
  }

  processBatch(sources, config, onProgress, onError) {
    const id = ++this.requestId;

    this.handlers.set(id, {
      onProgress: (msg) => onProgress(msg.percent, msg.message),
      onResult: (msg) => console.log('Done:', msg.output_path),
      onError: (msg) => onError(msg.message)
    });

    const request = {
      method: 'process_batch',
      id: id,
      params: {
        sources: sources,
        config: config
      }
    };

    this.process.stdin.write(JSON.stringify(request) + '\n');
  }

  handleMessage(msg) {
    const handler = this.handlers.get(msg.id);
    if (!handler) return;

    if (msg.type === 'progress') {
      handler.onProgress(msg);
    } else if (msg.type === 'result') {
      handler.onResult(msg);
      this.handlers.delete(msg.id);
    } else if (msg.type === 'error') {
      handler.onError(msg);
      this.handlers.delete(msg.id);
    }
  }
}

// Usage
const backend = new TranscriberBackend();

backend.processBatch(
  [
    '/path/to/call1.mp4',
    '/path/to/call2.mp4'
  ],
  {
    whisper_model: 'small',
    summary_mode: 'notes'
  },
  (percent, message) => {
    console.log(`${percent}% ${message}`);
  },
  (error) => {
    console.error('Error:', error);
  }
);
```

---

## Notes

1. **Request ID** — Use unique integer IDs to match requests/responses
2. **Progress messages** — Streamed before final result
3. **Output file** — Always saved with timestamp suffix
4. **Model caching** — First run may take time (downloading models)
5. **Cancellation** — Send any message (or nothing) to cancel current operation
6. **Source validation** — URLs must be HTTP/HTTPS or valid file paths

---

## See Also

- `backend/BATCH_MODE_USAGE.md` — Practical usage examples
- `backend/BATCH_MODE_IMPLEMENTATION.md` — Technical deep dive
- `backend/main.py` — Implementation source code
