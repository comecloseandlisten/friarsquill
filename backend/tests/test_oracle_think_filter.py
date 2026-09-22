"""Oracle stream filter — safety net for thinking tags split across chunks."""

from oracle import _ThinkStreamFilter, _THINK_TAG_NAMES, _strip_think_tags


def test_think_tag_names_include_redacted_thinking():
    assert "redacted_thinking" in _THINK_TAG_NAMES


def test_strip_think_tags_redacted_block():
    raw = (
        "<think>internal</think>\n\n"
        "Ответ пользователю."
    )
    assert "redacted_thinking" not in _strip_think_tags(raw).lower()
    assert "Ответ пользователю" in _strip_think_tags(raw)


def test_stream_filter_split_redacted_tag():
    f = _ThinkStreamFilter()
    out = []
    for piece in ("<redacted", "_thinking>sec", "ret</think>Hi"):
        out.append(f.feed(piece))
    out.append(f.flush())
    assert "".join(out) == "Hi"


def test_stream_filter_nested_name_order():
    # opener must match redacted_thinking, not a shorter prefix
    f = _ThinkStreamFilter()
    s = "<think>x</think>OK"
    assert f.feed(s) + f.flush() == "OK"


def test_flush_unclosed_thinking_emits_tail_not_empty():
    """EOF inside a thinking region must not discard the visible answer tail."""
    prefix = "a" * 35
    f = _ThinkStreamFilter()
    chunk = prefix + "<thinking>orphan tail without close"
    head = f.feed(chunk)
    tail = f.flush()
    assert "orphan tail without close" in (head + tail)
    assert head + tail == prefix + "orphan tail without close"
