# PLAN — Починить bard/notes и **убить костыли со стрипперами**

**Status:** Revision 2 (2026-04-17) — радикальная смена подхода
**Owner:** backend-specialist + test-engineer + code-archaeologist
**Scope:** `backend/summarizer.py`, `backend/config.py`, `backend/formatter.py`, `backend/tests/`

---

## 0. Смена подхода (ключевое решение юзера)

**Старая стратегия:** ловить `<think>` и CoT-утечки после генерации четырьмя серийными регексп-стрипперами. На каждый новый кейс утечки добавлять новый регексп. Хуйня бесконечная.

**Новая стратегия:** **отключить thinking на уровне модели** через `/no_think` директиву в system-prompt. Если модель не размышляет — **нечего стрипать**. Три из четырёх стрипперов становятся dead code и удаляются.

Используем Qwen3's native `/no_think` soft-switch (поддерживается chat-template у Qwen3.5-9B-Uncensored, которая и стоит в `F:/localvideotranscriber/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf`).

**Выгода:**
- ~600 строк регексп-костылей в помойку.
- Инференс 1.5-2× быстрее (нет ~50-70% токенов на `<think>` блоки).
- Детерминированнее вывод.
- Никаких «Looks good. Matches constraints. Check:»-утечек в середине текста.
- Retry-логика становится не нужна — пустые выводы пропадают вместе с thinking.

**Цена:**
- На сложных reasoning-задачах thinking чуть поднимает качество. Summarization = форматирование, не reasoning — нам пофиг.
- Оставляем один safety-net стриппер `_strip_thinking_tags` на случай если модель проигнорирует директиву (дёшево, 40 строк регекспа на `<think>` теги).

---

## 1. Что уже сделано (из Revision 1)

Backend-specialist **до срыва лимита** успел залить в код:

| Fix | Где | Статус |
|---|---|---|
| `_detect_repetition_loop(mode="bard"/"inquisition")` short-circuit | `summarizer.py:509-526` | ✅ Оставляем — это настоящий фикс. |
| `Config.repetition_guard: bool = True` | `config.py:70` | ✅ Оставляем. |
| `_apply_hygiene(text, mode)` + `_HYGIENE_PIPELINE` | `summarizer.py:327-373` | ⚠️ Переделать — упростить до `("thinking_tags",)` для всех режимов. |
| `Config.hygiene_escalation: bool = True` + retry в `_llm_call` | `config.py:71`, `summarizer.py:870-920` | 🗑️ Удалить целиком — с `/no_think` не нужно. |

Test-engineer создал `backend/tests/test_summarizer_bard_fix.py` (5 тестов). 3 из них станут obsolete → переписать.

Code-archaeologist до лимита не успел начать.

---

## 2. Что делаем в этой ревизии

### Fix A (P0) — Отключить thinking у модели
**Файл:** `backend/summarizer.py:834-870` (функция `_llm_call`)

1. Новое поле `Config.disable_thinking: bool = True` в `backend/config.py`.
2. В `_llm_call` перед сборкой `messages`: если `disable_thinking`, **добавить `/no_think` в конец user-message** (не system — Qwen3 chat template ищет директиву именно в user-turn). Пример:
   ```python
   user_content = prompt
   if getattr(config, "disable_thinking", True):
       user_content = user_content.rstrip() + "\n\n/no_think"
   messages.append({"role": "user", "content": user_content})
   ```
3. Лог в stderr один раз на старте: `[summarizer] disable_thinking=True — injecting /no_think into user turns`.

### Fix B (P0) — Отключить мёртвые стрипперы **(не удалять!)**
**Файл:** `backend/summarizer.py`

Решение юзера: **не удаляем, комментируем / дисконнектим.** Если `/no_think` вдруг не сработает на каких-то кейсах — один `git diff` и возвращаем. Плюс тесты продолжают ходить в эти функции как в utilities.

Что делаем с каждой:
- `_strip_untagged_leading_cot`, `_strip_numbered_analysis`, `_strip_prompt_instruction_echo` — **оставляем в файле как есть**. Ни строки кода внутри не удаляем.
- Отключаем их только из пайплайна — убираем из `_HYGIENE_PIPELINE` и из legacy-ветки `_finalize_summary_hygiene` (см. Fix C).
- Над каждой функцией добавить шапку-комментарий:
  ```python
  # DISABLED 2026-04-17: replaced by /no_think directive in _llm_call (see
  # Config.disable_thinking). Kept intact for reference, tests, and quick
  # re-enable (add the stripper name back to _HYGIENE_PIPELINE for the
  # affected mode). Do NOT delete — will be restored if /no_think
  # coverage proves insufficient.
  ```

Safety net — `_strip_thinking_tags` остаётся активным для всех режимов (последняя защита от редких утечек `<think>`).

### Fix C (P0) — Упростить hygiene pipeline
**Файл:** `backend/summarizer.py:327-373`

Заменить `_HYGIENE_PIPELINE` на:
```python
_HYGIENE_PIPELINE = {
    # All modes now use only the safety-net stripper. Thinking is disabled
    # at the model level via /no_think (see Config.disable_thinking).
    "notes":       ("thinking_tags",),
    "tldr":        ("thinking_tags",),
    "call_check":  ("thinking_tags",),
    "factcheck":   ("thinking_tags",),
    "inquisition": ("thinking_tags",),
    "bard":        ("thinking_tags",),
}
```

`_apply_hygiene` остаётся — просто вызывает один стриппер. Код становится почти no-op, но точка расширения сохранена на случай если придётся добавить что-то конкретное (например, `chunk_leakage` детектор если снова вылезет).

### Fix D (P0) — Удалить retry-escalation
**Файл:** `backend/summarizer.py:870-920`

Вычистить весь блок ретрая со строгим system-msg и raw-fallback. С отключённым thinking он не нужен. Также удалить:
- `Config.hygiene_escalation`
- Лог `output/metrics/hygiene_drops.jsonl` (если успели добавить)
- Функцию `_log_hygiene_drop` если была

`_llm_call` возвращается к простой форме: call → `_strip_thinking_tags` (safety net) → `_detect_repetition_loop` → return.

### Fix E (P1) — Оставить (без изменений)

- `_detect_repetition_loop` + `Config.repetition_guard` + mode short-circuit — это фикс настоящего бага с bard-списками, не связан с thinking.
- `Fix #5 в formatter.py:87-90` (плейсхолдер `## Overall Summary` при пустом combined_summary) — мелкая косметика.

### Fix F (P1) — Тесты (переписать)
**Файл:** `backend/tests/test_summarizer_bard_fix.py`

**Удалить:**
- `test_bard_labels_survive_hygiene` (нет per-mode strippers в активном пайплайне)
- `test_hygiene_empty_output_triggers_retry` (retry удалён)

**Оставить без изменений:**
- `test_strip_untagged_cot_preserves_long_valid_prose` — стриппер жив как функция (см. Fix B), тест продолжает валидировать её поведение как утилиты. Это наш «страховой полис» на случай возврата.

**Оставить:**
- `test_bard_4_moments_not_truncated` (тестирует Fix #1)
- `test_repetition_guard_still_catches_real_loop`

**Добавить:**
- `test_no_think_injected_into_user_message` — MagicMock LLM, проверить что последнее user-сообщение оканчивается на `/no_think`.
- `test_disable_thinking_false_does_not_inject` — `Config(disable_thinking=False)` → нет `/no_think`.
- `test_strip_thinking_tags_still_runs_as_safety_net` — мок LLM возвращает `<think>x</think>Real.` → результат `"Real."`.
- `test_config_no_hygiene_escalation_field` — `Config()` не имеет поля `hygiene_escalation` (regression против случайного восстановления).

### Fix G (P1) — Cleanup
Задача не изменилась по сравнению с Revision 1:
- Удалить `backend/summarizer легаси.py`, `backend/summarizer.py.bak`, `backend/summarizer.py.tmp`.
- Обновить `.gitignore`.
- Создать `scripts/cleanup_stale_output.py`.

---

## 3. Порядок работ

Зависимости:
```
Fix A (disable_thinking)  ───┐
Fix B (delete strippers)  ───┼─→ Fix F (tests updated)
Fix C (hygiene pipeline)  ───┤
Fix D (remove escalation) ───┘
Fix E (cosmetics) + Fix G (cleanup) — параллельно, независимы
```

Можно катить одним PR: backend-specialist делает A+B+C+D+E в `summarizer.py`+`config.py`+`formatter.py`. test-engineer параллельно переписывает тесты. code-archaeologist чистит хвосты.

---

## 4. Acceptance criteria (Definition of Done)

1. **Повторный bard-прогон** видео James Clear (`dck_MBRg-Do`) → минимум 3 момента в `## Best Moments` с полным набором полей.
2. **Повторный notes-прогон** русской лекции (`YtEZfpjpuP8`) → **нет** мета-фраз «Looks good», «Check:», «Final check on», «My draft uses». **Нет** `## Chunk N` в финальной секции.
3. В выводе нет `<think>`, `<thinking>`, `<reasoning>` тегов.
4. `grep "/no_think"` в последнем user-message для каждого `_llm_call` (можно проверить через debug-лог или тест).
5. `wc -l backend/summarizer.py` — ~1100-1200 строк (убрали retry и чуть-чуть вокруг; сами стрипперы закомментированы-активированы, остались на месте).
6. `pytest backend/tests/` — зелёные, включая переписанный `test_summarizer_bard_fix.py`.
7. Файлы `summarizer легаси.py`, `.bak`, `.tmp` удалены.
8. `python .agent/scripts/checklist.py .` проходит.

---

## 5. Риски

- **Qwen3 chat template не понимает `/no_think`.** Проверить заранее: запустить `_llm_call` с тестовым промптом `"Say hi"` и директивой, убедиться что в выводе нет `<think>`. Если не работает — fallback: инжектить в system-prompt как инструкцию `"Do not use <think> blocks. Output the answer directly."` + оставить `_strip_thinking_tags`.
- **Качество saммари упадёт на сложных видео.** Митигация: добавить CLI-флаг `--enable-thinking` для A/B-сравнения. Default = off (нам нужна скорость и чистота).
- **Всё-таки вылезет какой-нибудь новый вид утечки.** Митигация: `_strip_thinking_tags` оставлен как safety net; если модель проигнорит `/no_think` и напишет `<think>`, мы поймаем.

---

## 6. Что НЕ входит

- Смена самой модели.
- UI в electron (кроме добавления чекбокса «Enable thinking» опционально, но это P2).
- Рефакторинг `_summarize_with_topics` и модульного пайплайна.
- Documentation update (пока код меняется — не плодим устаревшие доки).

---

## 7. Следующий шаг

После `10 PM Asia/Bangkok` (сброс лимита Anthropic):
1. Перезапустить backend-specialist с этим планом — он рубит A+B+C+D+E.
2. Перезапустить test-engineer — он переписывает тесты по Fix F.
3. Перезапустить code-archaeologist — он делает Fix G.
4. Финальная верификация: запуск обоих видео из acceptance criteria, pytest, checklist.

Жду твой Y/N на Revision 2. Если другое направление хочешь — говори.
