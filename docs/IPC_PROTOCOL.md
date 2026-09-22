# IPC Protocol Reference

This document describes the runtime contract between Electron and the Python backend.

## Transport

- Channel type: newline-delimited JSON (NDJSON), not HTTP.
- Electron -> Python: `stdin` of `backend/main.py`.
- Python -> Electron: `stdout` of `backend/main.py`.
- One JSON object per line.
- UTF-8 is forced on Windows in `backend/main.py`.

## Process lifecycle

- Electron starts Python from `electron/main.js` via:
  - command: `python` (Windows) or `python3` (other platforms)
  - args: `-u backend/main.py`
- Python is treated as ready right after `spawn` (`pythonReady = true`).
- If process exits with non-zero code, renderer receives `type: "error"` on `python-message`.

## Electron IPC surface

`window.api` is defined in `electron/preload.js`.

### `invoke`-style channels

- `select-file` -> returns `string | null` (file path).
- `select-output-dir` -> returns `string | null` (directory path).
- `open-file(filePath)` -> opens file in OS shell.
- `open-folder(filePath)` -> opens folder and reveals file.
- `list-models` -> backend `method: "list_models"`, returns model registry object.
- `get-gpu-info` -> backend `method: "get_gpu_info"`, returns:
  - `cuda: boolean`
  - `device: string`
  - `vram_mb: number`
- `estimate-eta(params)` -> backend `method: "estimate_eta"`, returns ETA object.

### `send`-style channels

- `start-process(params)` -> backend message `{ id, method: "process", params }`.
- `cancel-process()` -> backend message `{ id, method: "cancel" }`.

### Renderer subscription channel

- `window.api.onMessage(callback)` subscribes to `python-message`.
- All backend stream events and most RPC responses are forwarded there.

## Backend request schema (`backend/main.py`)

Common request envelope:

```json
{
  "id": 123456789,
  "method": "process",
  "params": {}
}
```

`id` is used to correlate request/response and stream events.

### Methods

#### `process`

Primary single-source pipeline call.

Expected `params`:

- `source: string` (URL or local file path) - required.
- `config: object` - optional, parsed by `Config`.
- Optional top-level overrides:
  - `llm_model_preset`
  - `whisper_model`
  - `summary_mode`
  - `summary_mode_config`
  - `multilingual`
  - `multilingual_threshold`
  - `summary_language`
  - `diarize`
  - `num_speakers`
  - `speaker_names`
  - `diarize_backend`
  - `output_dir`

Result:

```json
{
  "id": 123456789,
  "type": "result",
  "markdown": "...",
  "output_path": "C:/.../video_summary.md",
  "metrics": { "...": "..." }
}
```

Error:

```json
{
  "id": 123456789,
  "type": "error",
  "message": "..."
}
```

#### `process_batch`

Batch pipeline call (implemented in Python, not exposed via current `preload.js` API).

Expected `params`:

- `sources: string[]` - required.
- `config: object` - optional.
- same optional overrides as `process`.

Result:

```json
{
  "id": 123456789,
  "type": "result",
  "markdown": "...",
  "output_path": "C:/.../batch_report_YYYYMMDD_HHMMSS.md",
  "videos_processed": 3
}
```

#### `cancel`

Sets cancel flag in `Pipeline`. In-flight job then fails with an error-like terminal message (`Cancelled`).

#### `list_models`

Returns:

```json
{
  "id": 123,
  "type": "result",
  "models": { "...": "..." }
}
```

#### `get_gpu_info`

Returns successful result even on detection failure (fallback values):

```json
{
  "id": 123,
  "type": "result",
  "cuda": false,
  "device": "",
  "vram_mb": 0
}
```

#### `estimate_eta`

Expected `params`:

- `duration_sec: number` - required (`None` is rejected in backend).
- `config: object` - optional.

Returns:

```json
{
  "id": 123,
  "type": "result",
  "eta": {
    "duration_sec": 3600,
    "transcribe_sec": 900,
    "summarize_sec": 180,
    "diarize_sec": 0,
    "total_sec": 1080,
    "formatted_total": "18m",
    "source": "calibrated"
  }
}
```

## Stream event schema (from pipeline progress callback)

### `type: "progress"`

```json
{
  "id": 123456789,
  "type": "progress",
  "stage": "transcribe",
  "percent": 42,
  "message": "Chunk 2/5: transcribing..."
}
```

Typical stage values:

- single mode: `download`, `extract`, `diarize`, `transcribe`, `summarize`, `format`
- batch mode: `batch`

### `type: "eta"`

Pre-run estimate:

```json
{
  "id": 123456789,
  "type": "eta",
  "stage": "pre",
  "duration_sec": 3600,
  "transcribe_sec": 900,
  "summarize_sec": 180,
  "diarize_sec": 0,
  "total_sec": 1080,
  "source": "static"
}
```

Runtime estimate:

```json
{
  "id": 123456789,
  "type": "eta",
  "stage": "runtime",
  "remaining_sec": 150,
  "elapsed_sec": 920
}
```

## Important behavior notes

- `electron/main.js` resolves pending RPC promises by `id`, but still forwards the same message to `python-message`.
- `window.api.onMessage` in `preload.js` does not currently expose an unsubscribe helper.
- `process_batch` exists in backend protocol but is not wired to renderer API by default.
- `cancel` is cooperative: cancellation is observed between pipeline stages and checks.
