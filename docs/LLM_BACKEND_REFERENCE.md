# LLM backend reference

Dense, module-oriented map of `backend/*.py` for navigation and safe edits. Transport and Electron wiring live in `docs/IPC_PROTOCOL.md` and `docs/LLM_GUIDE.md`.

| Module | Role |
|--------|------|
| `main.py` | Stdio NDJSON loop, method dispatch, threads for long work |
| `pipeline.py` | End-to-end orchestration, validation, progress/ETA, file output |
| `transcriber.py` | faster-whisper ASR, multilingual paths, chunking, checkpoints |
| `summarizer.py` | llama-cpp GGUF load, prompts, chunk/map-reduce summarization |
| `audio.py` | ffprobe/ffmpeg duration, extract, chunking helpers |
| `diarizer.py` | Speaker turns (`speechbrain` / `pyannote` / `energy`) |
| `formatter.py` | Markdown for single run and batch |
| `eta.py` | Static + calibrated ETA (`~/.localvideotranscriber/perf_stats.json`) |
| `models_registry.py` | Whisper + LLM preset metadata, local GGUF scan, preset resolution |
| `config.py` | Pydantic `Config` defaults and field names |

---

## `main.py`

**Responsibilities:** UTF-8 stdio on Windows; NVIDIA DLL registration and **main-thread** preload of `faster_whisper`, `ctranslate2`, `llama_cpp` (Windows threading rationale in code); `send()` JSON lines to stdout; read stdin loop and dispatch `method`.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `_register_nvidia_dll_dirs` | `os.add_dll_directory`, PATH, ctypes preload for CUDA DLLs |
| `send(msg)` | `json.dumps` + newline to stdout (Electron consumer) |
| `main()` | `Pipeline(progress_callback=send)`, per-line `json.loads`, thread workers |

**Input / output**

| Direction | Contract |
|-----------|----------|
| stdin | One JSON object per line: `method`, `id`, optional `params` |
| stdout | NDJSON: `progress`, `eta`, `result`, `error` (see IPC doc) |
| stderr | Human logs; not part of protocol |

**Side effects / caveats**

- `process` / `process_batch` run in **daemon threads**; exceptions → `type: "error"`.
- `get_gpu_info`: on exception, still sends `type: "result"` with `cuda: false` (not `error`).
- Comment block near EOF lists `process` params; `diarize_backend` comment omits `speechbrain` — **runtime validation is in `pipeline.py`** (`energy`, `pyannote`, `speechbrain`).

**When to edit**

- New RPC `method`, changing threading, or startup import order / Windows CUDA setup.

---

## `pipeline.py`

**Responsibilities:** `Pipeline.run` / `run_batch`: download or resolve file → duration/ETA → optional diarize → transcribe (including long-video streaming chunk path) → summarize → `formatter` → write `{stem}_summary.md` or `batch_report_*.md`; optional metrics JSON under `output_dir/metrics/`; `record_run` for ETA calibration; `gc.collect()` between heavy stages.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `_validate_source` | URL (`http`/`https`) or filesystem path; logs absolute paths |
| `_validate_output_dir` | Rejects `..` in `output_dir` string |
| `_normalize_runtime_config` | In-place fixes for bad types / empty strings |
| `Pipeline` | `cancel()`, `_progress`, `_eta`, `run`, `run_batch` |
| `_transcribe_prechunked` | Merge timed segments from pre-listed WAV chunks |
| `_transcribe_streaming_source` | Long media: `extract_audio_slice` + per-chunk transcribe |
| `_write_baseline_metrics` | `metrics/{stem}_metrics.json` |

**Input / output**

| API | In | Out |
|-----|----|-----|
| `run(msg_id, params)` | `params["source"]`, `params.get("config")`, optional top-level overrides (see `run` body) | `dict`: `markdown`, `output_path`, `metrics` |
| `run_batch(msg_id, params)` | `params["sources"]` (list), same config pattern | `dict`: `markdown`, `output_path`, `videos_processed` |
| `cancel()` | — | Sets cancel event; raises `"Cancelled"` in workers |

**Side effects / caveats**

- Long video (`duration >= long_video_threshold_sec` and **not** `diarize`): diarization skipped; uses streaming chunk transcription.
- `output_dir` comes from `params.get("output_dir", config.output_dir)` after validation.
- Batch: failed per-video entries log generic message; successful items only in `batch_results`.

**When to edit**

- Stage order, progress/ETA shaping, security rules for paths, batch aggregation, metrics payload.

---

## `transcriber.py`

**Responsibilities:** Load faster-whisper model; fast-path vs multilingual detection; internal chunked ASR for long files; merge diarization labels into segments; optional ASR checkpoint files under `config.checkpoint_dir`.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `ASRRuntime` | `WhisperModel` wrapper; `close()` unloads weights, parks ref (Windows destructor workaround) |
| `_transcribe_options` | Beam/VAD/word_timestamps + **fast profile** when duration ≥ `fast_profile_min_duration_sec` |
| `transcribe_single_language`, `_transcribe_with_language_fallback`, `_transcribe_chunked` | ASR paths |
| `_merge_diarization_with_segments` | Attaches `speaker` to segment dicts |
| `transcribe(...)` | Public entry: `list[dict]` segments `start`, `end`, `text`, `language`, optional `speaker` |

**Input / output**

| Parameter | Role |
|-----------|------|
| `wav_path` | Media path (pipeline often passes original file, not only WAV) |
| `diarization` | Optional list of `start`/`end`/`speaker` turns |
| `runtime` | Reuse model (batch); if `None`, creates and closes owned runtime |
| `allow_chunking` | When `False`, skips internal long-file chunking (pipeline chunk callers) |

**Side effects / caveats**

- `initial_prompt` in `_transcribe_options` is Russian profanity-aware instruction (fixed string).
- Long file: `_transcribe_chunked` uses checkpoints when `enable_checkpointing`.

**When to edit**

- Whisper parameters, chunking/checkpoint policy, language detection, diarization merge behavior.

---

## `summarizer.py`

**Responsibilities:** Resolve GGUF path (preset → `llm_model_path` → repo scan → `hf_hub_download`); build `Llama`; token-chunk transcript; per-mode prompts under `backend/prompts/{mode}/`; map-reduce / tree-reduce; optional summary checkpoints; `summary_mode_config` placeholder substitution.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `_detect_gpu_info` | `dict` `cuda`, `device`, `vram_mb` (used by `main` RPC) |
| `ALLOWED_MODES`, `_validate_mode` | Whitelist: `notes`, `call_check`, `factcheck`, `tldr`; unknown → `notes` |
| `_load_prompt_template` | `chunk` / `final` / `batch_chunk` / `batch_final` files |
| `_chunk_segments`, `_tree_reduce`, `_llm_call` | Chunking + LLM + repetition/thinking-tag hygiene |
| `summarize(...)` | Returns single markdown string |

**Input / output**

| Parameter | Role |
|-----------|------|
| `batch_mode` | Selects batch prompt phases |
| `transcript_language` | Summary target language resolution (with `languages`) |
| `checkpoint_key` | Filename fragment for summary checkpoint |

**Side effects / caveats**

- Writes under `config.checkpoint_dir` for summaries when enabled.
- `Config._allowed_summary_modes` in `config.py` should stay aligned with `ALLOWED_MODES` (duplicated concept).

**When to edit**

- New summary mode (prompts + whitelist), LLM params, chunking, tree-reduce, model resolution.

---

## `audio.py`

**Responsibilities:** `ffprobe`/ffmpeg duration; full-file WAV extract (16 kHz mono); progress via temp progress file on Windows; `extract_audio_chunks`, `extract_audio_slice`, `split_audio_chunks` for long-media and tests.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `get_duration` | Returns `float` seconds; raises `RuntimeError` if tools missing |
| `extract_audio` | WAV output; stall/total timeouts derived from `duration_sec` and config when passed |
| `extract_audio_slice` | Bounded segment to WAV (used by pipeline streaming path) |

**Input / output**

- Paths as `Path`; callbacks optional `(percent, message)` where implemented.

**Side effects / caveats**

- Spawns `ffmpeg`/`ffprobe`; progress file naming uses PID.
- Missing ffmpeg: explicit `RuntimeError` with install hints.

**When to edit**

- FFmpeg args, timeouts, chunk boundaries, progress reporting.

---

## `diarizer.py`

**Responsibilities:** If `config.diarize` is false, return `[]`. Else dispatch: `speechbrain`, `pyannote`, or default **energy** backend. Returns speaker turns for merger in `transcriber`.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `_diarize_speechbrain` | Primary default path |
| `_diarize_pyannote` | Optional heavier pipeline |
| `_diarize_energy_based` | Fallback / `else` branch |
| `diarize(wav_path, config, progress_cb)` | Public API: `list[{"start","end","speaker"}]` |

**Side effects / caveats**

- Loads ML stacks per backend; `gc.collect()` after speechbrain path.
- Backend string must match `pipeline` validation set.

**When to edit**

- Backend selection, clustering thresholds, performance, output turn shape.

---

## `formatter.py`

**Responsibilities:** Pure string building: single-file markdown (`format_markdown`) and batch report (`format_batch_markdown`); speaker grouping when segments include `speaker`; timestamp formatting.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `format_markdown(title, source, segments, summary, speaker_names)` | One run |
| `format_batch_markdown(batch_results, combined_summary, speaker_names)` | Batch layout |
| `_format_timestamp`, `_get_speaker_name` | Helpers |

**Input / output**

- `segments`: `start`, `end`, `text`, optional `speaker`.
- `batch_results`: `title`, `source`, `segments`, `duration`.

**Side effects**

- None (no I/O).

**When to edit**

- Output sections, metadata lines, grouping rules, new export fields (then wire from `pipeline`).

---

## `eta.py`

**Responsibilities:** Rough RT factors per `(whisper_model, device)`; LLM chunk time defaults; optional **calibration** from `~/.localvideotranscriber/perf_stats.json`; `estimate` returns component seconds + `source` static/calibrated; `format_eta` human string.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `RT_FACTORS`, `LLM_CHUNK_TIME_*`, `DIARIZATION_*_MULTIPLIER` | Tunables |
| `load_stats` / `save_stats` / `record_run` | Persistence |
| `estimate(config, duration_sec, device_resolved)` | Main math |
| `format_eta(seconds)` | Display |

**Input / output**

- `device_resolved`: normalized to `cuda` or `cpu`.
- Unknown whisper model: stderr warning + conservative default factor.

**Side effects**

- Reads/writes perf stats file (best-effort save).

**When to edit**

- Heuristics, calibration key scheme (must match `pipeline`’s `record_run` keys), multilingual/long-chunk overhead terms.

---

## `models_registry.py`

**Responsibilities:** Static `WHISPER_MODELS` and `LLM_PRESETS` metadata for UI; recursive-ish GGUF discovery under project root + common subdirs; `list_models`, `resolve_llm_preset`.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `scan_local_ggufs(project_root)` | `*.gguf` list with `id` `local:<filename>` |
| `list_models(project_root)` | `{ whisper, llm: { local, presets } }` |
| `resolve_llm_preset(preset_id, project_root)` | Maps preset → `path` or `repo`+`file` |

**Side effects**

- Filesystem scan only.

**When to edit**

- New presets, labels, scan paths, preset ID scheme (keep in sync with `summarizer` / UI).

---

## `config.py`

**Responsibilities:** Single Pydantic `Config` for defaults: ASR, LLM, diarization, batch, checkpoints, output; `summary_mode` and `_allowed_summary_modes` set.

**Top symbols**

| Symbol | Purpose |
|--------|---------|
| `Config` | All tunable fields (see field list in source) |

**Input / output**

- Constructed from `dict` in `main`/`pipeline` via `Config(**params.get("config", {}))` plus runtime overrides on `params`.

**Side effects**

- None by itself.

**When to edit**

- New user-facing options (then wire UI + IPC + pipeline + summarizer/transcriber as needed); keep field names stable for stored JSON.

---

## Related (not in required list)

- `downloader.py` — `download_audio` for URLs.
- `languages.py` — summary language resolution used by `summarizer`.
- `backend/prompts/` — template files per mode.
