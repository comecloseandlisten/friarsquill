# 2h Scenario Validation (Design/Baseline)

## Scope
- Validate behavior for long input (`duration_sec = 7200`).
- Confirm that long-video chunking and summary concurrency are reflected in ETA and runtime flow.

## Baseline Before (conceptual)
- ASR: single long pass with no chunk checkpoints.
- Summary map: strictly sequential.
- No persisted per-run stage metrics.

## After Changes
- ASR: long-video strategy enabled by default via:
  - `long_video_threshold_sec = 3600`
  - `long_video_chunk_sec = 1200`
  - `long_video_overlap_sec = 2.0`
- Summary map: configurable `summary_max_concurrency` (default `1`, safe-by-default).
- Checkpoint/resume:
  - ASR chunks: `asr_<key>.json`
  - Summary chunks: `summary_<key>.json`
- Baseline metrics are persisted per run in `output/metrics/*_metrics.json`.

## ETA sanity check (2h synthetic)
Executed from `backend/`:

```bash
python -c "from config import Config; from eta import estimate; c=Config(); e=estimate(c,7200,'cuda'); print({'transcribe_sec':round(e['transcribe_sec'],1),'summarize_sec':round(e['summarize_sec'],1),'total_sec':round(e['total_sec'],1)})"
```

Observed:
- `transcribe_sec: 647.0`
- `summarize_sec: 159.6`
- `total_sec: 806.6`

## Acceptance checklist for real 2h media run
- Processing starts chunk-by-chunk and does not hold full transcript pipeline state in memory.
- If interrupted, rerun resumes from ASR/summary checkpoints.
- Output keeps global timestamps in ascending order.
- `output/metrics/*_metrics.json` contains stage times and resource snapshots.
