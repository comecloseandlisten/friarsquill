"""Regression: Oracle system prompt enforces strict off-topic refusal."""

import oracle


def test_prompt_includes_refusal_only_contract():
    p = oracle.ORACLE_SYSTEM_PROMPT
    assert "Refusal-only output contract" in p
    assert "Do not partially comply" in p
    assert "no \"example not from the video\"" in p
    assert "Scope refusal" in p
    assert "2–3 short sentences" in p


def test_prompt_forbids_wrapping_off_topic_in_quotes():
    p = oracle.ORACLE_SYSTEM_PROMPT
    assert "wrap" in p.lower() or "wrap" in p  # "Do not \"wrap\""
    assert "blockquotes" in p.lower()
