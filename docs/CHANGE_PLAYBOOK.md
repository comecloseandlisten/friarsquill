# Change playbook (task → files → validation)

Task-oriented guide for implementing behavior changes. Always align with `docs/IPC_PROTOCOL.md`, `docs/LLM_BACKEND_REFERENCE.md`, and real symbols in code.

---

## 1. Add a new summary mode

**Files to edit**

- `backend/summarizer.py` — extend `ALLOWED_MODES`; ensure `_load_prompt_template` can resolve the mode directory.
- `backend/config.py` — add mode to `_allowed_summary_modes` (set literal).
- `backend/prompts/<new_mode>/` — add `chunk.txt`, `final.txt`. Optional: `batch_chunk.txt` / `batch_final.txt`; if absent, batch mode falls back to regular chunk/final (`summarizer.summarize` try/except around batch template load).
- UI (optional): `electron/renderer/app.js`, `index.html` — expose mode in dropdown or settings.
- Tests: `backend/tests/test_summarizer.py` (prompt loading / mode validation).

**Steps**

1. Copy an existing mode folder under `backend/prompts/` as a template.
2. Add the mode name to both whitelists in `summarizer.py` and `config.py`.
3. If the mode needs `summary_mode_config` keys, add `{{placeholders}}` in templates and document keys (see `_substitute_config_variables` allowed logging).

**Pitfalls**

- Typo in mode string → silent fallback to `notes` in `_validate_mode`.
- Batch runs: without `batch_chunk.txt` / `batch_final.txt`, code uses the normal templates (still works).

**Validation**

- Unit: `pytest backend/tests/test_summarizer.py -q`
- Manual: send `process` with `summary_mode` set (Electron or stdin NDJSON).

---

## 2. Expose a new Python RPC method (`method` on stdin)

**Files to edit**

- `backend/main.py` — new `elif method == "..."` branch; use `send()` for replies.
- `electron/main.js` — `sendToPython` from a new `ipcMain.handle` or `on` handler; reuse RPC `id` pattern if async reply needed.
- `electron/preload.js` — expose wrapper on `window.api`.
- `docs/IPC_PROTOCOL.md` — document schema (if you maintain it for this change).

**Steps**

1. Define request/response JSON shape and error behavior.
2. Implement backend handler (keep CPU-heavy work in a thread like `process`).
3. Wire Electron main → preload → renderer.

**Pitfalls**

- Forgetting to `flush` stdout (use existing `send()`).
- Duplicate `onMessage` subscriptions in renderer if adding new listeners without cleanup.

**Validation**

- Manual: invoke from renderer or scripted stdin line to `python -u backend/main.py`.
- If test harness exists, extend integration tests under `backend/tests/integration/`.

---

## 3. Wire batch processing in the UI (`process_batch`)

**Files to edit**

- `electron/main.js` — send `method: "process_batch"` with `params.sources` (and shared `config`).
- `electron/preload.js` — e.g. `startBatchProcess(sources, config)`.
- `electron/renderer/app.js` / `index.html` — multi-select or list input, progress UI for `stage: "batch"`.
- Optional: `docs/IPC_PROTOCOL.md` examples.

**Steps**

1. Mirror `start-process` flow but with `sources: string[]`.
2. Handle `result.output_path` for `batch_report_*.md`.
3. Parse `progress` messages with stage `batch` (percent semantics differ from single run).

**Pitfalls**

- `run_batch` enforces `config.batch_max_files`; large lists error before work starts.
- Per-video errors do not fail entire batch; check `videos_processed` vs input length.

**Validation**

- `pytest backend/tests/test_pipeline.py` if batch paths covered; else manual two-file batch.
- Confirm `cancel` still sets `Pipeline._cancel_event` during batch.

---

## 4. Tune ETA (static tables or calibration)

**Files to edit**

- `backend/eta.py` — `RT_FACTORS`, `LLM_CHUNK_TIME_*`, `DIARIZATION_*_MULTIPLIER`, `TOKENS_PER_MINUTE_SPEECH`, long-video overhead in `estimate`.
- Optionally `backend/pipeline.py` — only if changing how `record_run` keys are computed (must stay consistent with `estimate`’s lookup keys).

**Steps**

1. Adjust static tables for new hardware assumptions or models.
2. If changing calibration key format, update both `record_run` callers in `pipeline.py` and readers in `eta.estimate`.

**Pitfalls**

- Unknown `whisper_model` in `RT_FACTORS` triggers stderr warning and conservative default.
- `get_gpu_info` failure path in `main` returns zeros — UI should not divide blindly.

**Validation**

- `pytest backend/tests/` for any ETA unit tests; manual `estimate_eta` RPC with `duration_sec` + `config`.

---

## 5. Change long-video / chunking behavior

**Files to edit**

- `backend/config.py` — `long_video_threshold_sec`, `long_video_chunk_sec`, `long_video_overlap_sec`, `ffmpeg_chunk_extract_timeout_sec`, `fast_profile_*`, `asr_checkpoint_*`.
- `backend/pipeline.py` — `use_prechunked_long_mode` gating (diarize interaction).
- `backend/transcriber.py` — `_transcribe_chunked`, `transcribe` threshold logic.
- `backend/audio.py` — `extract_audio_slice` timeouts and behavior.

**Steps**

1. Decide whether diarization must run on full file; if yes, **disable** long mode or change gating (today: long + diarize still skips prechunked skip for diarize stage only when `use_prechunked_long_mode` is false — read `run()` carefully).
2. Tune overlap to reduce duplicate text at chunk boundaries.

**Pitfalls**

- When `use_prechunked_long_mode` is true, diarization is skipped entirely for that run.
- Streaming path deletes intermediate chunk WAVs; debugging may need temporary logging.

**Validation**

- `backend/tests/integration/test_integration_transcription.py` or long-media fixtures in `test_data/`.
- Watch `metrics` JSON for `stage_times_sec`.

---

## 6. Add or change an LLM preset (Hugging Face or local)

**Files to edit**

- `backend/models_registry.py` — append to `LLM_PRESETS` (`id`, `label`, `repo`, `file`, `size_mb`).
- UI: `electron/renderer/app.js` — ensure preset id is sent as `llm_model_preset` in `config` or top-level params (match existing `process` payload).

**Steps**

1. Add preset entry; verify `resolve_llm_preset` returns `repo`+`file` or `path` for `local:` ids.
2. `summarizer.summarize` already resolves `config.llm_model_preset` via `resolve_llm_preset`.

**Pitfalls**

- `local:` presets require `scan_local_ggufs` to find exact filename match.
- Large GGUF: `llm_context_length` 0 triggers VRAM-based auto sizing — may OOM on small GPUs.

**Validation**

- `list_models` RPC shows new preset.
- One full `process` with preset selected.

---

## 7. Change diarization backend default or policy

**Files to edit**

- `backend/config.py` — `diarize_backend` default (`speechbrain` / `pyannote` / `energy`).
- `backend/diarizer.py` — implementation per backend.
- `backend/pipeline.py` — validation tuple for `diarize_backend` in `run` / `run_batch`.
- `backend/eta.py` — `DIARIZATION_*_MULTIPLIER` if cost model should change.

**Steps**

1. Keep backend strings synchronized: `pipeline` raises `ValueError` if not in `("energy", "pyannote", "speechbrain")`.
2. `diarize()` `else` branch is energy — any new string requires `diarize()` dispatch update **and** pipeline validation.

**Pitfalls**

- pyannote may need extra deps or auth env vars (see `diarizer.py` implementation).
- Long-video mode skips diarization when `use_prechunked_long_mode`.

**Validation**

- `pytest backend/tests/test_diarizer.py -q`
- Short local file with `diarize: true` per backend.

---

## 8. Add a Markdown output field or section

**Files to edit**

- `backend/formatter.py` — extend `format_markdown` / `format_batch_markdown` signatures as needed.
- `backend/pipeline.py` — pass new data (from `metrics`, `config`, or segments) into formatter; adjust `return` dict if API should expose new fields to Electron.

**Steps**

1. Prefer backward-compatible additions (new optional kwargs).
2. If changing file on disk only, `pipeline` write path is `output_path.write_text(markdown, ...)`.

**Pitfalls**

- Empty `segments`: `format_markdown` uses zero duration and word count; summary section still renders.
- Batch word count joins segment texts — keep consistent if adding fields.

**Validation**

- `pytest backend/tests/test_formatter.py -q`
- Open generated `.md` after a run.

---

## 9. Adjust Whisper quality / speed (beam, VAD, word timestamps)

**Files to edit**

- `backend/config.py` — `beam_size`, `vad_filter`, `word_timestamps`, fast profile fields.
- `backend/transcriber.py` — `_transcribe_options` (defaults and fast profile), any `WhisperModel` usage.

**Steps**

1. Tune defaults or UI-exposed `config` keys.
2. Consider interaction: fast profile overrides beam/VAD/word_timestamps when duration ≥ `fast_profile_min_duration_sec`.

**Pitfalls**

- `initial_prompt` in `_transcribe_options` is hardcoded Russian instruction — changing behavior for other languages may need conditional logic.
- `ASRRuntime.close()` parks model references on Windows — do not assume process memory returns to baseline.

**Validation**

- `pytest backend/tests/test_pipeline.py` / transcription integration tests.
- Compare `metrics.stage_times_sec.transcribe` before/after.

---

## 10. Tighten or relax source / output path security

**Files to edit**

- `backend/pipeline.py` — `_validate_source`, `_validate_output_dir`.
- Possibly `backend/downloader.py` if URL schemes or download targets change.

**Steps**

1. Document allowed URL schemes and path forms.
2. Avoid breaking legitimate absolute Windows paths when rejecting traversal.

**Pitfalls**

- `_validate_output_dir` rejects `..` substring; nested valid paths without `..` still allowed.
- URL validation uses `urlparse`; file branch resolves `Path` but existence check is only for non-URL in `run()`.

**Validation**

- Unit tests with good/bad paths; try `..` in `output_dir` and expect `ValueError`.
- Integration: `test_integration_edge_cases.py` if applicable.

---

## 11. Change ASR checkpoint frequency or location

**Files to edit**

- `backend/config.py` — `enable_checkpointing`, `checkpoint_dir`, `asr_checkpoint_save_every_chunks`, `asr_checkpoint_save_interval_sec`.
- `backend/transcriber.py` — `_checkpoint_file`, `_save_asr_checkpoint`, `_load_asr_checkpoint`, `_transcribe_chunked`.

**Steps**

1. Align directory with `summary` checkpoints (same `checkpoint_dir` base by default).
2. If changing filename scheme, invalidate old checkpoints or handle miss gracefully (code returns empty cache).

**Pitfalls**

- Checkpoint mismatch on chunk count clears resume cache.
- Disk growth on long runs if save interval too aggressive.

**Validation**

- Interrupt a long transcription mid-run and restart (if product flow supports resume — verify actual behavior in `transcriber` before promising resume in UI).

---

## 12. Expose new `Config` fields end-to-end (UI + IPC + backend)

**Files to edit**

- `backend/config.py` — new fields with defaults.
- `backend/pipeline.py` — if top-level `params` override pattern is required, add mirrors like existing `summary_language` / `diarize` blocks.
- `electron/renderer/app.js` — collect value into object sent as `config` or top-level param.
- Tests: `backend/tests/test_config.py`.

**Steps**

1. Add Pydantic field; ensure JSON-serializable defaults.
2. Use the field inside the appropriate stage (`transcriber`, `summarizer`, `audio`, etc.).

**Pitfalls**

- Pydantic may coerce types; invalid UI values can raise on `Config(**dict)`.
- Missing override in `run_batch` if you only added to `run` — keep parity.

**Validation**

- `pytest backend/tests/test_config.py -q`
- Full Electron run with network disabled (local file) to confirm no regressions.
