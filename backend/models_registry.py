import os
from pathlib import Path
from typing import Optional


# Whisper model registry with metadata
WHISPER_MODELS = [
    {
        "id": "tiny",
        "label": "Tiny (39M params)",
        "size_mb": 140,
        "recommended_vram_gb": 1,
    },
    {
        "id": "base",
        "label": "Base (74M params)",
        "size_mb": 270,
        "recommended_vram_gb": 1,
    },
    {
        "id": "small",
        "label": "Small (244M params)",
        "size_mb": 950,
        "recommended_vram_gb": 2,
    },
    {
        "id": "medium",
        "label": "Medium (769M params)",
        "size_mb": 2900,
        "recommended_vram_gb": 4,
    },
    {
        "id": "large-v3",
        "label": "Large v3 (1.55B params)",
        "size_mb": 5900,
        "recommended_vram_gb": 8,
    },
    {
        "id": "large-v3-turbo",
        "label": "Large v3 Turbo (1.55B params, optimized)",
        "size_mb": 2600,
        "recommended_vram_gb": 6,
    },
]

# LLM presets registry (HuggingFace GGUF models)
LLM_PRESETS = [
    {
        "id": "qwen2.5-7b",
        "label": "Qwen2.5 7B Instruct (Q4_K_M)",
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "size_mb": 4700,
    },
    {
        "id": "llama3.1-8b",
        "label": "Llama 3.1 8B Instruct (Q4_K_M)",
        "repo": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "size_mb": 4900,
    },
    {
        "id": "mistral-7b",
        "label": "Mistral 7B Instruct v0.3 (Q4_K_M)",
        "repo": "bartowski/Mistral-7B-Instruct-v0.3-GGUF",
        "file": "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
        "size_mb": 4400,
    },
    {
        "id": "gemma2-9b",
        "label": "Gemma 2 9B Instruct (Q4_K_M)",
        "repo": "bartowski/gemma-2-9b-it-GGUF",
        "file": "gemma-2-9b-it-Q4_K_M.gguf",
        "size_mb": 5500,
    },
]


def scan_local_ggufs(project_root: str) -> list[dict]:
    """
    Recursively scan for *.gguf files in the project root and common subdirectories.

    Args:
        project_root: Absolute path to the project root directory

    Returns:
        List of dicts with id, label, path, and size_mb for each GGUF file found
    """
    project_path = Path(project_root)
    local_models = []

    # Search in project root and common model subdirectories
    search_dirs = [project_path]
    common_subdirs = ["models", "model", "weights", "gguf"]
    for subdir in common_subdirs:
        sub_path = project_path / subdir
        if sub_path.exists() and sub_path.is_dir():
            search_dirs.append(sub_path)

    # Collect all GGUF files
    seen_files = set()
    for search_dir in search_dirs:
        try:
            for gguf_file in search_dir.glob("*.gguf"):
                if gguf_file.is_file():
                    abs_path = str(gguf_file.resolve())
                    # Avoid duplicates (resolved paths should be unique)
                    if abs_path not in seen_files:
                        seen_files.add(abs_path)
                        size_mb = gguf_file.stat().st_size // (1024 * 1024)
                        local_models.append({
                            "id": f"local:{gguf_file.name}",
                            "label": gguf_file.name,
                            "path": abs_path,
                            "size_mb": size_mb,
                        })
        except (OSError, IOError):
            # Skip directories we can't read
            pass

    # Sort by filename for consistent ordering
    local_models.sort(key=lambda m: m["label"])
    return local_models


def list_models(project_root: str) -> dict:
    """
    List all available models (Whisper and LLM).

    Args:
        project_root: Absolute path to the project root directory

    Returns:
        Dict with structure:
        {
            "whisper": [list of Whisper models],
            "llm": {
                "local": [list of local GGUF files],
                "presets": [list of HuggingFace presets]
            }
        }
    """
    return {
        "whisper": WHISPER_MODELS,
        "llm": {
            "local": scan_local_ggufs(project_root),
            "presets": LLM_PRESETS,
        },
    }


def resolve_llm_preset(preset_id: str, project_root: str) -> dict:
    """
    Resolve a preset ID to a configuration dict.

    Args:
        preset_id: Preset ID like "qwen2.5-7b" or "local:filename.gguf"
        project_root: Absolute path to the project root directory

    Returns:
        Dict with keys: path, repo, file (some may be None depending on the preset type)
        For "local:" presets, path is set; for HuggingFace presets, repo and file are set.
    """
    # Handle local GGUF presets
    if preset_id.startswith("local:"):
        filename = preset_id[6:]  # Remove "local:" prefix
        local_models = scan_local_ggufs(project_root)
        for model in local_models:
            if model["label"] == filename:
                return {
                    "path": model["path"],
                    "repo": None,
                    "file": None,
                }
        # If not found, return None values
        return {
            "path": None,
            "repo": None,
            "file": None,
        }

    # Handle HuggingFace presets
    for preset in LLM_PRESETS:
        if preset["id"] == preset_id:
            return {
                "path": None,
                "repo": preset["repo"],
                "file": preset["file"],
            }

    # Unknown preset ID
    return {
        "path": None,
        "repo": None,
        "file": None,
    }
