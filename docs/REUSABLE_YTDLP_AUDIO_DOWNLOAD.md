# Паттерн: загрузка аудио по URL через yt-dlp + FFmpeg (для STT / пайплайнов)

Документ описывает **переносимый** подход: по HTTP(S)-ссылке (YouTube и сотни других сайтов, поддерживаемых [yt-dlp](https://github.com/yt-dlp/yt-dlp)) получить на диске **один аудиофайл**, обычно **WAV 16 kHz mono** — удобно для Whisper и аналогов.

Это не обязательно «скачать целое видео в MP4»: для транскрипции достаточно **лучшего аудиопотока** и последующей нормализации через ffmpeg.

---

## Зависимости

| Компонент | Назначение |
|-----------|------------|
| **Python 3.10+** | Среда |
| **`yt-dlp`** | Извлечение потоков, загрузка, постпроцессоры |
| **`ffmpeg`** в `PATH` | Постпроцессор `FFmpegExtractAudio` вызывает ffmpeg |

```text
pip install yt-dlp
```

На машине должен быть установлен **ffmpeg** (winget / brew / пакетный менеджер ОС).

---

## Идея в двух шагах

1. **yt-dlp** качает выбранный формат (предпочтительно только аудио: `bestaudio/best`).
2. **Постпроцессор** `FFmpegExtractAudio` конвертирует в **WAV** с нужной частотой и каналами (типично **16 kHz, mono** для Whisper).

Отдельный шаг «извлечь звук из видеофайла» в вашем приложении тогда можно не делать, если выход уже WAV.

---

## Минимальный переносимый фрагмент (Python)

Ниже — логика без привязки к конкретному UI или фреймворку. Подставьте свой `output_dir` (например `tempfile.TemporaryDirectory()`).

```python
import time
from pathlib import Path


def download_audio_for_stt(
    url: str,
    output_dir: Path,
    *,
    progress_cb=None,  # optional: callable(int) -> None, 0..100
) -> Path:
    import yt_dlp

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_dir / "source_audio"

    def hook(d):
        if d["status"] != "downloading" or progress_cb is None:
            if d["status"] == "finished" and progress_cb:
                progress_cb(100)
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes") or 0
        if total > 0:
            progress_cb(int(downloaded / total * 100))

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(stem),
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "concurrent_fragment_downloads": 1,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        ],
        "postprocessor_args": {
            "extractaudio": ["-ar", "16000", "-ac", "1"],
        },
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        raise RuntimeError(f"Audio fetch failed: {e}") from e

    for ext in (".wav", ".m4a", ".webm", ".opus", ".ogg", ".mp3", ".mp4", ".mkv", ".aac"):
        p = stem.with_suffix(ext)
        if p.exists():
            return p.resolve()

    candidates = sorted(output_dir.glob("source_audio*"))
    if candidates:
        return candidates[0].resolve()

    raise FileNotFoundError("Download failed: no audio file produced")
```

### Почему два способа найти файл

yt-dlp и ffmpeg могут добавить суффикс или другое расширение в зависимости от сайта и кодека. Надёжно: сначала проверить ожидаемые расширения у `stem`, затем **glob** по префиксу.

---

## Ключевые опции (шпаргалка для копирования в другой проект)

| Опция | Типичное значение | Зачем |
|--------|-------------------|--------|
| `format` | `bestaudio/best` | Меньше трафика и времени, чем полное видео |
| `noplaylist` | `True` | Одна сущность по ссылке, не весь плейлист |
| `outtmpl` | путь без расширения или с `%(ext)s` | Контроль имени; см. доку yt-dlp |
| `postprocessors` | `FFmpegExtractAudio` + `wav` | Единый контейнер для STT |
| `postprocessor_args["extractaudio"]` | `-ar 16000 -ac 1` | Whisper-friendly |
| `retries` / `fragment_retries` | `5–10` | Устойчивость к сбоям сети |
| `concurrent_fragment_downloads` | `1` | Меньше параллельной нагрузки на хост/диск |

При необходимости добавьте `cookiefile`, `proxy`, `http_headers` — через тот же словарь `opts` ([сводка опций](https://github.com/yt-dlp/yt-dlp#usage-and-options)).

---

## Интеграция в чужой проект

1. **Вход:** одна строка URL (после валидации: только `http://` / `https://`, при желании allowlist доменов).
2. **Рабочая директория:** временная папка процесса; после транскрипции удалить.
3. **Прогресс:** проброс `progress_hooks` в ваш UI (проценты грубые, если `total_bytes` неизвестен — показывать неопределённый прогресс или спиннер).
4. **Отмена:** yt-dlp не всегда мгновенно прерывается; типичный паттерн — subprocess с отдельным процессом или флаг + проверка между этапами. Для API `YoutubeDL` смотрите актуальные возможности версии yt-dlp в вашем lockfile.
5. **Безопасность:** не передавайте произвольные строки в shell — используйте только API Python; не логируйте полные URL с секретами в query.

---

## Отличие от «скачать видео файлом»

Если в другом проекте нужен именно **MP4/MKV**, замените или дополните конфиг:

- уберите `FFmpegExtractAudio` и задайте `format` вроде `bv*+ba/b` или фиксированный формат;
- либо оставьте загрузку как есть и конвертируйте отдельным вызовом ffmpeg.

Паттерн из этого документа ориентирован на **аудио → WAV для распознавания речи**.

---

## Реализация-референс в репозитории

Этот же подход используется в `backend/downloader.py` (функция `download_audio`) и вызывается из `backend/pipeline.py` для URL-источников.
