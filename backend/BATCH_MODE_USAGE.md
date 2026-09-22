# Batch Mode Usage Guide

## Quick Start

### Python API (Direct Usage)

```python
from pipeline import Pipeline

def progress_callback(msg):
    print(msg)

pipeline = Pipeline(progress_callback=progress_callback)

# Process multiple videos
result = pipeline.run_batch(
    msg_id=1,
    params={
        "sources": [
            "https://example.com/call1.mp4",
            "/local/path/call2.mp4",
            "https://youtube.com/watch?v=xyz"
        ],
        "config": {
            "whisper_model": "small",
            "llm_model_path": None,
            "summary_mode": "call_check",
            "diarize": True,
            "num_speakers": 2,
            "speaker_names": {
                "SPEAKER_00": "Manager",
                "SPEAKER_01": "Client"
            },
            "multilingual": "auto",
            "chunk_size_tokens": 1500,
        },
        "output_dir": "./output",
        "summary_mode_config": {
            "required_phrases": ["confirm budget", "next steps"],
            "red_flags": ["I don't know", "call back later"]
        }
    }
)

print(f"Processed {result['videos_processed']} videos")
print(f"Report saved to: {result['output_path']}")
print("\nMarkdown output:")
print(result['markdown'])
```

### JSON-RPC (From Electron)

```javascript
// Send to backend process
const request = {
  method: "process_batch",
  id: 1,
  params: {
    sources: [
      "https://drive.google.com/call1.mp4",
      "/home/user/call2.mp4"
    ],
    config: {
      whisper_model: "small",
      summary_mode: "call_check",
      diarize: true,
      num_speakers: 2
    },
    summary_mode_config: {
      required_phrases: ["confirm budget", "next steps"],
      red_flags: ["I don't know"]
    }
  }
};

// Send via JSON-RPC to backend
```

---

## Progress Tracking

Progress messages stream with stage="batch":

```python
def progress_callback(msg):
    msg_id = msg["id"]      # Same as request ID
    stage = msg["stage"]    # "batch"
    percent = msg["percent"] # 0-100
    message = msg["message"] # "Processing video 2/5..."

    # Example messages:
    # [1/5] Preparing source...
    # [1/5] Downloading... 50%
    # [1/5] Extracting audio...
    # [1/5] Detecting speakers...
    # [1/5] Transcribing... 80s / 120s
    # [1/5] Video processed: 450 segments
    # [2/5] Preparing source...
    # ...
    # Loading summarization model...
    # Synthesizing final summary...
    # Generating markdown...
    # Batch processing complete!
```

---

## Configuration Options

### Basic Config (Inherited from single-video mode)

```python
config = {
    "whisper_model": "tiny|base|small|medium|large",  # default: small
    "compute_type": "auto|int8|float16|float32",      # default: auto
    "device": "auto|cpu|cuda",                         # default: auto
    "language": None,  # None = auto-detect
    "beam_size": 5,
    "vad_filter": True,

    # Multilingual
    "multilingual": "auto|true|false",  # default: auto
    "multilingual_threshold": 0.8,

    # Summarization
    "llm_model_path": None,  # auto-download if null
    "llm_model_repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
    "llm_model_file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "llm_context_length": 4096,
    "llm_temperature": 0.1,
    "chunk_size_tokens": 1500,
    "chunk_overlap_tokens": 200,

    # Summary modes
    "summary_mode": "notes|call_check|factcheck|tldr",  # default: notes
    "summary_mode_config": {},  # mode-specific (see below)

    # Diarization
    "diarize": True,
    "num_speakers": None,  # None = auto-detect
    "speaker_names": {"SPEAKER_00": "Name1", "SPEAKER_01": "Name2"},
    "diarize_backend": "energy|pyannote",  # default: energy

    # Output
    "output_dir": "./output"
}
```

### Per-Mode Configuration

#### `summary_mode: "call_check"`

```python
"summary_mode_config": {
    "required_phrases": [
        "confirm budget",
        "tell me about your company",
        "next steps"
    ],
    "red_flags": [
        "I don't know",
        "call back tomorrow",
        "I'll check with management"
    ],
    "scoring_criteria": [
        "rapport_building",
        "needs_discovery",
        "objection_handling",
        "closing"
    ]
}
```

#### `summary_mode: "notes"`

```python
"summary_mode_config": {}  # Uses defaults, no customization needed
```

#### `summary_mode: "factcheck"`

```python
"summary_mode_config": {
    # No specific config, uses defaults
}
```

#### `summary_mode: "tldr"`

```python
"summary_mode_config": {
    "max_words": 150  # Max length of TLDR
}
```

---

## Output Format

### File Location
```
{output_dir}/batch_report_{YYYYMMDD}_{HHMMSS}.md
```

Example: `batch_report_20260405_143022.md`

### Structure

```markdown
# Batch Transcription Report

**Videos processed:** 3
**Total duration:** 45:30
**Total words:** 12,450

---

## Overall Summary

[Combined summary of all videos, identifying patterns and cross-video insights]

---

## Video 1: Call with Ivanov

**Source:** `https://example.com/call1.mp4`
**Duration:** 12:34
**Words:** 4,200
**Segments:** 145

### Transcript

**[00:00] Manager:** Hello, how are you today...
**[00:15] Client:** I'm doing well, thanks for calling...

---

## Video 2: Call with Petrov

**Source:** `/local/calls/call2.mp4`
**Duration:** 16:45
**Words:** 5,100
**Segments:** 180

### Transcript

**[00:00] Manager:** Good morning, let's discuss...
**[00:20] Client:** Sure, I'm ready...

---

## Video 3: Call with Smirnov

**Source:** `https://example.com/call3.mp4`
**Duration:** 16:11
**Words:** 3,150
**Segments:** 110

### Transcript

...
```

---

## Error Handling

### If one video fails:

```json
{
  "id": 1,
  "type": "progress",
  "stage": "batch",
  "percent": 35,
  "message": "[2/5] Error: Failed to download file (404)"
}
```

Batch continues processing remaining videos. Final result includes only successfully processed videos:

```json
{
  "id": 1,
  "type": "result",
  "markdown": "...",
  "output_path": "...",
  "videos_processed": 4  # Out of 5
}
```

### If all videos fail:

```json
{
  "id": 1,
  "type": "result",
  "markdown": "# Batch Report\n\n_No videos successfully processed._",
  "output_path": "...",
  "videos_processed": 0
}
```

### If no sources provided:

```json
{
  "id": 1,
  "type": "error",
  "message": "No sources provided for batch processing"
}
```

---

## Cancellation

```python
# Request cancellation
pipeline.cancel()

# Backend will:
# 1. Stop downloading/processing current video
# 2. Raise Exception("Cancelled")
# 3. Send error response

# Client sees:
{
  "id": 1,
  "type": "error",
  "message": "Cancelled"
}
```

---

## Performance Tips

### For Large Batches (10+ videos)

1. **Use smaller model** for faster transcription:
   ```python
   "whisper_model": "tiny"  # vs default "small"
   ```

2. **Disable diarization** if not needed:
   ```python
   "diarize": False
   ```

3. **Use energy-based diarization** (default) instead of pyannote:
   ```python
   "diarize_backend": "energy"
   ```

4. **Increase chunk size** to reduce summarization time:
   ```python
   "chunk_size_tokens": 2500  # vs default 1500
   ```

5. **Disable VAD** if low-quality audio:
   ```python
   "vad_filter": False
   ```

### Example Fast Config

```python
config = {
    "whisper_model": "tiny",
    "llm_model_path": "/path/to/local/model.gguf",
    "summary_mode": "tldr",
    "diarize": False,
    "multilingual": "false",
    "vad_filter": False,
    "chunk_size_tokens": 2500,
}
```

---

## Batch-Specific Prompts (Optional)

To customize behavior for batch mode, create:

```
backend/prompts/{mode}/batch_chunk.txt
backend/prompts/{mode}/batch_final.txt
```

If these files exist, batch mode uses them. Otherwise falls back to:

```
backend/prompts/{mode}/chunk.txt
backend/prompts/{mode}/final.txt
```

### Example: `prompts/call_check/batch_final.txt`

```
You are a sales call analyst. You are reviewing {{VIDEO_COUNT}} sales calls from the same manager.

Analyze these {{VIDEO_COUNT}} call summaries to:
1. Grade each call independently (score/10)
2. Identify patterns (consistent strengths/weaknesses)
3. Note improvement areas
4. Recommend coaching points

Format:
## Individual Scores
- Call 1 (Ivanov): 7.5/10
- Call 2 (Petrov): 8.0/10
- Call 3 (Smirnov): 6.5/10

## Performance Patterns
- Strengths: ...
- Weaknesses: ...

## Coaching Recommendations
1. ...
2. ...

{{CHUNK_SUMMARIES}}
```

Template variables available:
- `{{CHUNK_SUMMARIES}}` — individual video summaries (automatic)
- Any custom variable from `summary_mode_config`

---

## Testing Checklist

- [ ] Test with 1 video (sanity check)
- [ ] Test with 3-5 videos (typical batch)
- [ ] Test with mix of URLs and local files
- [ ] Test cancellation mid-batch
- [ ] Test with diarization enabled
- [ ] Test with different summary modes
- [ ] Test error handling (one video fails)
- [ ] Check output file created in correct location
- [ ] Verify markdown formatting is correct
- [ ] Check progress messages stream correctly

---

## Troubleshooting

### "No sources provided"
Ensure `sources` is non-empty list of strings.

### "Cancelled" error
Normal if user clicked cancel. Graceful shutdown.

### Model download takes long time
First run downloads ~5GB. Subsequent runs use cached model.
Set `llm_model_path` to local path to skip download.

### Very slow summarization
Try smaller `llm_model_file` or increase `chunk_size_tokens`.

### Out of memory
Reduce `chunk_size_tokens` or process fewer videos per batch.

### Mixed language transcripts look wrong
Enable multilingual mode:
```python
"multilingual": "true"
```

### Diarization not detecting speaker changes
Try `pyannote` backend (requires HF token):
```python
"diarize_backend": "pyannote",
"num_speakers": 2  # or actual number
```

---

## See Also

- `BATCH_MODE_IMPLEMENTATION.md` — Technical details
- `API_EXAMPLES.md` — General API documentation
- `SUMMARY_MODES.md` — Summary mode specifics
- `config.py` — Configuration class definition
