# PLAN: Доработки UI, выбор моделей, ETA и fast-path по языку

**Дата:** 2026-04-06
**Статус:** Ожидает реализации
**Принцип:** Постепенно, задача за задачей. Каждая задача — отдельный коммит.
**Связанный план:** [PLAN-improvements.md](./PLAN-improvements.md)

---

## Контекст

На текущий момент в проекте:
- `backend/config.py` — содержит `whisper_model`, `language`, `llm_model_path`, `llm_model_repo`, `llm_model_file`, `multilingual` (auto)
- `backend/transcriber.py` — двухпроходная транскрипция с автодетектом языка по 30-сек окнам
- `backend/summarizer.py` — авто-детект локального `.gguf`, иначе HF download
- `electron/renderer/` — UI (index.html / style.css / app.js), скорее всего без полноценных селекторов моделей и без ETA
- Режим батчей и режим обычной обработки живут в `pipeline.py`

Новые требования от пользователя:
1. Редизайн интерфейса по референсам (будут присланы отдельно)
2. Выбор модели и для Whisper (транскрайбер), и для LLM (саммарайзер)
3. Оценка времени обработки (ETA) до запуска и обновление по мере работы
4. Если язык видео **задан явно** — не запускать двухпроходный language detection / батчевый скан, а сразу идти в транскрипцию + саммари
5. Выбор языка **саммари** (отдельно от языка видео) — чтобы можно было видео на EN суммировать на RU и наоборот

---

## ЗАДАЧА 1 — Редизайн UI по референсам

### Цель
Привести интерфейс Electron-приложения в соответствие с референсами, которые пришлёт пользователь.

### Подзадачи
1. **Получить референсы** от пользователя (скриншоты / ссылки / Figma).
2. **Извлечь дизайн-токены:**
   - Цветовая палитра (primary, bg, surface, text, accent, border)
   - Типографика (семейства, размеры, weights)
   - Радиусы, тени, spacing grid
   - Состояния: hover / active / disabled / focus
3. **Составить карту компонентов** (что есть сейчас vs что нужно):
   - Header / file drop zone
   - Боковая панель с настройками (language, whisper model, llm model, mode)
   - Прогресс-бар с ETA
   - Область результата (transcript / summary tabs)
   - Кнопки action (start / cancel / export)
4. **Переписать `style.css`** под новые токены (или перейти на CSS variables / утилиты).
5. **Рефактор `index.html`** — минимальные правки разметки для новой структуры.
6. **Обновить `app.js`** — обработчики новых элементов, анимации состояний.
7. **Smoke test:** прогнать приложение, убедиться что все ручки работают.

### Файлы
- `electron/renderer/index.html`
- `electron/renderer/style.css`
- `electron/renderer/app.js`

### Критерий готовности
- Приложение визуально совпадает с референсами (±)
- Все прежние функции работают
- Никаких захардкоженных цветов вне :root / tokens

### ⚠️ Блокер
Задача стартует **после получения референсов**. До этого — только сбор текущего состояния UI (аудит).

---

## ЗАДАЧА 2 — Выбор моделей для Whisper и LLM

### Проблема
Сейчас модели задаются через конфиг / CLI. В GUI нет явных селекторов. Пользователь хочет выбирать:
- **Whisper model** (tiny / base / small / medium / large-v3 / large-v3-turbo)
- **LLM model для суммаризации** (локальные `.gguf` из папки проекта + возможность скачать с HF)

### Архитектурное решение

**Backend:**
1. Новый эндпоинт / IPC-хендлер `list_models()`:
   - Для Whisper — статический список поддерживаемых размеров + флаг «установлена / будет скачана»
   - Для LLM — сканирует корень проекта на `*.gguf`, возвращает `[{name, path, size_mb}, ...]`. Плюс пресеты из HF (Qwen2.5-7B, Llama-3.1-8B, и т.д.)
2. `config.py` — уже поддерживает нужные поля, добавить только:
   - `llm_model_preset: str | None` — удобный алиас («qwen2.5-7b», «local:Qwen3.5-9B-...gguf»)
3. `pipeline.py` / `summarizer.py` — принимать выбранную модель из конфига, fallback логика без изменений

**Frontend (electron/renderer):**
1. Два новых `<select>`:
   - `#whisperModelSelect` → value = id размера whisper
   - `#llmModelSelect` → value = path или preset
2. При старте приложения `app.js` вызывает `list_models()` и заполняет селекты
3. Выбранные значения пробрасываются в backend вместе с остальными настройками
4. Для LLM — показывать размер файла и пометку «local» / «will download»

### Файлы
- `backend/config.py`
- `backend/summarizer.py`
- `backend/pipeline.py`
- `backend/main.py` (или точка входа IPC)
- `electron/main.js` / `electron/preload.js` — проброс IPC
- `electron/renderer/index.html`, `app.js`

### Критерий готовности
- В UI два рабочих селектора
- Выбор сохраняется между запусками (electron-store или localStorage)
- Смена модели реально меняет используемую модель в бэкенде
- Список локальных `.gguf` обновляется при reopen

---

## ЗАДАЧА 3 — Оценка времени обработки (ETA)

### Проблема
Пользователь не знает, сколько займёт обработка. Для часового видео это критично.

### Архитектурное решение

**Факторы, влияющие на время:**
- Длительность аудио (сек)
- Размер Whisper модели (small ≈ 0.1× RT на CUDA, large-v3 ≈ 0.4× RT)
- CPU vs CUDA
- Включён ли multilingual двухпроходный режим (+30-50%)
- Включена ли диаризация (+20-40%)
- Размер LLM и количество чанков саммарайзера

**Подход: эмпирическая модель + калибровка**

1. **Статические коэффициенты** — таблица «модель × устройство → RT-фактор», замеренная разработчиком один раз:
   ```
   whisper_small_cuda = 0.08
   whisper_small_cpu  = 0.45
   whisper_large_cuda = 0.35
   llm_qwen7b_cuda    = 30 sec / 1000 токенов выхода
   ```
2. **Формула ETA:**
   ```
   eta_transcribe = duration * rt_factor[whisper_model][device] * multilingual_mult
   eta_summarize  = chunks * llm_chunk_time + 1 * llm_final_time
   eta_total      = eta_transcribe + eta_diarization + eta_summarize
   ```
3. **Калибровка:** после каждого прогона замерять реальное время и сохранять в `~/.localvideotranscriber/perf_stats.json`. Обновлять `rt_factor` как скользящее среднее.
4. **Пре-оценка:** делается сразу после выбора файла (нужна длительность через `ffprobe` — уже используется) и выбора настроек. Показывается в UI до кнопки Start.
5. **Runtime ETA:** во время обработки обновлять остаток по реальному прогрессу (`elapsed / done_ratio - elapsed`).

### Файлы
- `backend/eta.py` (новый модуль) — формулы + калибровка
- `backend/pipeline.py` — вызывать `estimate(config, duration)` и прокидывать в progress callback
- `electron/renderer/app.js` — отображение pre-ETA и runtime-ETA
- `electron/renderer/index.html` — место для отображения

### Критерий готовности
- Перед стартом виден estimated time (mm:ss)
- Во время работы runtime-ETA обновляется не реже 1 раз/сек
- После 3-5 прогонов калиброванные значения становятся точнее статических

---

## ЗАДАЧА 4 — Выбор языка саммари

### Проблема
Сейчас саммари генерируется на том же языке, что и транскрипт (или как придётся — зависит от того, что модель решит). Пользователь хочет явно выбирать язык финального саммари: например, видео на английском → саммари на русском.

### Архитектурное решение

**Backend:**
1. `config.py` — новое поле:
   ```python
   summary_language: str = "auto"  # auto = язык транскрипта, либо "ru", "en", "es", ...
   ```
2. `summarizer.py` — в промпт-темплейты (`chunk_summary.txt`, `final_summary.txt`, `batch_chunk`, `batch_final`, плюс все режимы в `prompts/`) добавить плейсхолдер `{target_language}`.
3. Перед вызовом LLM подставлять язык:
   - `auto` → определяется из первого сегмента транскрипта (langdetect / langid) или берётся `info.language` от whisper
   - иначе → явный язык из конфига
4. Инструкция в промпте: `Write the summary in {target_language_name}. Do not mix languages.`

**Промпты:**
- Пройтись по всем файлам в `backend/prompts/` (call_check, chunk_summary, factcheck, final_summary, notes, tldr) и добавить единую секцию с языком
- Хранить маппинг кодов → человекочитаемых имён: `{"ru": "Russian", "en": "English", "es": "Spanish", ...}`

**Frontend:**
1. Новый селектор `#summaryLanguageSelect`:
   - Auto (same as transcript)
   - Russian / English / Spanish / German / French / Chinese / Japanese / ... (те же что и для whisper language)
2. Выбор сохраняется между запусками
3. Пробрасывается в backend вместе с остальными настройками

### Файлы
- `backend/config.py`
- `backend/summarizer.py`
- `backend/prompts/**/*.txt` — все шаблоны
- `backend/pipeline.py` (проброс настройки)
- `electron/renderer/index.html`, `app.js`

### Критерий готовности
- В UI есть селектор языка саммари
- При выборе `ru` для англоязычного видео саммари возвращается на русском
- Auto-режим не ломает старое поведение
- Все режимы (tldr / notes / factcheck / call_check) уважают настройку
- Batch-режим тоже уважает настройку

### Замечание по моделям
- Мелкие LLM (<7B) могут плохо держать таргет-язык на длинных чанках. В UI показать warning если выбрана маленькая модель + нерусский/ненативный для модели язык
- Qwen-2.5-7B хорошо умеет RU/EN/ZH/ES; для других языков качество может просаживаться — добавить tooltip

---

## ЗАДАЧА 5 — Fast-path: если язык задан, пропускать батчевый скан

### Проблема
Сейчас при запуске всегда отрабатывает language detection (окна 30s), даже когда пользователь явно выбрал язык в UI. Для часового видео это лишние 30-60 секунд + лишняя нагрузка.

### Архитектурное решение

**Логика в `pipeline.py` / `transcriber.py`:**

```python
if config.language is not None and config.language != "auto":
    # FAST-PATH: пользователь задал язык явно
    # 1. Пропускаем _detect_languages()
    # 2. Пропускаем batch-scan по окнам
    # 3. Вызываем model.transcribe(..., language=config.language) напрямую
    # 4. Суммарайзер сразу получает единый транскрипт
    segments = transcribe_single_language(audio, lang=config.language)
else:
    # текущая логика: auto-detect → возможно multilingual
    segments = transcribe_with_language_detection(audio)
```

**Изменения:**

1. **`transcriber.py`**
   - Выделить `transcribe_single_language(audio, lang)` как тонкую обёртку над `model.transcribe(audio, language=lang)`
   - Существующую логику переименовать в `transcribe_with_language_detection`
   - Верхнеуровневая `transcribe()` делает if-switch по `config.language`

2. **`pipeline.py`**
   - Прокинуть выбор: если `config.language` задан — fast-path, `multilingual=False`
   - В summarizer путь не меняется

3. **`config.py`**
   - Уже есть `language: str | None`. Добавить семантику: `None` или `"auto"` = детект, всё остальное = fast-path

4. **`electron/renderer`**
   - В селекторе языка первый пункт «Auto-detect», остальные — конкретные языки
   - Если выбрано не auto → в UI можно показать badge «Fast mode» и скрыть опцию multilingual

5. **Логирование**
   - В stderr писать `[pipeline] language=<lang> → fast-path enabled, skipping language scan`

### Файлы
- `backend/transcriber.py`
- `backend/pipeline.py`
- `backend/config.py` (документация)
- `electron/renderer/app.js`, `index.html`

### Критерий готовности
- При выборе конкретного языка в UI двухпроходный скан не запускается (проверяется по логам и по замеру времени)
- При выборе Auto — поведение как раньше
- Тест: прогон одного и того же файла в обоих режимах, fast-path быстрее на ~30-60 сек для часового видео

---

## Порядок выполнения

| # | Задача | Агент | Зависит от |
|---|--------|-------|------------|
| 1 | Fast-path по языку | backend-specialist | — |
| 2 | ETA (backend формулы + API) | backend-specialist | — |
| 3 | Выбор моделей (backend list_models + конфиг) | backend-specialist | — |
| 4 | Выбор языка саммари (backend + промпты) | backend-specialist | — |
| 5 | UI редизайн по референсам | frontend-specialist | ожидание рефов |
| 6 | UI: селекторы моделей + ETA + язык саммари | frontend-specialist | 1, 2, 3, 4, 5 |
| 7 | Интеграционный прогон + калибровка ETA | test-engineer | 1-6 |

Задачи 1-4 не блокируют друг друга — можно делать параллельно. Задача 5 блокирована рефами. Задача 6 собирает всё вместе.

---

## Верификация (Phase X)

- [ ] Fast-path: логи показывают пропуск language detection при явном языке
- [ ] Fast-path: время обработки часового видео на 30+ сек меньше
- [ ] list_models возвращает корректный список локальных .gguf
- [ ] Селекторы в UI заполняются при старте
- [ ] Выбор модели реально меняет используемую модель (проверить по логам загрузки)
- [ ] Настройки сохраняются между запусками приложения
- [ ] Pre-ETA отображается до старта после выбора файла
- [ ] Runtime ETA обновляется в процессе
- [ ] После 3+ прогонов perf_stats.json содержит калибровочные данные
- [ ] UI соответствует референсам (визуальный review)
- [ ] Выбор языка саммари работает: EN видео → RU саммари, RU видео → EN саммари
- [ ] Auto-режим языка саммари совпадает с языком транскрипта
- [ ] Все режимы промптов (tldr / notes / factcheck / call_check) уважают target language
- [ ] Все прежние фичи (multilingual, диаризация, batch mode) работают
- [ ] `python .agent/scripts/checklist.py .` — зелёный
- [ ] Smoke test: короткое видео + длинное видео, обе модели whisper, обе модели LLM

---

## Открытые вопросы

1. **Референсы UI** — ждём от пользователя (скриншоты / Figma / описание стиля)
2. **Пресеты LLM моделей** — какие модели добавить кроме текущей Qwen? (Llama-3.1-8B, Mistral-7B, Gemma-2-9B?)
3. **Единицы ETA** — показывать «2 min 30 sec» или «~3 минуты»?
4. **Калибровка ETA** — на пользователя или глобально по пресетам?
