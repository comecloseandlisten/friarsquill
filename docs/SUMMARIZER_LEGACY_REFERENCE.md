# Reference: `backend/summarizer легаси.py`

This file is **not imported** by the app (`pipeline.py` uses `summarizer.summarize` from `summarizer.py` only).

It is kept as a **behavioral reference** for an older pipeline: MAP chunk summaries, then a single `_tree_reduce` with `final.txt` (no per-topic modular assembly, no `summary_chunk_max_tokens` cap on MAP in that copy).

Do **not** enable the embedded debug log writes to `debug-ff5c79.log` in production; when comparing implementations, read the logic only.

See `docs/LLM_GUIDE.md` → «Summarization: modular topics» for how the current modular pipeline aligns with that quality goal.
