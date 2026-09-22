# VTS — Video Transcription & Summarization

Offline desktop tool for transcribing and summarizing videos. Supports YouTube URLs, yt-dlp-compatible links, and local video/audio files. Produces structured Markdown summaries with timestamps.

Built with Electron + Python. No cloud APIs — everything runs locally.

**LLM / AI assistants:** see [`llms.txt`](llms.txt) for repo index, [`docs/LLM_GUIDE.md`](docs/LLM_GUIDE.md) for architecture map, [`docs/LLM_TASK_MATRIX.md`](docs/LLM_TASK_MATRIX.md) for intent-to-code routing, [`docs/LLM_BACKEND_REFERENCE.md`](docs/LLM_BACKEND_REFERENCE.md) and [`docs/CHANGE_PLAYBOOK.md`](docs/CHANGE_PLAYBOOK.md) for backend navigation and change workflows, plus [`docs/IPC_PROTOCOL.md`](docs/IPC_PROTOCOL.md) and [`docs/FUNCTION_INDEX.md`](docs/FUNCTION_INDEX.md).

## Prerequisites

- **Python 3.10+** — [python.org](https://www.python.org/downloads/)
- **Node.js 18+** — [nodejs.org](https://nodejs.org/)
- **ffmpeg** — must be on PATH
  - Windows: `winget install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

## Setup

```bash
# Install Electron
npm install

# Install Python dependencies (use a venv if preferred)
pip install -r backend/requirements.txt
```

On first run, Whisper and LLM models will be auto-downloaded (~1-2 GB total).

### Windows: NVIDIA GPU (llama-cpp-python CUDA)

`backend/requirements.txt` installs **llama-cpp-python** from **PyPI**. On Windows that wheel is **CPU-only** (`py3-none-win_amd64`), so summarization never touches the GPU until you replace it with a **CUDA prebuild**.

Upstream **abetlen** indexes (`https://abetlen.github.io/llama-cpp-python/whl/cu12x`) mainly ship **Linux** CUDA wheels for recent versions; **Windows CUDA prebuilds for current releases are effectively maintained by the community**, not PyPI.

**Practical fix (recommended):** use prebuilt **Windows + CUDA** wheels from **[dougeeai/llama-cpp-python-wheels](https://github.com/dougeeai/llama-cpp-python-wheels/releases)** (e.g. **0.3.20** tags such as `v0.3.20-cuda13.0-sm89` for Ada / RTX 40xx, `…-sm86` for Ampere / RTX 30xx, `…-sm75` for Turing / RTX 20xx, `…-cuda12.1-sm86` if you rely on CUDA **12.x** `cublas64_12.dll`, etc.). Each release page lists the exact `.whl` filename, driver, and toolkit expectations.

Example (Ada / RTX 40xx, **CUDA 13.0** toolkit — adjust URL to the asset you download from Releases):

```powershell
python -m pip install --force-reinstall --no-deps `
  "https://github.com/dougeeai/llama-cpp-python-wheels/releases/download/v0.3.20-cuda13.0-sm89/llama_cpp_python-0.3.20+cuda13.0.sm89.ada-py3-none-win_amd64.whl"
```

Then verify:

```powershell
python -c "import llama_cpp.llama_cpp as L; print('gpu offload:', L.llama_supports_gpu_offload())"
```

Full matrix, older CUDA lines, and troubleshooting (missing `cublas` DLLs): [`backend/requirements-llama-cuda.txt`](backend/requirements-llama-cuda.txt).

**From source (local build):** if you have the CUDA toolkit, MSVC (VS 2022 C++ workload), and CMake, you can compile `llama-cpp-python` with `GGML_CUDA` in the project venv instead of downloading a wheel. Step-by-step commands, verification, and pitfalls: [`docs/WINDOWS_LLAMA_CPP_CUDA_SOURCE_BUILD.md`](docs/WINDOWS_LLAMA_CPP_CUDA_SOURCE_BUILD.md).

**Debugging pip’s choice** (if you experiment with `--extra-index-url` instead): `pip install --dry-run --force-reinstall --no-deps …` — without `--force-reinstall`, pip may print “already satisfied” and never show which wheel would win.

## Usage

```bash
npm start
```

1. Paste a YouTube URL or choose a local video/audio file
2. Select whisper model and language (or leave auto-detect)
3. Click **Start Processing**
4. Wait for transcription and summarization
5. Copy the result or open the generated `.md` file

## Architecture

```
Electron (UI) <--stdio JSON-RPC--> Python (backend)
```

Pipeline stages: **download** → **extract audio** → **transcribe** → **summarize** → **format**

- **faster-whisper** — speech-to-text (CTranslate2, Silero VAD)
- **llama-cpp-python** — local LLM summarization (Qwen2.5-1.5B GGUF)
- **yt-dlp** — video/audio download from 1800+ sites

Memory is managed carefully: transcription and summarization models are never loaded simultaneously.

## Configuration

| Setting | Default | Options |
|---------|---------|---------|
| Whisper model | `small` | tiny, base, small, medium, large-v3, large-v3-turbo |
| Compute type | `auto` | auto, int8, float16, float32 |
| Language | auto-detect | en, ru, ja, zh, de, fr, es, ko |
| LLM | Qwen2.5-1.5B Q4_K_M | Any GGUF via settings |

## License

MIT
