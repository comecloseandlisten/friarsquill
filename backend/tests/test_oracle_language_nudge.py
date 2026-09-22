"""Oracle reply-language nudge (Cyrillic / CJK)."""

import oracle


def test_cyrillic_gets_russian_nudge():
    n = oracle._reply_language_nudge("Как жить с кризисом?")
    assert "Cyrillic" in n
    assert "Russian" in n or "language" in n


def test_ascii_only_no_nudge():
    assert oracle._reply_language_nudge("What is said at 12:34?") == ""


def test_japanese_kana_triggers_japanese():
    n = oracle._reply_language_nudge("これは何ですか")
    assert "Japanese" in n


def test_chinese_han_triggers_chinese():
    n = oracle._reply_language_nudge("这段视频说了什么？")
    assert "Chinese" in n
