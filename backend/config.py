from pydantic import BaseModel


class Config(BaseModel):
    # Transcription
    whisper_model: str = "small"
    compute_type: str = "auto"  # auto, int8, float16, float32
    language: str | None = None  # None or "auto" = language detection; concrete code (e.g. "en") = fast-path, skip detection
    device: str = "auto"  # auto, cpu, cuda
    beam_size: int = 5
    vad_filter: bool = True
    word_timestamps: bool = True
    multilingual: str = "auto"  # auto, true, false — auto = enable if >1 language detected
    multilingual_threshold: float = 0.8  # language detection confidence threshold
    fast_profile_for_long_video: bool = True
    fast_profile_min_duration_sec: int = 1800
    fast_profile_beam_size: int = 2
    fast_profile_word_timestamps: bool = False
    fast_profile_vad_filter: bool = True
    long_video_threshold_sec: int = 3600
    # Use per-chunk extract+transcribe below this duration (decoupled from ASR chunking threshold).
    streaming_extract_threshold_sec: int = 900
    long_video_chunk_sec: int = 1200
    long_video_overlap_sec: float = 2.0
    asr_cpu_threads: int = 4
    ffmpeg_extract_stall_min_sec: int = 600
    ffmpeg_extract_stall_max_sec: int = 21600
    ffmpeg_extract_stall_pad_sec: int = 600
    ffmpeg_extract_compat_total_multiplier: float = 4.0
    ffmpeg_extract_compat_total_pad_sec: int = 900
    ffmpeg_extract_compat_total_unknown_sec: int = 7200
    ffmpeg_chunk_extract_timeout_sec: int | None = None

    # Summarization
    llm_model_preset: str | None = None  # alias like "qwen2.5-7b" or "local:<filename>.gguf"; when set, overrides llm_model_path/repo/file resolution
    llm_model_path: str | None = None
    llm_model_repo: str = "Qwen/Qwen2.5-7B-Instruct-GGUF"
    llm_model_file: str = "qwen2.5-7b-instruct-q4_k_m.gguf"
    llm_context_length: int = 0  # 0 = auto (fit to VRAM/RAM), >0 = explicit token count
    llm_gpu_layers: int = -1  # -1 = all layers on GPU, 0 = CPU only
    llm_n_threads: int = 4
    llm_temperature: float = 0.3
    llm_repeat_penalty: float = 1.2  # penalize repetition (1.0 = off, 1.1-1.3 = recommended)
    chunk_size_tokens: int = 3000
    chunk_overlap_tokens: int = 200
    summary_max_concurrency: int = 1
    # Cap max_tokens for MAP chunks and intermediate tree-reduce (0 = no cap, use calibrate only)
    summary_chunk_max_tokens: int = 0

    # Summary modes and configuration
    summary_mode: str = "notes"  # notes, call_check, inquisition, factcheck, tldr, bard
    summary_mode_config: dict = {}  # mode-specific config (e.g., required_phrases for call_check)
    summary_language: str = "auto"  # "auto" = same as transcript language, otherwise ISO code: "ru", "en", "es", "de", "fr", "zh", "ja", "pt", "it", "tr", "ar"

    # Topic segmentation (hierarchical summarization for long videos)
    topic_segmentation: bool = True  # deeper summaries for long videos (vocabulary-shift heuristic, zero extra LLM calls)
    topic_auto_threshold: int = 50  # auto-enable if segments > this
    topic_min_segments: int = 10  # minimum segments per topic (smaller merged with neighbor)
    topic_boundary_confidence: float = 0.6  # minimum confidence to accept a boundary
    topic_window_tokens: int = 3000  # sliding window size for boundary detection
    topic_window_step_tokens: int = 2500  # sliding window step (~17% overlap, enough for boundaries)

    # Skip summarization (transcription only)
    no_summary: bool = False  # when True, skip LLM summarization entirely

    # Hygiene / repetition guards (see backend/summarizer.py)
    # repetition_guard: when False, _detect_repetition_loop is a no-op globally.
    #   bard / inquisition modes short-circuit the guard regardless (their format
    #   is structurally repetitive and confuses the detector).
    # disable_thinking: when True (default), _llm_call discourages chain-of-thought.
    #   For Qwen-family GGUF (heuristic: "qwen" in model path/repo/file/preset),
    #   it appends the native "/no_think" user-turn switch. For other models it
    #   appends a strict anti-CoT line to the system message instead ("/no_think"
    #   is ignored and may appear as garbage text in the transcript).
    repetition_guard: bool = True
    disable_thinking: bool = True

    # Speaker diarization (LLM-based semantic analysis of transcript text)
    diarize: bool = False  # enable speaker diarization
    speaker_names: dict = {}  # {"SPEAKER_00": "Manager", "SPEAKER_01": "Client"}

    # Batch processing
    batch_mode: bool = False  # enable batch processing of multiple sources
    batch_max_files: int = 100  # maximum files to process in batch mode
    batch_timeout_per_file: int = 3600  # timeout per file in seconds (1 hour)
    batch_reuse_asr_runtime: bool = True

    # Output
    output_dir: str = "./output"
    include_full_transcript: bool = True

    # Security: allowed summary modes (whitelist)
    _allowed_summary_modes: set = {"notes", "call_check", "inquisition", "factcheck", "tldr", "bard"}
