# Function Index For LLMs

This index highlights the most important symbols and why they matter.
It is intentionally curated (not a dump of every helper).

## Backend entrypoint and protocol

### `backend/main.py`

- `_register_nvidia_dll_dirs()` - Windows CUDA DLL registration and preload workaround before ASR imports.
- `send(msg)` - canonical backend -> frontend JSON emitter (`stdout` line).
- `main()` - stdin loop, method dispatch, thread launch for `process` and `process_batch`.

## Pipeline orchestration

### `backend/pipeline.py`

- `_validate_source(source)` - URL/path validation and safety checks before processing.
- `_validate_output_dir(output_dir)` - output path traversal guard.
- `_normalize_runtime_config(config)` - normalizes unsafe/null runtime values.
- `Pipeline.cancel()` - sets cooperative cancellation event.
- `Pipeline._progress(msg_id, stage, percent, message)` - throttled progress emitter used by UI.
- `Pipeline._eta(msg_id, stage, eta_dict)` - ETA event emitter.
- `Pipeline._transcribe_streaming_source(...)` - long-media mode, chunk extraction + incremental ASR merge.
- `Pipeline._transcribe_prechunked(...)` - pre-split chunk transcription and overlap dedupe.
- `Pipeline.run(msg_id, params)` - single-source full flow.
- `Pipeline.run_batch(msg_id, params)` - multi-source flow with aggregate summary and report.

## Transcription subsystem

### `backend/transcriber.py`

- `_resolve_device_and_compute(config)` - resolves `auto` device/compute policy.
- `ASRRuntime` - lifecycle wrapper around Faster-Whisper model.
- `ASRRuntime.close()` - explicit cleanup with Windows CT2 crash workaround (`_LEAKED_CT2_REFS`).
- `_detect_languages(...)` - probes language map before final mode decision.
- `_is_multilingual(...)` - threshold-based multilingual classifier.
- `_transcribe_with_language_fallback(...)` - resilient language-aware pass for mixed content.
- `transcribe_single_language(...)` - fast path when language is fixed.
- `_transcribe_segment_based(...)` - segmented transcription path.
- `_transcribe_chunked(...)` - long audio chunk mode with checkpoint support.
- `_load_asr_checkpoint(...)` / `_save_asr_checkpoint(...)` - resume support for long jobs.
- `_merge_diarization_with_segments(...)` - merges speaker turns into ASR segments.
- `transcribe(...)` - top-level transcription API used by pipeline.

## Summarization subsystem

### `backend/summarizer.py`

- `_detect_gpu_info()` - runtime CUDA/VRAM detection used by UI and context sizing.
- `_auto_context_length(...)` - adaptive `n_ctx` selection from model/VRAM hints.
- `_validate_mode(mode)` - summary mode whitelist guard.
- `_load_prompt_template(mode, phase)` - template loader from `backend/prompts/`.
- `_validate_config_value(key, value)` - safe value validation for prompt substitution.
- `_substitute_config_variables(template, config_dict)` - controlled `{{KEY}}` replacement.
- `_chunk_segments(...)` - transcript -> token chunks with overlap.
- `_find_local_gguf()` - local model discovery fallback.
- `_build_llm_kwargs(...)` - canonical llama.cpp runtime kwargs builder.
- `_llm_call(...)` - one LLM call wrapper (temperature/repetition/system prompt).
- `_tree_reduce(...)` - multi-pass reduction for large chunk sets.
- `_run_chunk_summary(...)` - single chunk summarization executor.
- `_load_summary_checkpoint(...)` / `_save_summary_checkpoint(...)` - checkpointed summarization resume.
- `summarize(...)` - top-level summarization API (single + batch mode).

## Audio and source handling

### `backend/audio.py`

- `get_duration(input_path)` - duration probe (`ffprobe`, then ffmpeg fallback).
- `extract_audio(...)` - robust ffmpeg extraction with stall/timeout protections.
- `extract_audio_slice(...)` - partial extraction used by streaming long mode.
- `extract_audio_chunks(...)` - pre-chunk extraction utility.
- `split_audio_chunks(...)` - chunk planning and splitting helper.

### `backend/downloader.py`

- `download_audio(url, output_dir, progress_cb)` - yt-dlp wrapper with WAV output normalization.

## Diarization and formatting

### `backend/diarizer.py`

- `_diarize_speechbrain(...)` - default diarization backend.
- `_diarize_pyannote(...)` - optional high-quality diarization backend.
- `_diarize_energy_based(...)` - lightweight fallback diarization.
- `diarize(...)` - dispatcher with backend selection and fallback strategy.

### `backend/formatter.py`

- `_format_timestamp(seconds)` - canonical timestamp format helper.
- `_get_speaker_name(speaker_id, speaker_names)` - display label mapping.
- `format_markdown(...)` - single-report markdown output.
- `format_batch_markdown(...)` - batch-report markdown output.

## Runtime metadata and model registry

### `backend/eta.py`

- `estimate(config, duration_sec, device_resolved)` - static + calibrated ETA estimator.
- `record_run(kind, key, observed_value)` - stores observed performance stats.
- `format_eta(seconds)` - human-readable ETA string.
- `load_stats()` / `save_stats()` - calibration persistence IO.

### `backend/models_registry.py`

- `scan_local_ggufs(project_root)` - local GGUF indexer.
- `list_models(project_root)` - combined preset + local model list for UI.
- `resolve_llm_preset(preset_id, project_root)` - preset resolver, including `local:<file>.gguf`.

## UI / Electron boundary

### `electron/main.js`

- `startPython()` - spawns backend and binds stdout/stderr/exit handlers.
- `sendToPython(msg)` - writes NDJSON request to backend stdin.
- `sendToRenderer(msg)` - forwards backend events to renderer and resolves pending RPC by `id`.
- `rpcCall(method, params, timeoutMs)` - request/response helper for `list_models`, `get_gpu_info`, `estimate_eta`.

### `electron/preload.js`

- `contextBridge.exposeInMainWorld('api', ...)` - only allowed renderer-facing API.
- `onMessage(callback)` - subscription to `python-message` stream.

### `electron/renderer/app.js`

- `loadModels()` - model catalog bootstrapping and UI dropdown population.
- `loadGpuInfo()` - runtime GPU info fetch and banner update.
- `estimateETA()` - helper for explicit ETA call (currently not wired into main start flow).
- `queueProgressUpdate(msg)` / `flushProgressUpdate()` - UI progress update throttling.
- `setStage(activeStage)` - stage-state visualization.
- `renderMarkdown(md)` - final markdown rendering in UI panel.

## Tests worth reading first

- `backend/tests/test_pipeline.py` - orchestration behavior and progress semantics.
- `backend/tests/test_summarizer.py` - prompt/template and summarization guards.
- `backend/tests/test_config.py` - config defaults and invariant expectations.
- `backend/tests/integration/test_integration_pipeline.py` - end-to-end behavior.
- `backend/tests/integration/test_integration_audio.py` - audio probing/extraction/chunking.
- `backend/tests/integration/test_integration_edge_cases.py` - failure and boundary scenarios.
