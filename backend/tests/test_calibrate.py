"""Test calibrate module — golden value equivalence with legacy _auto_context_length."""
import pytest
from unittest.mock import patch

from calibrate import calibrate_llm_context, calibrate_generation_budget, detect_gpu_info, calibrate_recommendations


class TestCalibrateLlmContext:
    """Verify calibrate_llm_context matches legacy _auto_context_length exactly."""

    # ---- CPU / no-GPU edge cases ----

    def test_cpu_mode_returns_8192(self):
        assert calibrate_llm_context(4000, 8000, 0) == 8192

    def test_no_vram_returns_8192(self):
        assert calibrate_llm_context(4000, 0, -1) == 8192

    # ---- Golden values captured from legacy code ----

    @pytest.mark.parametrize(
        "model_size_mb, vram_mb, gpu_layers, expected",
        [
            # ~3B on 24GB
            (2000, 24000, -1, 131072),
            # ~7B on 8GB (RTX 2080 SUPER)
            (4500, 8000, -1, 14336),
            # Model barely fits → 4096
            (7500, 8000, -1, 4096),
            # ~13B on 16GB
            (8000, 16000, -1, 20480),
            # 30B+ on 48GB
            (18000, 48000, -1, 59392),
            # Very constrained → clamp to 4096
            (7000, 8000, -1, 4096),
        ],
    )
    def test_golden_values(self, model_size_mb, vram_mb, gpu_layers, expected):
        result = calibrate_llm_context(model_size_mb, vram_mb, gpu_layers)
        assert result == expected, (
            f"calibrate_llm_context({model_size_mb}, {vram_mb}, {gpu_layers}) "
            f"= {result}, expected {expected}"
        )

    # ---- Structural invariants ----

    def test_result_always_multiple_of_1024(self):
        for vram in [6000, 8000, 12000, 16000, 24000, 48000]:
            for model_size in [2000, 4000, 6000, 8000, 15000]:
                result = calibrate_llm_context(model_size, vram, -1)
                assert result % 1024 == 0

    def test_result_within_bounds(self):
        for vram in [4000, 8000, 16000, 24000, 48000]:
            for model_size in [1000, 4000, 8000, 20000]:
                result = calibrate_llm_context(model_size, vram, -1)
                assert 4096 <= result <= 131072

    # ---- Edge: negative available VRAM ----

    def test_negative_available_returns_4096(self):
        """Model bigger than VRAM + overhead."""
        assert calibrate_llm_context(10000, 8000, -1) == 4096

    # ---- Cross-check with summarizer wrapper ----

    def test_matches_summarizer_wrapper(self):
        """Ensure summarizer._auto_context_length delegates correctly."""
        from summarizer import _auto_context_length
        cases = [
            (2000, 24000, -1),
            (4500, 8000, -1),
            (7500, 8000, -1),
            (8000, 16000, -1),
            (18000, 48000, -1),
            (4000, 8000, 0),
            (4000, 0, -1),
        ]
        for args in cases:
            assert _auto_context_length(*args) == calibrate_llm_context(*args), (
                f"Mismatch for args {args}"
            )


class TestDetectGpuInfo:
    """Test GPU detection fallback paths."""

    def test_returns_dict_with_keys(self):
        """Even without GPU, must return all expected keys."""
        with patch("calibrate.subprocess.check_output", side_effect=Exception("no nvidia-smi")):
            info = detect_gpu_info()
            assert "cuda" in info
            assert "device" in info
            assert "vram_mb" in info

    def test_no_gpu_returns_zeros(self):
        with patch("calibrate.subprocess.check_output", side_effect=Exception("no gpu")):
            # Also patch pynvml import to fail
            import sys
            saved = sys.modules.get("pynvml")
            sys.modules["pynvml"] = None
            try:
                info = detect_gpu_info()
                assert info["cuda"] is False
                assert info["vram_mb"] == 0
            finally:
                if saved is not None:
                    sys.modules["pynvml"] = saved
                else:
                    sys.modules.pop("pynvml", None)


class TestCalibrateGenerationBudget:
    """Test generation budget calculation from calibrated n_ctx.

    Budget = min(n_ctx - prompt - 128, n_ctx // 2), floor 1024.
    """

    # ---- CPU tier (n_ctx=8192): user expects ~4096 ----

    def test_cpu_tier_small_prompt_capped_at_half(self):
        # n_ctx=8192, prompt=2000 → available=6064, cap=4096 → 4096
        assert calibrate_generation_budget(8192, 2000) == 4096

    def test_cpu_tier_medium_prompt_capped_at_half(self):
        # n_ctx=8192, prompt=3000 → available=5064, cap=4096 → 4096
        assert calibrate_generation_budget(8192, 3000) == 4096

    def test_cpu_tier_large_prompt_below_cap(self):
        # n_ctx=8192, prompt=5000 → available=3064, cap=4096 → 3064
        assert calibrate_generation_budget(8192, 5000) == 3064

    # ---- 8GB GPU tier (n_ctx=12800) ----

    def test_8gb_tier_capped_at_half(self):
        # n_ctx=12800, prompt=2000 → available=10672, cap=6400 → 6400
        assert calibrate_generation_budget(12800, 2000) == 6400

    # ---- 24GB GPU tier (n_ctx=94208) ----

    def test_24gb_tier_capped_at_half(self):
        # n_ctx=94208, prompt=5000 → available=89080, cap=47104 → 47104
        assert calibrate_generation_budget(94208, 5000) == 47104

    # ---- Edge cases ----

    def test_budget_minimum_floor_1024(self):
        assert calibrate_generation_budget(4096, 4000) == 1024

    def test_budget_prompt_exceeds_context_returns_floor(self):
        assert calibrate_generation_budget(4096, 10000) == 1024

    @pytest.mark.parametrize("n_ctx", [4096, 8192, 16384, 32768, 65536, 131072])
    def test_budget_always_at_least_1024(self, n_ctx):
        assert calibrate_generation_budget(n_ctx, n_ctx) >= 1024


class TestCalibrateRecommendations:
    """Test recommendations stub."""

    def test_returns_gpu_info(self):
        fake_info = {"cuda": True, "device": "Test GPU", "vram_mb": 8000}
        result = calibrate_recommendations(gpu_info=fake_info)
        assert result["gpu_info"] == fake_info
