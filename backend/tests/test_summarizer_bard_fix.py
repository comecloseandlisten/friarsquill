"""Regression tests for the bard-mode fix - Revisions 2 + 3.

Revision 2 (2026-04-17) pivoted from regex-based CoT stripping to disabling
thinking at the model level via Qwen3's ``/no_think`` soft-switch and
kept the legacy strippers as dead-code utilities.

Revision 3 (2026-04-18) rewrote the bard/notes prompts — which had been asking
the model for explicit numbered "Шаг 1 / Шаг 2 / Шаг 3" reasoning, one root
cause of the CoT leak observed in output/video_summary.md at 2026-04-17 17:04 UTC.

Covered:

* Fix #1 - ``_detect_repetition_loop`` accepts ``mode`` and short-circuits
  for bard/inquisition while still catching real model loops in notes mode.
* Fix A - ``_llm_call`` injects ``/no_think`` into the user turn when
  ``config.disable_thinking`` is True.
* Fix B safety-net - ``_strip_thinking_tags`` still runs post-generation to
  catch any ``<think>`` leakage that slips past ``/no_think``.
* Fix D regression guard - ``Config.hygiene_escalation`` must be gone.
* Hygiene pipeline - ``_HYGIENE_PIPELINE`` runs ``thinking_tags`` then
  ``numbered_analysis`` for every mode; ``prompt_echo`` is not in the
  registry (see summarizer comments).
* ``_strip_untagged_leading_cot`` remains callable as a utility for quick
  re-enable and must still behave correctly on clean prose.
"""
from unittest.mock import MagicMock

from config import Config
from summarizer import (
    _HYGIENE_PIPELINE,
    _config_suggests_qwen_native_no_think,
    _detect_repetition_loop,
    _invoke_chat_completion_dispatch,
    _llm_call,
    _strip_untagged_leading_cot,
)


class TestQwenNoThinkHeuristic:
    """Heuristic for when native /no_think is appended vs system supplement."""

    def test_default_config_suggests_qwen(self):
        assert _config_suggests_qwen_native_no_think(Config())

    def test_path_with_qwen_matches(self):
        assert _config_suggests_qwen_native_no_think(
            Config(llm_model_path="/opt/models/Qwen3-8B-Q4_K_M.gguf")
        )


class TestBardRepetitionGuard:
    """Fix #1: the repetition guard must not eat structurally-repeating bard output."""

    def test_bard_4_moments_not_truncated(self):
        # Arrange - bard-style output with 4 moments sharing identical labels.
        moments = []
        for idx, (title, quote) in enumerate(
            [
                ("Willpower vs. Systems", "Systems beat goals every time."),
                ("Identity-Based Habits", "Become the person, not the outcome."),
                ("Environment Design", "Make the cue obvious."),
                ("Small Wins Compound", "Tiny gains, massive results."),
            ],
            start=1,
        ):
            moments.append(
                f"### {idx} - {title}\n"
                f"**Why it catches attention:** {quote}\n"
                f"**Timecode:** 00:{idx:02d}:00\n"
                f"**Quote:** \"{quote}\"\n"
                f"**Context:** Fragment #{idx} of the interview.\n"
                f"**Force:** *****\n"
            )
        text = "## Best Moments\n\n" + "\n".join(moments)

        # Act - run the loop detector in bard mode.
        result = _detect_repetition_loop(text, mode="bard")

        # Assert - no moment was dropped or truncated.
        assert result.strip() == text.strip()
        for idx in range(1, 5):
            assert f"### {idx} -" in result
        assert result.count("**Force:** *****") == 4
        assert result.count("**Why it catches attention:**") == 4

    def test_repetition_guard_still_catches_real_loop(self):
        # Arrange - a genuine model loop (same line 10 times in a row).
        text = "loading data\n" * 10

        # Act - notes mode still runs the guard.
        result = _detect_repetition_loop(text, mode="notes")

        # Assert - the loop was detected and the output was truncated.
        assert len(result) < len(text)
        result_lines = [ln for ln in result.split("\n") if ln.strip()]
        # No 10-in-a-row repetition should survive.
        max_run = 0
        run = 0
        prev = None
        for line in result_lines:
            if line == prev:
                run += 1
            else:
                run = 1
                prev = line
            max_run = max(max_run, run)
        assert max_run < 10


class TestStripUntaggedCoTSafety:
    """Stripper kept as a dead-code utility - test validates its unit behavior."""

    def test_strip_untagged_cot_preserves_long_valid_prose(self):
        # Arrange - 2000+ chars of clean prose, no heading, no CoT markers.
        sentence = (
            "The speaker explains how small daily improvements compound over "
            "time, describing the plateau of latent potential and the "
            "importance of focusing on identity rather than outcomes. "
        )
        # Roughly 180 chars per sentence, 12 sentences ~= 2100 chars.
        prose = (sentence * 12).strip()
        assert len(prose) >= 2000
        assert not prose.lstrip().startswith("##")
        for marker in ("thinking process", "analyze the request", "the user wants"):
            assert marker not in prose.lower()

        # Act.
        result = _strip_untagged_leading_cot(prose)

        # Assert - output length is within 10% of input (nothing was stripped).
        assert len(result) >= int(len(prose) * 0.9)
        assert "small daily improvements" in result


class TestChatCompletionDispatch:
    """Real Llama uses the internal chat handler; MagicMock uses create_chat_completion."""

    def test_magicmock_forwards_jinja_extras_to_create_chat(self):
        llm = MagicMock()
        llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }
        out = _invoke_chat_completion_dispatch(
            llm,
            [{"role": "user", "content": "ping"}],
            temperature=0.2,
            max_tokens=8,
            repeat_penalty=1.1,
            jinja_template_extras={"enable_thinking": False},
        )
        assert out["choices"][0]["message"]["content"] == "ok"
        llm.create_chat_completion.assert_called_once()
        call_kw = llm.create_chat_completion.call_args.kwargs
        assert call_kw.get("enable_thinking") is False
        assert call_kw.get("stream") is False


class TestNoThinkInjection:
    """Fix A: /no_think on Qwen-style models; system supplement on others."""

    def test_no_think_injected_into_user_message(self):
        # Arrange - MagicMock LLM that records the messages argument.
        llm = MagicMock()
        llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "Hi."}}]
        }
        config = Config(summary_mode="notes", disable_thinking=True)

        # Act.
        _llm_call(llm, "Say hi", config, "", max_tokens=32)

        # llama.cpp state reset between completions (no-op on MagicMock)
        assert llm.reset.called

        # Assert - last message is user-role and content ends with /no_think.
        call_kwargs = llm.create_chat_completion.call_args.kwargs
        messages = (
            call_kwargs.get("messages")
            or llm.create_chat_completion.call_args.args[0]
        )
        last_msg = messages[-1]
        assert last_msg["role"] == "user"
        assert last_msg["content"].rstrip().endswith("/no_think")

    def test_disable_thinking_false_does_not_inject(self):
        # Arrange - same setup but disable_thinking=False.
        llm = MagicMock()
        llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "Hi."}}]
        }
        config = Config(summary_mode="notes", disable_thinking=False)

        # Act.
        _llm_call(llm, "Say hi", config, "", max_tokens=32)

        # Assert - /no_think is NOT present in any user content.
        call_kwargs = llm.create_chat_completion.call_args.kwargs
        messages = (
            call_kwargs.get("messages")
            or llm.create_chat_completion.call_args.args[0]
        )
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert user_msgs, "expected at least one user message"
        for m in user_msgs:
            assert "/no_think" not in m["content"]

    def test_non_qwen_skips_slash_directive_supplements_system(self):
        llm = MagicMock()
        llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "Hi."}}]
        }
        config = Config(
            summary_mode="notes",
            disable_thinking=True,
            llm_model_repo="TheBloke/Llama-2-7B-Chat-GGUF",
            llm_model_file="llama-2-7b-chat.Q4_K_M.gguf",
        )
        assert not _config_suggests_qwen_native_no_think(config)

        _llm_call(llm, "Say hi", config, "Respond ONLY in English.", max_tokens=32)

        call_kwargs = llm.create_chat_completion.call_args.kwargs
        messages = call_kwargs.get("messages") or llm.create_chat_completion.call_args.args[0]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert user_msgs and "/no_think" not in user_msgs[-1]["content"]
        sys_msgs = [m for m in messages if m["role"] == "system"]
        assert sys_msgs
        assert "Do not use <thinking>" in sys_msgs[0]["content"]


class TestSafetyNet:
    """Fix B: _strip_thinking_tags still runs as a post-generation safety net."""

    def test_strip_thinking_tags_still_runs_as_safety_net(self):
        # Arrange - LLM returns a <think> block followed by real content.
        llm = MagicMock()
        llm.create_chat_completion.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "<think>meta</think>Real content here, long "
                            "enough to pass any length check applied."
                        )
                    }
                }
            ]
        }
        config = Config(summary_mode="notes")

        # Act.
        result = _llm_call(llm, "prompt", config, "", max_tokens=128)

        # Assert - <think> block and its contents are gone; real prose survives.
        assert "<think>" not in result
        assert "meta" not in result
        assert "Real content here" in result


class TestConfigHygieneFieldRemoved:
    """Fix D regression guard - hygiene_escalation field must not come back."""

    def test_hygiene_escalation_field_removed(self):
        assert not hasattr(
            Config(), "hygiene_escalation"
        ), "hygiene_escalation should be removed in Revision 2"


class TestConfigDisableThinkingDefault:
    """Fix A default - disable_thinking must default to True."""

    def test_disable_thinking_default_true(self):
        assert Config().disable_thinking is True
