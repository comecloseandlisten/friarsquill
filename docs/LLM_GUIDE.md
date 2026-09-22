# LLM guide — localvideotranscriber (Friar's Quill)

Concise map for AI assistants and developers. Paths are relative to the repository root.

Deep references:

- `docs/IPC_PROTOCOL.md` — complete Electron ↔ Python contract with message schemas.
- `docs/FUNCTION_INDEX.md` — curated index of high-impact functions/classes across modules.
- `docs/LLM_TASK_MATRIX.md` — task intent matrix: primary files, anchor symbols, validation, pitfalls.
- `docs/LLM_BACKEND_REFERENCE.md` — backend module map: symbols, I/O contracts, side effects, edit signals.
- `docs/CHANGE_PLAYBOOK.md` — task-based playbook (what to edit, pitfalls, how to validate).

## What this project is

- **Desktop**: Electron (`package.json` → `main`: `electron/main.js`).
- **Backend**: Python 3.10+ script `backend/main.py`, spawned as `python -u backend/main.py` (see `electron/main.js`).
- **Transport**: One JSON object per line on **stdin** (Electron → Python) and **stdout** (Python → Electron). Not HTTP.
- **Purpose**: Transcribe audio/video (local file or URL via yt-dlp), optionally diarize speakers, summarize with a local GGUF LLM, write Markdown under `output_dir`.

## Entry points

| Layer | File | Role |
|--------|------|------|
| Electron bootstrap | `electron/main.js` | `BrowserWindow`, `spawn` Python, `ipcMain` handlers, stdout line parser → `webContents.send('python-message', msg)` |
| Renderer API | `electron/preload.js` | `contextBridge.exposeInMainWorld('api', { ... })` |
| UI | `electron/renderer/app.js`, `index.html` | Builds params, `window.api.startProcess(params)`, subscribes via `window.api.onMessage` |
| Python loop | `backend/main.py` | Reads stdin lines → `json.loads` → dispatches by `method`; threads for `process` / `process_batch` |
| Orchestration | `backend/pipeline.py` | Class `Pipeline`: `run`, `run_batch`, `cancel` |

## Python stdin protocol (`backend/main.py`)

Each line is a JSON object. Important keys:

- `id` — correlates async `result` / `error` / streaming events.
- `method` — one of:
  - `process` — `params`: `source` (URL or path), `config` (dict for `Config`), optional overrides (`summary_mode`, `diarize`, `output_dir`, etc.); see comment block near end of `backend/main.py`.
  - `process_batch` — `params`: `sources` (list of strings), same config overrides as `process`.
  - `cancel` — sets `Pipeline` cancel event.
  - `list_models` — returns registry + local GGUFs via `models_registry.list_models`.
  - `get_gpu_info` — uses `summarizer._detect_gpu_info`.
  - `estimate_eta` — `params`: `duration_sec`, `config`; uses `eta.estimate` / `eta.format_eta`.

**Stdout messages** (one JSON per line): `type` is typically `progress`, `eta`, `result`, or `error`. Progress includes `stage`, `percent`, `message`. Electron forwards all parsed lines to the renderer as `python-message`.

**Note:** Current UI wiring sends `process` only (`ipcMain.on('start-process')` → `sendToPython`). `process_batch` is implemented in Python but not exposed as a separate IPC channel in `electron/main.js`.

## Renderer ↔ main process (`preload.js`)

Exposed on `window.api`:

- `selectFile`, `selectOutputDir` — dialogs
- `startProcess(params)`, `cancelProcess`
- `onMessage(callback)` — all backend JSON events
- `openFile`, `openFolder`
- `listModels`, `getGpuInfo`, `estimateEta`

## Pipeline flow (`Pipeline.run` in `backend/pipeline.py`)

High-level order:

1. **download** — URL → `downloader.download_audio`; local path → resolve and verify exists.
2. **extract** — Currently a no-op progress step; audio is passed through (comment in code: faster-whisper / diarizer accept source format via ffmpeg stack).
3. **ETA** — Optional `type: "eta"` events (`pre`, `runtime`) via `eta.estimate`.
4. **diarize** — If `config.diarize` and not long pre-chunked mode: `diarizer.diarize`. Backends: `speechbrain`, `pyannote`, `energy` (`config.diarize_backend`).
5. **transcribe** — `transcriber.transcribe` with optional `ASRRuntime` reuse; long videos may use internal streaming/chunk path (`_transcribe_streaming_source` / `_transcribe_prechunked`).
6. **summarize** — `summarizer.summarize` (map-reduce over chunks; checkpointing supported).
7. **format** — `formatter.format_markdown` → `{stem}_summary.md` under validated `output_dir`.

### Summarization: modular topics (`backend/summarizer.py`)

When `topic_segmentation` is true and the mode is not `inquisition` / `bard` (batch uses its own path), the default **notes** flow is:

1. **MAP** — each transcript chunk → chunk prompt (`prompts/{mode}/chunk.txt`); `max_tokens` is capped only if `config.summary_chunk_max_tokens > 0` (default **0** = use `calibrate.calibrate_generation_budget` only, same idea as the older reference implementation in `backend/summarizer легаси.py`).
2. **Group** — adjacent chunk summaries grouped by heading vocabulary (short videos: one group per chunk).
3. **Topic reduce** — every group, including **single-chunk** groups, is passed through `_tree_reduce` with `prompts/_shared/topic_reduce.txt` so the section reads like a cohesive chapter (legacy quality came largely from a single strong `final.txt` pass; topic_reduce is that pass **per topic**).
4. **Assemble** — `_assemble_final`: LLM overview + topic bodies + LLM conclusion (`prompts/_shared/overview.txt`, `conclusion.txt`).

Older one-shot behavior was MAP → one `final.txt` `_tree_reduce` over all chunks (see reference file above). The modular path keeps **topic boundaries** but must not skip synthesis on a lone chunk, or the middle of the doc stays at raw MAP quality.

**Batch:** `run_batch` aggregates multiple sources, emits `stage: "batch"` progress, then combined summarization / `formatter.format_batch_markdown`.

## Configuration (`backend/config.py`)

`Config` is a Pydantic `BaseModel` — single source of truth for defaults and field names. Notable groups:

- **ASR**: `whisper_model`, `device`, `compute_type`, `language`, `multilingual`, VAD, beam sizes, long-video thresholds, checkpoint fields.
- **LLM**: `llm_model_preset`, `llm_model_path`, `llm_model_repo`, `llm_model_file`, `llm_gpu_layers`, chunk token sizes, `summary_chunk_max_tokens` (0 = no cap on MAP budget), `summary_mode` (`notes`, `inquisition`, `call_check`, `factcheck`, `tldr`), `summary_language`, `topic_segmentation`.
- **Diarization**: `diarize`, `num_speakers`, `speaker_names`, `diarize_backend`, `diarize_clustering_threshold`.
- **Output**: `output_dir`, `include_full_transcript`.

Runtime normalization: `pipeline._normalize_runtime_config`.

## Where to edit common tasks

Step-by-step recipes (pitfalls + tests) live in `docs/CHANGE_PLAYBOOK.md`.

| Task | Primary files |
|------|----------------|
| New UI control / layout | `electron/renderer/index.html`, `electron/renderer/app.js` |
| New IPC surface | `electron/main.js`, `electron/preload.js`, then `backend/main.py` if new `method` |
| Pipeline stages / progress | `backend/pipeline.py` |
| Whisper behavior, chunking, checkpoints | `backend/transcriber.py` |
| LLM prompts, chunking, GGUF loading | `backend/summarizer.py`; mode templates in `backend/prompts/{notes,inquisition,call_check,factcheck,tldr}/chunk.txt` and `final.txt` |
| Download behavior | `backend/downloader.py` |
| FFmpeg duration / extract | `backend/audio.py` |
| Speaker diarization algorithms | `backend/diarizer.py` |
| Markdown output shape | `backend/formatter.py` |
| Model lists for UI | `backend/models_registry.py` |
| ETA tuning | `backend/eta.py` |
| Security validation (source/output paths) | `backend/pipeline.py` (`_validate_source`, `_validate_output_dir`) |

## Tests

- **Unit**: `backend/tests/` (e.g. `test_pipeline.py`, `test_config.py`, `test_summarizer.py`).
- **Integration**: `backend/tests/integration/` with `conftest.py` fixtures; run from environment with deps and optional `test_data` (see `test_data/README.md`).

## Operating constraints (from code)

- **Windows**: `backend/main.py` registers NVIDIA DLL directories and preloads heavy imports on the main thread before worker threads (commented rationale: ctranslate2 / threading).
- **Memory**: ASR model is closed before LLM work; explicit `gc.collect()` between heavy stages in `pipeline.py`.

## Known behavior traps

- `electron/main.js` marks backend as ready immediately after `spawn`; first call can still race with Python startup on slow systems.
- `electron/main.js` resolves RPC responses by `id` but still forwards them to `python-message`; avoid assuming RPC responses are invisible to stream listeners.
- `electron/preload.js` `onMessage` currently has no unsubscribe helper; multiple subscriptions can duplicate UI handling.
- `backend/main.py` supports `process_batch`, but current renderer API does not expose it.
- `electron/renderer/app.js` has an `estimateETA()` helper, but it is not part of the main start flow.
