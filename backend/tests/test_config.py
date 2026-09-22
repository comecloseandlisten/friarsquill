"""Test Config model and validation."""
import pytest
from config import Config


class TestConfigDefaults:
    """Test Config default values."""

    def test_default_values(self):
        """Test that Config initializes with correct defaults."""
        config = Config()

        # Transcription defaults
        assert config.whisper_model == "small"
        assert config.compute_type == "auto"
        assert config.language is None
        assert config.device == "auto"
        assert config.beam_size == 5
        assert config.vad_filter is True
        assert config.multilingual == "auto"
        assert config.multilingual_threshold == 0.8

        # Summarization defaults
        assert config.llm_model_repo == "Qwen/Qwen2.5-7B-Instruct-GGUF"
        assert config.llm_model_file == "qwen2.5-7b-instruct-q4_k_m.gguf"
        assert config.llm_context_length == 0  # 0 = auto-detect based on VRAM
        assert config.llm_gpu_layers == -1
        assert config.llm_temperature == 0.3
        assert config.chunk_size_tokens == 3000
        assert config.chunk_overlap_tokens == 200
        assert config.summary_chunk_max_tokens == 0

        # Summary mode defaults
        assert config.summary_mode == "notes"
        assert config.summary_mode_config == {}

        # Diarization defaults
        assert config.diarize is False
        assert config.speaker_names == {}

        # Batch mode default
        assert config.batch_mode is False

        # Output default
        assert config.output_dir == "./output"
        assert config.include_full_transcript is True


class TestConfigSummaryModes:
    """Test summary mode configuration."""

    def test_summary_mode_notes(self):
        """Test notes mode configuration."""
        config = Config(summary_mode="notes")
        assert config.summary_mode == "notes"

    def test_summary_mode_call_check(self):
        """Test call_check mode configuration."""
        config = Config(
            summary_mode="call_check",
            summary_mode_config={"required_phrases": ["agreement", "payment"]},
        )
        assert config.summary_mode == "call_check"
        assert "required_phrases" in config.summary_mode_config
        assert config.summary_mode_config["required_phrases"] == ["agreement", "payment"]

    def test_summary_mode_factcheck(self):
        """Test factcheck mode configuration."""
        config = Config(summary_mode="factcheck")
        assert config.summary_mode == "factcheck"

    def test_summary_mode_tldr(self):
        """Test tldr mode configuration."""
        config = Config(summary_mode="tldr")
        assert config.summary_mode == "tldr"

    def test_summary_mode_config_empty(self):
        """Test that summary_mode_config can be empty dict."""
        config = Config(summary_mode_config={})
        assert config.summary_mode_config == {}

    def test_summary_mode_config_nested(self):
        """Test that summary_mode_config supports nested structures."""
        config = Config(
            summary_mode_config={
                "red_flags": ["fraud", "breach"],
                "thresholds": {"severity": 0.8},
            }
        )
        assert config.summary_mode_config["red_flags"] == ["fraud", "breach"]
        assert config.summary_mode_config["thresholds"]["severity"] == 0.8


class TestConfigMultilingual:
    """Test multilingual configuration."""

    def test_multilingual_auto(self):
        """Test multilingual auto mode."""
        config = Config(multilingual="auto")
        assert config.multilingual == "auto"

    def test_multilingual_true(self):
        """Test multilingual forced true."""
        config = Config(multilingual="true")
        assert config.multilingual == "true"

    def test_multilingual_false(self):
        """Test multilingual forced false."""
        config = Config(multilingual="false")
        assert config.multilingual == "false"

    def test_multilingual_threshold(self):
        """Test multilingual threshold configuration."""
        config = Config(multilingual_threshold=0.7)
        assert config.multilingual_threshold == 0.7

    def test_multilingual_threshold_boundary(self):
        """Test multilingual threshold at boundaries."""
        config_low = Config(multilingual_threshold=0.0)
        config_high = Config(multilingual_threshold=1.0)
        assert config_low.multilingual_threshold == 0.0
        assert config_high.multilingual_threshold == 1.0

    def test_language_overrides_multilingual(self):
        """Test that explicit language can be set with multilingual."""
        config = Config(language="fr", multilingual="true")
        assert config.language == "fr"
        assert config.multilingual == "true"


class TestConfigDiarization:
    """Test diarization configuration."""

    def test_diarize_disabled_by_default(self):
        """Test that diarization is disabled by default."""
        config = Config()
        assert config.diarize is False

    def test_diarize_enabled(self):
        """Test enabling diarization."""
        config = Config(diarize=True)
        assert config.diarize is True

    def test_speaker_names_mapping(self):
        """Test speaker name mapping."""
        names = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        config = Config(diarize=True, speaker_names=names)
        assert config.speaker_names == names
        assert config.speaker_names["SPEAKER_00"] == "Alice"

    def test_speaker_names_empty(self):
        """Test that speaker_names defaults to empty dict."""
        config = Config()
        assert config.speaker_names == {}

    def test_diarize_config_complete(self):
        """Test complete diarization configuration."""
        config = Config(
            diarize=True,
            speaker_names={"SPEAKER_00": "Manager", "SPEAKER_01": "Client"},
        )
        assert config.diarize is True
        assert len(config.speaker_names) == 2


class TestConfigBatchMode:
    """Test batch processing configuration."""

    def test_batch_mode_disabled_by_default(self):
        """Test that batch mode is disabled by default."""
        config = Config()
        assert config.batch_mode is False

    def test_batch_mode_enabled(self):
        """Test enabling batch mode."""
        config = Config(batch_mode=True)
        assert config.batch_mode is True


class TestConfigTranscription:
    """Test transcription-specific configuration."""

    def test_compute_type_auto(self):
        """Test auto compute type selection."""
        config = Config(compute_type="auto")
        assert config.compute_type == "auto"

    def test_compute_type_int8(self):
        """Test int8 compute type."""
        config = Config(compute_type="int8")
        assert config.compute_type == "int8"

    def test_compute_type_float16(self):
        """Test float16 compute type."""
        config = Config(compute_type="float16")
        assert config.compute_type == "float16"

    def test_compute_type_float32(self):
        """Test float32 compute type."""
        config = Config(compute_type="float32")
        assert config.compute_type == "float32"

    def test_device_auto(self):
        """Test auto device selection."""
        config = Config(device="auto")
        assert config.device == "auto"

    def test_device_cpu(self):
        """Test CPU device."""
        config = Config(device="cpu")
        assert config.device == "cpu"

    def test_device_cuda(self):
        """Test CUDA device."""
        config = Config(device="cuda")
        assert config.device == "cuda"

    def test_beam_size_custom(self):
        """Test custom beam size."""
        config = Config(beam_size=10)
        assert config.beam_size == 10

    def test_vad_filter_disabled(self):
        """Test disabling VAD filter."""
        config = Config(vad_filter=False)
        assert config.vad_filter is False

    def test_language_specific(self):
        """Test setting specific language."""
        config = Config(language="es")
        assert config.language == "es"


class TestConfigLLM:
    """Test LLM model configuration."""

    def test_llm_model_path_custom(self):
        """Test setting custom LLM model path."""
        config = Config(llm_model_path="/path/to/model.gguf")
        assert config.llm_model_path == "/path/to/model.gguf"

    def test_llm_model_repo_custom(self):
        """Test setting custom LLM model repo."""
        config = Config(llm_model_repo="meta-llama/Llama-2-7B")
        assert config.llm_model_repo == "meta-llama/Llama-2-7B"

    def test_llm_temperature_low(self):
        """Test low temperature (deterministic)."""
        config = Config(llm_temperature=0.0)
        assert config.llm_temperature == 0.0

    def test_llm_temperature_high(self):
        """Test high temperature (creative)."""
        config = Config(llm_temperature=1.0)
        assert config.llm_temperature == 1.0

    def test_chunk_size_custom(self):
        """Test custom chunk size."""
        config = Config(chunk_size_tokens=2000)
        assert config.chunk_size_tokens == 2000

    def test_chunk_overlap_custom(self):
        """Test custom chunk overlap."""
        config = Config(chunk_overlap_tokens=300)
        assert config.chunk_overlap_tokens == 300


class TestConfigOutput:
    """Test output configuration."""

    def test_output_dir_default(self):
        """Test default output directory."""
        config = Config()
        assert config.output_dir == "./output"

    def test_output_dir_custom(self):
        """Test custom output directory."""
        config = Config(output_dir="/results")
        assert config.output_dir == "/results"

    def test_include_full_transcript_true(self):
        """Test including full transcript."""
        config = Config(include_full_transcript=True)
        assert config.include_full_transcript is True

    def test_include_full_transcript_false(self):
        """Test excluding full transcript."""
        config = Config(include_full_transcript=False)
        assert config.include_full_transcript is False


class TestConfigIntegration:
    """Test configuration integration scenarios."""

    def test_full_config_single_language(self):
        """Test full configuration for single language."""
        config = Config(
            whisper_model="large",
            language="en",
            summary_mode="notes",
            diarize=False,
            output_dir="/output",
        )
        assert config.whisper_model == "large"
        assert config.language == "en"
        assert config.multilingual == "auto"  # Not overridden
        assert config.summary_mode == "notes"
        assert config.diarize is False

    def test_full_config_multilingual(self):
        """Test full configuration for multilingual content."""
        config = Config(
            multilingual="true",
            multilingual_threshold=0.75,
            diarize=True,
            summary_mode="call_check",
        )
        assert config.multilingual == "true"
        assert config.multilingual_threshold == 0.75
        assert config.diarize is True
        assert config.summary_mode == "call_check"

    def test_config_batch_complete(self):
        """Test complete batch configuration."""
        config = Config(
            batch_mode=True,
            summary_mode="tldr",
            diarize=True,
            speaker_names={"SPEAKER_00": "Speaker A", "SPEAKER_01": "Speaker B"},
            output_dir="/batch_output",
        )
        assert config.batch_mode is True
        assert config.summary_mode == "tldr"
        assert len(config.speaker_names) == 2
        assert config.output_dir == "/batch_output"
