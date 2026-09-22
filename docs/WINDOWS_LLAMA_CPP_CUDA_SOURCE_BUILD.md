# Windows: building llama-cpp-python with CUDA (from source)

This project depends on **llama-cpp-python** for GGUF summarization. On Windows, `pip install -r backend/requirements.txt` usually installs a **CPU-only** wheel from PyPI (`py3-none-win_amd64`). To use the **GPU**, either install a **prebuilt CUDA wheel** (see [`backend/requirements-llama-cuda.txt`](../backend/requirements-llama-cuda.txt)) or **build the package locally** with `GGML_CUDA` enabled, as described below.

## Prerequisites

- **NVIDIA GPU** with a recent driver.
- **CUDA Toolkit** installed so CMake can find CUDA (e.g. `nvcc` on PATH). The toolkit version should match what you expect at runtime (DLL names follow the major CUDA line you link against).
- **Visual Studio 2022** with the **Desktop development with C++** workload (MSVC + Windows SDK), or another supported C++ toolchain as required by upstream llama.cpp.
- **CMake** on PATH.
- **Python venv** for this repo (`.venv` at the project root is what Electron uses by default — see [`electron/main.js`](../electron/main.js)).

Sanity checks:

```powershell
where cmake
where nvcc
$env:CUDA_PATH
```

## Build steps (PowerShell)

Run everything in **one shell session** so environment variables apply to the `pip` subprocess.

```powershell
Set-Location <path-to-repo-clone>
.\.venv\Scripts\Activate.ps1

python -m pip uninstall -y llama-cpp-python

$env:FORCE_CMAKE = "1"
$env:CMAKE_ARGS = "-DGGML_CUDA=on"

# Optional: restrict compile to your GPU architecture (faster build, smaller binaries).
# Examples: Turing sm_75 → 75; Ada sm_89 → 89; Ampere sm_86 → 86 (match your card).
# $env:CMAKE_ARGS = "-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=75"

python -m pip install --no-cache-dir llama-cpp-python
```

Use `python -m pip install --no-cache-dir --verbose llama-cpp-python` if you need full CMake/compiler logs.

### If the build fails on optional targets

Some environments hit errors on optional vision / auxiliary targets. You can extend `CMAKE_ARGS` (keep a single string or join flags with spaces):

```powershell
$env:CMAKE_ARGS = "-DGGML_CUDA=on -DLLAVA_BUILD=off"
```

Exact flag names can vary by llama-cpp-python release; check the error output and upstream docs if this flag is unrecognized.

### Time and artifacts

The first build from the **sdist** (`*.tar.gz`) can take **tens of minutes** (CUDA kernels compile to many object files). That is normal.

## Verification

1. **Python API**

   ```powershell
   python -c "import llama_cpp.llama_cpp as L; print('gpu offload:', L.llama_supports_gpu_offload())"
   ```

   Expect **`gpu offload: True`**. On success, llama.cpp may print `ggml_cuda_init: found …` to **stderr**.

2. **PowerShell and stderr**

   PowerShell can surface stderr from native code as a **non-terminating** `NativeCommandError` even when Python exits with code 0. Ignore it if the line above still prints `True`.

3. **On-disk CUDA backend**

   Under the venv, `llama_cpp\lib` should include **`ggml-cuda.dll`** (CPU-only installs typically lack it and only ship `ggml-cpu.dll`):

   ```powershell
   Get-ChildItem "$env:VIRTUAL_ENV\Lib\site-packages\llama_cpp\lib\*.dll" | Select-Object -ExpandProperty Name
   ```

## After upgrading dependencies

Running `pip install -r backend/requirements.txt` again may **reinstall the PyPI CPU wheel** for `llama-cpp-python`. If `llama_supports_gpu_offload()` becomes `False`, repeat the uninstall + `FORCE_CMAKE` / `CMAKE_ARGS` install, or reinstall a known-good **CUDA wheel** from [`backend/requirements-llama-cuda.txt`](../backend/requirements-llama-cuda.txt).

## How this fits the app

- Electron spawns **`{repo}/.venv/Scripts/python.exe`** when that file exists, so the venv you built is the one the app uses.
- [`backend/main.py`](../backend/main.py) calls `_register_nvidia_dll_dirs()` **before** importing `faster_whisper`, `ctranslate2`, and `llama_cpp`, so pip-installed CUDA DLLs under `site-packages/nvidia/*/bin` are visible on Windows. See [`WINDOWS_CUDA_NATIVE_LIBS_TRANSFER.txt`](WINDOWS_CUDA_NATIVE_LIBS_TRANSFER.txt) for the rationale (Whisper / CT2 paths). Your **llama** build still uses the CUDA toolkit you compiled against; pip `nvidia-*` wheels are mainly for other native deps.

## Related docs

- [`backend/requirements-llama-cuda.txt`](../backend/requirements-llama-cuda.txt) — prebuilt community wheels, pip dry-run notes.
- [`WINDOWS_CUDA_NATIVE_LIBS_TRANSFER.txt`](WINDOWS_CUDA_NATIVE_LIBS_TRANSFER.txt) — `add_dll_directory` / PATH for `nvidia/*` on Windows.
- [README.md](../README.md) — high-level Windows GPU section.
