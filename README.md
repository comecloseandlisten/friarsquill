<h1 align="center">
  <img src="docs/brand/banner.svg" alt="Friar's Quill — The Chronicler" width="880">
</h1>

<p align="center">
  Локальная рукопись для видео и аудио.<br>
  Ссылка или файл на столе → транскрипт → сводка в Markdown с таймкодами.<br>
  <strong>Electron + Python. Без облачных API.</strong>
</p>

<p align="center">
  <img src="docs/brand/seal-quill.svg" width="28" alt="">
  &nbsp; Chronicle
  &nbsp;&nbsp;·&nbsp;&nbsp;
  <img src="docs/brand/seal-mace.svg" width="28" alt="">
  &nbsp; Inquisition
  &nbsp;&nbsp;·&nbsp;&nbsp;
  <img src="docs/brand/seal-lute.svg" width="28" alt="">
  &nbsp; Ballad
</p>

---

## Три лика пера

Печать в шапке окна меняется вместе с режимом. В бэкенд уходит поле `summary_mode`.

<table>
  <tr>
    <td align="center" width="33%">
      <img src="docs/brand/seal-quill.svg" width="96" alt="Перо — Chronicle"><br><br>
      <strong>Chronicle</strong><br>
      <code>notes</code><br><br>
      Хроника: обзор, тезисы, термины, цитаты, вывод.
    </td>
    <td align="center" width="33%">
      <img src="docs/brand/seal-mace.svg" width="96" alt="Булава — Inquisition"><br><br>
      <strong>Inquisition</strong><br>
      <code>inquisition</code><br><br>
      Трибунал: цель оценки, обязательные пункты, красные флаги, скоринг.
    </td>
    <td align="center" width="33%">
      <img src="docs/brand/seal-lute.svg" width="96" alt="Лютня — Ballad"><br><br>
      <strong>Ballad</strong><br>
      <code>bard</code><br><br>
      Баллада ярких мест: фокус, число моментов, таймкоды и цитаты.
    </td>
  </tr>
</table>

Промпты лежат в `backend/prompts/notes/`, `backend/prompts/inquisition/` и `backend/prompts/bard/`. Другие значения `summary_mode` (`call_check`, `factcheck`, `tldr`) доступны из API — см. [`backend/SUMMARY_MODES.md`](backend/SUMMARY_MODES.md).

---

## Путь рукописи

```
Fetch  →  Distil  →  Scribe  →  Gloss  →  Limn
```

| В окне | Стадия | Что происходит |
| --- | --- | --- |
| **Fetch** | `download` | yt-dlp забирает аудио по ссылке или берётся локальный файл |
| **Distil** | `extract` | ffmpeg приводит дорожку к 16 kHz mono WAV |
| **Scribe** | `transcribe` | faster-whisper, Silero VAD |
| **Gloss** | `summarize` | локальная GGUF-модель через llama.cpp |
| **Limn** | `format` | Markdown с таймкодами, `*_summary.md` |

Транскрипция и суммаризация не держат модели в памяти одновременно. Длинный ролик идёт чанками: MAP по кускам, затем REDUCE в итоговый текст.

---

## Оракул

<p align="center">
  <img src="electron/renderer/assets/oracle/oracle-head-idle.svg" width="160" alt="The Oracle">
</p>

<p align="center">
  <em>The Oracle</em> отвечает только по текущей рукописи:<br>
  транскрипт и сводка этого ролика, с таймкодами. Чужие темы он отклоняет.
</p>

После готовой хроники в окне появляется «Talk to the Oracle». Сессия привязана к паре «транскрипт + сводка» и не смешивается с другим видео. Методы: `oracle_chat`, `oracle_cancel`, `oracle_close`.

---

## С чего начать

Нужны **Python 3.10+**, **Node.js 18+** и **ffmpeg** в `PATH`.

```bash
winget install ffmpeg   # Windows
brew install ffmpeg     # macOS
sudo apt install ffmpeg # Linux
```

```bash
npm install
pip install -r backend/requirements.txt
npm start
```

При первом запуске Whisper и LLM скачиваются сами (порядка 1–2 ГБ).

1. Вставить ссылку или положить локальный файл.
2. Выбрать кодекс Whisper и язык — или оставить автоопределение.
3. **Start Processing**.
4. Забрать текст из окна или открыть готовый `.md`.

---

## Рубрики

| Настройка | По умолчанию | Варианты |
| --- | --- | --- |
| Whisper | `small` | tiny, base, small, medium, large-v3, large-v3-turbo |
| Reckoning | `auto` | auto, int8, float16, float32 |
| Язык | авто | en, ru, ja, zh, de, fr, es, ko и другие |
| Oracle (LLM) | Qwen2.5-1.5B Q4_K_M | любой GGUF из настроек |
| GPU layers | все (`-1`) | частичный offload или только CPU |

Источники: локальное видео или аудио, YouTube и прочие площадки, которые умеет [yt-dlp](https://github.com/yt-dlp/yt-dlp).

---

## Как устроено

```
Electron (UI)  ←——  JSON-RPC по stdio  ——→  Python (backend/main.py)
```

| Слой | Где |
| --- | --- |
| Окно | `electron/main.js`, `electron/preload.js`, `electron/renderer/` |
| Пайплайн | `backend/pipeline.py` |
| Загрузка | `backend/downloader.py` |
| Транскрипция | `backend/transcriber.py` — faster-whisper |
| Сводка | `backend/summarizer.py` — llama-cpp-python |
| Оракул | `backend/oracle.py` |
| Вёрстка Markdown | `backend/formatter.py` |

Это не HTTP: одна JSON-строка на stdin, ответы и прогресс — на stdout.

<details>
<summary><strong>Windows + NVIDIA: CUDA-колесо llama-cpp-python</strong></summary>

<br>

`backend/requirements.txt` ставит **llama-cpp-python** с PyPI. На Windows это CPU-колесо (`py3-none-win_amd64`), и сводка не видит GPU, пока его не заменить CUDA-сборкой.

Индексы abetlen в основном отдают Linux-колёса. Готовые Windows CUDA-сборки держит сообщество: [dougeeai/llama-cpp-python-wheels](https://github.com/dougeeai/llama-cpp-python-wheels/releases) (теги вроде **0.3.20**: `v0.3.20-cuda13.0-sm89` для Ada / RTX 40xx, `…-sm86` для Ampere / RTX 30xx, `…-sm75` для Turing / RTX 20xx).

```powershell
python -m pip install --force-reinstall --no-deps `
  "https://github.com/dougeeai/llama-cpp-python-wheels/releases/download/v0.3.20-cuda13.0-sm89/llama_cpp_python-0.3.20+cuda13.0.sm89.ada-py3-none-win_amd64.whl"
```

Проверка:

```powershell
python -c "import llama_cpp.llama_cpp as L; print('gpu offload:', L.llama_supports_gpu_offload())"
```

Матрица колёс и пропавшие `cublas`-DLL: [`backend/requirements-llama-cuda.txt`](backend/requirements-llama-cuda.txt).  
Сборка из исходников (CUDA toolkit, MSVC, CMake): [`docs/WINDOWS_LLAMA_CPP_CUDA_SOURCE_BUILD.md`](docs/WINDOWS_LLAMA_CPP_CUDA_SOURCE_BUILD.md).

</details>

---

## Дальше по полкам

| | |
| --- | --- |
| Обзор по-русски | [`docs/PROJECT_OVERVIEW_RU.md`](docs/PROJECT_OVERVIEW_RU.md) |
| Карта для людей и моделей | [`llms.txt`](llms.txt) · [`docs/LLM_GUIDE.md`](docs/LLM_GUIDE.md) |
| Куда править под задачу | [`docs/LLM_TASK_MATRIX.md`](docs/LLM_TASK_MATRIX.md) · [`docs/CHANGE_PLAYBOOK.md`](docs/CHANGE_PLAYBOOK.md) |
| Бэкенд и протокол | [`docs/LLM_BACKEND_REFERENCE.md`](docs/LLM_BACKEND_REFERENCE.md) · [`docs/IPC_PROTOCOL.md`](docs/IPC_PROTOCOL.md) |
| Указатель функций | [`docs/FUNCTION_INDEX.md`](docs/FUNCTION_INDEX.md) |

## Лицензия

MIT
