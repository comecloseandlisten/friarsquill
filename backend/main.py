import os
import sys
import json
import threading

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _register_nvidia_dll_dirs():
    """
    On Windows, pip packages like nvidia-cublas-cu12, nvidia-cudnn-cu12 install
    DLLs into site-packages/nvidia/*/bin/, but Windows does not search these
    paths by default. We must explicitly register them via os.add_dll_directory()
    BEFORE importing ctranslate2 / faster_whisper, otherwise CUDA operations fail
    with "cublas64_12.dll not found".
    """
    if sys.platform != "win32":
        return
    if not hasattr(os, "add_dll_directory"):
        return

    try:
        import sysconfig
        site_packages = sysconfig.get_paths().get("purelib")
    except Exception:
        site_packages = None

    if not site_packages:
        return

    nvidia_root = os.path.join(site_packages, "nvidia")
    if not os.path.isdir(nvidia_root):
        return

    registered = []
    path_additions = []
    bin_dirs = []
    for sub in os.listdir(nvidia_root):
        bin_dir = os.path.join(nvidia_root, sub, "bin")
        if os.path.isdir(bin_dir):
            try:
                # 1) Register for Python's LoadLibrary calls
                os.add_dll_directory(bin_dir)
                # 2) Prepend to PATH for C++ runtime / transitive DLL resolution
                path_additions.append(bin_dir)
                bin_dirs.append(bin_dir)
                registered.append(sub)
            except Exception as e:
                print(f"[main] Failed to register DLL dir {bin_dir}: {e}", file=sys.stderr, flush=True)

    if path_additions:
        os.environ["PATH"] = os.pathsep.join(path_additions) + os.pathsep + os.environ.get("PATH", "")

    if registered:
        print(f"[main] Registered NVIDIA DLL dirs: {', '.join(registered)}", file=sys.stderr, flush=True)

    # Preload critical CUDA DLLs via ctypes. Once loaded, any subsequent
    # LoadLibrary() call from ctranslate2's native code returns the existing
    # handle regardless of search path. This is the most bulletproof fix.
    import ctypes
    critical_dlls = [
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudnn_ops64_9.dll",
        "cudnn_cnn64_9.dll",
        "cudnn_adv64_9.dll",
        "cudnn_graph64_9.dll",
        "cudnn_heuristic64_9.dll",
        "cudnn_engines_precompiled64_9.dll",
        "cudnn_engines_runtime_compiled64_9.dll",
        "cudnn64_9.dll",
        "nvrtc64_120_0.dll",
    ]
    preloaded = []
    for dll_name in critical_dlls:
        for bin_dir in bin_dirs:
            dll_path = os.path.join(bin_dir, dll_name)
            if os.path.isfile(dll_path):
                try:
                    ctypes.CDLL(dll_path)
                    preloaded.append(dll_name)
                    break
                except OSError as e:
                    print(f"[main] Preload failed for {dll_name}: {e}", file=sys.stderr, flush=True)
    if preloaded:
        print(f"[main] Preloaded CUDA DLLs: {', '.join(preloaded)}", file=sys.stderr, flush=True)


_register_nvidia_dll_dirs()

# Pre-import heavy libraries in main thread to avoid ctranslate2
# deadlock when first imported inside a child thread on Windows
import faster_whisper  # noqa: F401
import ctranslate2  # noqa: F401
import llama_cpp  # noqa: F401

from pipeline import Pipeline


def send(msg: dict):
    """Send JSON message to Electron via stdout."""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _log(msg):
    print(f"[main] {msg}", file=sys.stderr, flush=True)


def main():
    _log("Backend started, waiting for commands on stdin")
    pipeline = Pipeline(progress_callback=send)

    while True:
        line = sys.stdin.readline()
        if not line:
            _log("EOF on stdin, shutting down")
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _log(f"Invalid JSON on stdin: {line[:200]}")
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        _log(f"Received method={method}, id={msg_id}")

        if method == "process":
            params = msg.get("params", {})
            _log(f"  source={params.get('source', '?')[:120]}")
            _log(f"  config keys: {list(params.get('config', {}).keys())}")

            def run(mid=msg_id, params=params):
                try:
                    result = pipeline.run(mid, params)
                    send({"id": mid, "type": "result", **result})
                    _log(f"Process complete for id={mid}")
                except Exception as e:
                    _log(f"Process FAILED for id={mid}: {e}")
                    send({"id": mid, "type": "error", "message": str(e)})

            threading.Thread(target=run, daemon=True).start()

        elif method == "process_batch":
            def run_batch(mid=msg_id, params=msg.get("params", {})):
                try:
                    result = pipeline.run_batch(mid, params)
                    send({"id": mid, "type": "result", **result})
                except Exception as e:
                    send({"id": mid, "type": "error", "message": str(e)})

            threading.Thread(target=run_batch, daemon=True).start()

        elif method == "cancel":
            pipeline.cancel()

        elif method == "oracle_chat":
            params = msg.get("params", {})

            def run_oracle(mid=msg_id, params=params):
                try:
                    result = pipeline.oracle_chat(mid, params)
                    send({"id": mid, "type": "result", "oracle": True, **result})
                    _log(f"Oracle chat done for id={mid}")
                except Exception as e:
                    _log(f"Oracle chat FAILED for id={mid}: {e}")
                    send({"id": mid, "type": "error", "oracle": True, "message": str(e)})

            threading.Thread(target=run_oracle, daemon=True).start()

        elif method == "oracle_cancel":
            try:
                pipeline.oracle_cancel()
            except Exception as e:
                _log(f"oracle_cancel error: {e}")

        elif method == "oracle_close":
            try:
                pipeline.oracle_close()
                send({"id": msg_id, "type": "result", "oracle": True, "closed": True})
            except Exception as e:
                send({"id": msg_id, "type": "error", "oracle": True, "message": str(e)})

        elif method == "list_models":
            try:
                from models_registry import list_models
                project_root = os.path.dirname(os.path.abspath(__file__))
                # walk up one level to the project root
                project_root = os.path.dirname(project_root)
                result = list_models(project_root)
                send({"id": msg_id, "type": "result", "models": result})
            except Exception as e:
                send({"id": msg_id, "type": "error", "message": str(e)})

        elif method == "get_gpu_info":
            try:
                from summarizer import _detect_gpu_info
                info = _detect_gpu_info()
                send({"id": msg_id, "type": "result", **info})
            except Exception:
                send({"id": msg_id, "type": "result", "cuda": False, "device": "", "vram_mb": 0})

        elif method == "estimate_eta":
            try:
                from config import Config
                from audio import get_duration
                from eta import estimate, format_eta

                params = msg.get("params", {})
                duration_sec = params.get("duration_sec")
                config_dict = params.get("config", {})

                if duration_sec is None:
                    raise ValueError("duration_sec parameter is required")

                config = Config(**config_dict)

                # Resolve device
                device_resolved = config.device
                if device_resolved == "auto":
                    try:
                        import torch
                        device_resolved = "cuda" if torch.cuda.is_available() else "cpu"
                    except ImportError:
                        device_resolved = "cpu"

                eta_estimate = estimate(config, duration_sec, device_resolved)
                eta_estimate["formatted_total"] = format_eta(eta_estimate["total_sec"])

                send({
                    "id": msg_id,
                    "type": "result",
                    "eta": eta_estimate,
                })
            except Exception as e:
                send({"id": msg_id, "type": "error", "message": str(e)})

        # Note: The "process" method accepts the following optional parameters:
        # - source: str — URL or file path to process
        # - summary_mode: str (notes, call_check, factcheck, tldr)
        # - summary_mode_config: dict (mode-specific configuration)
        # - multilingual: str (auto, true, false) — enables adaptive multilingual transcription
        # - multilingual_threshold: float (0.0-1.0) — language detection confidence threshold
        # - diarize: bool — enable LLM-based speaker diarization (post-transcription)
        # - speaker_names: dict — mapping of SPEAKER_XX to display names
        # - output_dir: str — output directory for results
        #
        # Note: The "process_batch" method accepts the following parameters:
        # - sources: list[str] — list of URLs or file paths to process
        # - config: dict — configuration (same as "process")
        # - summary_mode: str (optional) — override config
        # - summary_mode_config: dict (optional) — override config
        # - multilingual: str (optional) — override config
        # - multilingual_threshold: float (optional) — override config
        # - diarize: bool (optional) — override config
        # - speaker_names: dict (optional) — override config
        # - output_dir: str — output directory for results
        #
        # The "cancel" method cancels the current processing operation.


if __name__ == "__main__":
    main()
