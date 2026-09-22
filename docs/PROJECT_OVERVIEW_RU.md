# Обзор проекта localvideotranscriber (VTS)

## 1. Функции проекта

**Назначение:** локальная (без облачных API) транскрипция и суммаризация видео/аудио с сохранением результата в Markdown с таймкодами.

**Основные возможности:**

- **Источники:** локальный файл (видео/аудио), URL (в т.ч. YouTube и другие сайты, совместимые с [yt-dlp](https://github.com/yt-dlp/yt-dlp)).
- **Транскрипция:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2), VAD (Silero), выбор модели (tiny … large-v3-turbo), тип вычислений (`auto` / int8 / fp16 / fp32), язык вручную или авто/мультиязычный режим (см. `backend/transcriber.py`).
- **Суммаризация локальным LLM:** [llama-cpp-python](https://github.com/abetlen/llama-cpp-python), GGUF (по умолчанию Qwen2.5-1.5B). В интерфейсе три «лица» режима — **Chronicle**, **Inquisition (Tribunal)**, **Ballad**; в конфиге бэкенда это поля `summary_mode` (`notes`, `inquisition`, `bard`) — см. раздел 3, `backend/summarizer.py`, `backend/config.py`.
- **Вывод:** структурированный Markdown (`*_summary.md`), опционально полный транскрипт в отчёте; для пакета источников — агрегированный отчёт (`Pipeline.run_batch` в `backend/pipeline.py`).
- **Прочее:** оценка ETA (`backend/eta.py`), отмена обработки, список моделей (`backend/models_registry.py`), аккуратная загрузка памяти: ASR и LLM не держатся в памяти одновременно.

**UI:** выбор файла/папки вывода, запуск процесса, прогресс по стадиям — `electron/renderer/`.

**Замечание по API:** в Python реализован `process_batch`, но в документации указано, что текущий UI шлёт в основном `process`; пакетная обработка — на стороне бэкенда/протокола.

**Оракул (чат по видео):** после появления «хроники» (сгенерированного Markdown) в UI можно открыть чат **The Oracle** — ответы только по содержанию *этого* ролика, на основе транскрипта и сводки; вопросы вне темы обрабатываются отказом. Подробнее — раздел 4 ниже.

---

## 2. Технологии и архитектура 

**Стек:**

| Слой | Технологии |
|------|------------|
| Десктоп / UI | **Electron** (`electron/main.js`, `electron/preload.js`, renderer HTML/JS) |
| Бэкенд | **Python 3.10+**, точка входа `backend/main.py` |
| Транскрипция | **faster-whisper**, ffmpeg на PATH |
| Загрузка по URL | **yt-dlp** |
| Суммаризация | **llama-cpp-python** + GGUF |


- Транспорт **не HTTP**: один JSON-объект на строку **stdin** (Electron → Python) и **stdout** (Python → Electron); 
- Оркестрация пайплайна в классе **`Pipeline`**: по сути цепочка **download → (extract как стадия прогресса) → diarize (опц.) → transcribe → summarize → format**.

---

## 3. Режимы суммаризации: Chronicle, Inquisition, Ballad

В приложении выбираются три режима кнопками (`electron/renderer/index.html`, логика в `electron/renderer/app.js`). Это **имена в UI**; в JSON в Python уходит поле **`config.summary_mode`** — другое имя, как в таблице ниже.

| В UI | `summary_mode` в бэкенде | Смысл |
|------|-------------------------|--------|
| **Chronicle** | `notes` (значение по умолчанию, если режим не переопределён) | Обычная **хроника**: структурированный конспект по транскрипту — обзор, тезисы по темам, термины, цитаты, вывод. Промпты: `backend/prompts/notes/`. |
| **Inquisition** (подпись *The Tribunal*) | `inquisition` | **Разбор по вашим правилам**: цель оценки, обязательные пункты, красные флаги, критерии скоринга. Панель настроек заполняет `summary_mode_config`: `EVALUATION_GOAL`, `REQUIRED_ITEMS`, `RED_FLAGS`, `SCORING_CRITERIA`. Промпты: `backend/prompts/inquisition/`. |
| **Ballad** | `bard` | **Баллада о ярких моментах**: выжимка «лучших кусков» по фокусу пользователя (юмор, драма, аргументы и т.д.), число моментов, таймкоды и цитаты по чекбоксам. Конфиг: `HIGHLIGHT_FOCUS`, `HIGHLIGHT_COUNT`, `INCLUDE_TIMESTAMPS`, `INCLUDE_QUOTES`. Промпты: `backend/prompts/bard/` (в т.ч. отдельный шаг `reduce_intermediate` для длинных роликов). |

**Технически** в `Config` по-прежнему разрешены и другие значения `summary_mode` (например `call_check`, `factcheck`, `tldr`) для вызовов API или скриптов — см. [`backend/SUMMARY_MODES.md`](../backend/SUMMARY_MODES.md); в стандартном окне Electron выведены только три режима выше.

Пайплайн по чанкам: MAP по кускам транскрипта → REDUCE в финальный Markdown. Для `notes` при включённой тематической сегментации действует цепочка с `topic_reduce` и общими шаблонами в `backend/prompts/_shared/`; `inquisition` и `bard` идут своими `final.txt` и частично обходят модульную сборку «как у notes» (см. `docs/LLM_GUIDE.md`, комментарии в `backend/summarizer.py`).

---

## 4. Оракул (The Oracle)

**Назначение:** пост-транскрипционный **Q&A только по текущему видео**. Модель не отвечает на общие вопросы, код, шутки и т.п. — жёстко зафиксировано системным промптом в [`backend/oracle.py`](../backend/oracle.py) (`ORACLE_SYSTEM_PROMPT`): ответы строятся **только** из переданного транскрипта и «хроники» (сводки), с таймкодами и короткими цитатами где уместно; язык ответа совпадает с языком последнего вопроса пользователя.

**Как это связано с приложением:**

- После успешной обработки в интерфейсе появляется CTA «Talk to the Oracle»; открывается модальное окно чата (`electron/renderer/index.html`, логика в `electron/renderer/app.js`).
- Сессия привязывается к паре «транскрипт + сводка» (хеш); при смене результата история сбрасывается, контексты разных видео не смешиваются.
- **Протокол:** методы `oracle_chat`, `oracle_cancel`, `oracle_close` в [`backend/main.py`](../backend/main.py); ответ стримится событиями `oracle_token` и завершается `result` с полем `oracle: true` (тот же путь вызова LLM, что у суммаризатора, включая подавление «thinking» где настроено).
- **Жизненный цикл:** класс `Oracle` создаётся лениво в [`backend/pipeline.py`](../backend/pipeline.py); модель кэшируется между репликами; `oracle_close` выгружает модель и освобождает память/VRAM.

Подробности реализации и ограничений (лимиты длины, история диалога) — в модулном докстринге в начале `backend/oracle.py`.

---

## 5. Прогресс UI (Fetch → Limn) и диагностика «застыло» на суммаризации

**Имена стадий в интерфейсе** (`electron/renderer/index.html`) соответствуют полю `stage` в сообщениях прогресса:

| Подпись в UI | `stage` в JSON |
|--------------|----------------|
| Fetch | `download` |
| Distil | `extract` |
| Scribe | `transcribe` |
| Gloss | `summarize` |
| Limn | `format` |

**Почему на стадии Gloss (суммаризация) полоса может долго стоять на одном проценте (например 30%):** бэкенд шлёт процент **MAP-фазы** по числу чанков: для трёх чанков первый шаг даёт **30%** (`10 + 60×1/3`) и текст вроде «Summarizing chunk 1/3» — это **старт первого LLM-вызова**, а не «прогресс внутри генерации». До завершения первого чанка полоса не обновится.

**Если в диспетчере задач нет нагрузки на CPU/GPU**, смотрите консоль Electron, строки **`[Python]`** / `[summarizer]`:

| Последняя зафиксированная строка | Смысл |
|-----------------------------------|--------|
| `Llama() mmap/weight load starting` без `Llama() load finished` | Зависание или очень долгая загрузка весов (диск, RAM, антивирус). |
| `load finished`, есть `Chunk 1/N: sending`, нет `done` и нет `LLM chat_completion: native generate returned` | Если перед этим есть `entering native generate` — зависание **внутри** llama.cpp / драйвера CUDA. |
| Повторяется `LLM heartbeat (chat_completion): …s inside native generate` при плоской нагрузке | В Rubrics снизить **«LLM on GPU (layers)»** (частичный offload), проверить **CUDA-сборку** `llama-cpp-python` (готовый wheel или локальная сборка) и драйвер; см. README (Windows / NVIDIA), [`docs/WINDOWS_LLAMA_CPP_CUDA_SOURCE_BUILD.md`](WINDOWS_LLAMA_CPP_CUDA_SOURCE_BUILD.md) — пошаговая сборка из исходников под GPU. |

В `backend/summarizer.py` добавлены явные логи этапов и **heartbeat** раз в 10 с во время блокирующего вызова генерации.

