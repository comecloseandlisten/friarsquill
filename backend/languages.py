"""
Language support module for summary language selection.

Handles resolution of summary language codes to human-readable names,
with fallback to transcript language and English as ultimate default.
"""

from typing import NamedTuple


class SummaryAssemblyHeadings(NamedTuple):
    """Fixed ``##`` section titles for programmatic modular-summary assembly."""

    overview: str
    conclusion: str
    part_template: str  # e.g. ``"Part {n}"`` — use ``.part(n)``
    beginning: str

    def part(self, n: int) -> str:
        return self.part_template.format(n=n)


# Localized headings for ``summarizer._assemble_final`` / topic grouping fallbacks.
# Prompts already ask the model for {target_language}; these strings must match.
_ASSEMBLY_HEADINGS: dict[str, SummaryAssemblyHeadings] = {
    "en": SummaryAssemblyHeadings(
        overview="About this video",
        conclusion="Conclusion",
        part_template="Part {n}",
        beginning="Beginning",
    ),
    "ru": SummaryAssemblyHeadings(
        overview="О чём видео",
        conclusion="Итог",
        part_template="Часть {n}",
        beginning="Начало",
    ),
    "es": SummaryAssemblyHeadings(
        overview="De qué trata el vídeo",
        conclusion="Conclusión",
        part_template="Parte {n}",
        beginning="Inicio",
    ),
    "de": SummaryAssemblyHeadings(
        overview="Inhalt des Videos",
        conclusion="Fazit",
        part_template="Teil {n}",
        beginning="Anfang",
    ),
    "fr": SummaryAssemblyHeadings(
        overview="À propos de cette vidéo",
        conclusion="Conclusion",
        part_template="Partie {n}",
        beginning="Introduction",
    ),
    "zh": SummaryAssemblyHeadings(
        overview="视频简介",
        conclusion="总结",
        part_template="第 {n} 部分",
        beginning="开篇",
    ),
    "ja": SummaryAssemblyHeadings(
        overview="動画の概要",
        conclusion="まとめ",
        part_template="パート{n}",
        beginning="冒頭",
    ),
    "pt": SummaryAssemblyHeadings(
        overview="Sobre o vídeo",
        conclusion="Conclusão",
        part_template="Parte {n}",
        beginning="Início",
    ),
    "it": SummaryAssemblyHeadings(
        overview="Di cosa parla il video",
        conclusion="Conclusione",
        part_template="Parte {n}",
        beginning="Inizio",
    ),
    "tr": SummaryAssemblyHeadings(
        overview="Videonun özeti",
        conclusion="Sonuç",
        part_template="Bölüm {n}",
        beginning="Giriş",
    ),
    "ar": SummaryAssemblyHeadings(
        overview="عن الفيديو",
        conclusion="الخلاصة",
        part_template="الجزء {n}",
        beginning="المقدمة",
    ),
    "uk": SummaryAssemblyHeadings(
        overview="Про що відео",
        conclusion="Підсумок",
        part_template="Частина {n}",
        beginning="Початок",
    ),
    "pl": SummaryAssemblyHeadings(
        overview="O czym jest film",
        conclusion="Podsumowanie",
        part_template="Część {n}",
        beginning="Wstęp",
    ),
    "nl": SummaryAssemblyHeadings(
        overview="Over de video",
        conclusion="Conclusie",
        part_template="Deel {n}",
        beginning="Begin",
    ),
    "ko": SummaryAssemblyHeadings(
        overview="영상 소개",
        conclusion="결론",
        part_template="제 {n} 부분",
        beginning="도입",
    ),
}


def get_summary_assembly_headings(lang_code: str) -> SummaryAssemblyHeadings:
    """Section titles for modular pipeline; unknown ISO codes fall back to English."""
    code = (lang_code or "en").lower().strip()
    return _ASSEMBLY_HEADINGS.get(code, _ASSEMBLY_HEADINGS["en"])


LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
    "es": "Spanish",
    "de": "German",
    "fr": "French",
    "zh": "Chinese",
    "ja": "Japanese",
    "pt": "Portuguese",
    "it": "Italian",
    "tr": "Turkish",
    "ar": "Arabic",
    "uk": "Ukrainian",
    "pl": "Polish",
    "nl": "Dutch",
    "ko": "Korean",
}


# Native-language "write ONLY in X" enforcement phrases.
# The prompt bodies in ``backend/prompts`` are written in Russian; small local
# models default to Russian unless pressured in the TARGET language itself.
# These strings are prepended to every summarization prompt so the model sees
# the language requirement in the output language BEFORE the Russian rubric.
_LANG_ENFORCEMENT_NATIVE: dict[str, str] = {
    "ru": "Отвечай ТОЛЬКО на русском языке. Каждое слово, заголовок и пункт списка — на русском.",
    "en": "Write ONLY in English. Every word, heading, and list item must be in English — no Russian, no mixed-language text.",
    "es": "Escribe SOLO en español. Cada palabra, título y elemento de lista debe estar en español — sin ruso ni texto mixto.",
    "de": "Schreibe AUSSCHLIESSLICH auf Deutsch. Jedes Wort, jede Überschrift und jeder Listenpunkt muss auf Deutsch sein — kein Russisch, keine Sprachmischung.",
    "fr": "Écris UNIQUEMENT en français. Chaque mot, titre et élément de liste doit être en français — pas de russe, pas de texte mixte.",
    "zh": "只用中文写作。每一个字、每一个标题、每一个列表项都必须是中文——不要使用俄语,不要混合语言。",
    "ja": "日本語のみで書いてください。すべての単語、見出し、リスト項目は日本語でなければなりません。ロシア語や言語の混在は禁止です。",
    "pt": "Escreva APENAS em português. Cada palavra, título e item de lista deve estar em português — sem russo, sem texto misto.",
    "it": "Scrivi SOLO in italiano. Ogni parola, titolo ed elemento di lista deve essere in italiano — niente russo, niente testo misto.",
    "tr": "YALNIZCA Türkçe yaz. Her kelime, her başlık ve her liste öğesi Türkçe olmalı — Rusça yok, karışık dil yok.",
    "ar": "اكتب باللغة العربية فقط. يجب أن تكون كل كلمة وكل عنوان وكل عنصر في القائمة باللغة العربية — بدون روسية، بدون مزج لغات.",
    "uk": "Пиши ЛИШЕ українською мовою. Кожне слово, заголовок і пункт списку має бути українською — без російської, без мішанини мов.",
    "pl": "Pisz WYŁĄCZNIE po polsku. Każde słowo, nagłówek i element listy musi być po polsku — bez rosyjskiego, bez mieszania języków.",
    "nl": "Schrijf UITSLUITEND in het Nederlands. Elk woord, elke kop en elk lijstitem moet in het Nederlands zijn — geen Russisch, geen gemengde taal.",
    "ko": "한국어로만 작성하세요. 모든 단어, 제목, 목록 항목은 한국어여야 합니다. 러시아어 금지, 혼합 언어 금지.",
}


def language_enforcement_block(target_language_name: str, target_language_code: str) -> str:
    """
    Strong, target-language-native header to prepend to every summarization
    prompt. Addresses the root cause of "summary comes out in Russian even
    though English was selected": the prompt body is mostly Russian, which
    overrides a single trailing "write in English" line for small models.

    Format (for non-English targets):
        {native "write only in X" line}

        {English reinforcement sentence referencing the target language name}

        ---

    For English targets we skip the native line (it would duplicate the
    English reinforcement). For Russian targets we keep the native line only,
    since the underlying rubric is already Russian.
    """
    code = (target_language_code or "").lower().strip()
    name = target_language_name or code.upper() or "English"

    native = _LANG_ENFORCEMENT_NATIVE.get(code)
    english_reinforcement = (
        f"CRITICAL LANGUAGE REQUIREMENT: Write the ENTIRE response in {name} only. "
        f"Every word, heading, quote label, and list item must be in {name}. "
        f"Do NOT use Russian (or any other language) anywhere in the output. "
        f"If an instruction in this prompt is phrased in Russian, translate it mentally — "
        f"do NOT echo Russian words into the answer."
    )

    if code == "ru":
        # Russian target: rubric is already Russian — native line alone is enough.
        body = native or english_reinforcement
    elif code == "en" or native is None:
        # English target or unknown code: English reinforcement only.
        body = english_reinforcement
    else:
        # Non-Russian / non-English known target: native line first, then English.
        body = f"{native}\n\n{english_reinforcement}"

    return f"{body}\n\n---\n\n"


def resolve_summary_language(config_value: str, transcript_lang: str | None) -> tuple[str, str]:
    """
    Resolve the target summary language code and human-readable name.

    Args:
        config_value: User-configured summary language ("auto" or ISO code like "ru", "en", etc.)
        transcript_lang: Detected transcript language code from Whisper (e.g. "en", "ru")

    Returns:
        Tuple of (language_code, language_name) where:
        - language_code: ISO 639-1 code (e.g. "ru", "en")
        - language_name: Human-readable name (e.g. "Russian", "English")

    Logic:
        1. If config_value == "auto": use transcript_lang if available, else fall back to "en"
        2. If config_value is a known code: use it directly
        3. If config_value is unknown: return code as-is with name = code.upper()
    """
    # Handle "auto" mode: use transcript language or fall back to English
    if config_value == "auto" or config_value is None:
        resolved_code = transcript_lang or "en"
    else:
        resolved_code = config_value.lower().strip()

    # Look up human-readable name
    resolved_name = LANGUAGE_NAMES.get(resolved_code, resolved_code.upper())

    return (resolved_code, resolved_name)
