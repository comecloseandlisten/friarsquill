# VTS / Friar's Quill — лендинг: промпт и дизайн-спецификация

Документ синхронизирован с визуальными токенами десктоп-приложения (`electron/renderer/style.css`): светлая тема **Chronicle** (пергамент, хроника) и тёмная **Inquisition** (трибунал, «кровавый» сумрак).

---

## Часть A — промпт для генератора (копируй целиком)

```
Ты senior frontend + visual designer. Сверстай одностраничный лендинг (HTML + CSS или React + CSS modules) для продукта VTS — локального десктоп-приложения для транскрипции и суммаризации видео без облаков (Electron + Python, Whisper, локальные LLM).

Бренд-настроение: «illuminated manuscript / scriptorium» в светлой теме и «dark oppressive tribunal» в тёмной. Не корпоративный SaaS-пластик; не неоновый киберпанк. Премиальная типографика, тактильный фон, умеренные анимации.

Обязательно:
1) Переключатель темы (light = Chronicle / dark = Inquisition), состояние в localStorage, уважение prefers-color-scheme как дефолт до первого выбора.
2) Подключи Google Fonts: UnifrakturMaguntia, MedievalSharp, IM Fell English, Cinzel, Cormorant Garamond, JetBrains Mono (weights по спецификации ниже).
3) Используй ТОЧНО CSS-переменные и hex из раздела «Дизайн-токены» этого документа — не выдумывай другую палитру.
4) Типографика: display для H1/Hero; script для акцентных слов; body для параграфов; roman (Cinzel) для мелкого uppercase UI; mono для кода/CLI.
5) Фон светлой темы: слоистые radial-gradient + лёгкий grain (как описано). Тёмной: кроваво-коричневые виньетки, без чистого #000.
6) WCAG: основной текст минимум AA на фоне карточек и страницы; кнопки с явным hover/focus-visible (outline контрастный к фону).
7) Секции: Hero (название + подзаголовок + CTA Download / GitHub), «Как это работает» (3–4 шага), «Приватность и офлайн», «Системные требования» (Python 3.10+, ffmpeg, место под модели), FAQ (аккордеон), футер с лицензией/ссылками.
8) Визуальные мотивы: тонкая двойная рамка/«folio header», drop-cap только в hero, сдержанные тени с тёплым оттенком.
9) Адаптив: mobile-first, читаемые размеры, без горизонтального скролла.
10) Никаких сток-фото «улыбающихся людей в офисе». Допустимы абстрактные текстуры пергамента, SVG-печать/сургуч, схема пайплайна.

Выведи полный рабочий код одного файла или минимального набора файлов + краткий комментарий где вставить контент.
```

---

## Часть B — продукт и голос бренда

| Поле | Значение |
|------|----------|
| Рабочее имя продукта | **VTS** — Video Transcription & Summarization |
| UI-бренд в приложении | **Friar's Quill** — средневековая хроника / скрипторий |
| Светлая тема (нейминг в коде) | Chronicle — пергамент, сажа и красная рубрика |
| Тёмная тема | **Inquisition mode** — трибунал, глубокие кроваво-коричневые плоскости |
| Обещание | Локально, без облачных API; транскрипт + структурированный Markdown с таймкодами |
| Тон копирайта | Уверенный, чуть театральный, без канцелярита; можно играть метафорами «кодекс», «folios», но не перебарщивать в юридических текстах |

Лендинг должен ощущаться продолжением приложения: те же шрифты и семантика цвета (пергамент / чернило / рубрика).

---

## Часть C — типографика

### C.1 Семейства и роли

Все стеки уже заданы в приложении; для веба подключай через Google Fonts или self-host.

| Роль CSS | Переменная | Назначение на лендинге | Стек (порядок важен) |
|-----------|------------|------------------------|----------------------|
| Display | `--font-display` | H1, крупные заголовки героя | `UnifrakturMaguntia`, `MedievalSharp`, `Cormorant Garamond`, `Amiri`, `Ma Shan Zheng`, `Shippori Mincho`, `Gowun Batang`, serif |
| Script | `--font-script` | Акцентные фразы, italic-замена, подзаголовки «рукописью» | `MedievalSharp`, `UnifrakturMaguntia`, … (тот же хвост) |
| Body | `--font-body` | Параграфы, списки, основной текст | `IM Fell English`, `MedievalSharp`, `Cormorant Garamond`, …, `Georgia`, serif |
| Roman / caps | `--font-roman` | Кнопки-«читатель», лейблы, uppercase с letter-spacing | `Cinzel`, `IM Fell English`, … |
| Mono | `--font-mono` | Команды терминала, пути, версии | `JetBrains Mono`, `IBM Plex Mono`, ui-monospace, monospace |

**Рекомендации по весам для Google Fonts**

- UnifrakturMaguntia — только 400 (blackletter).
- MedievalSharp — 400.
- IM Fell English — Regular 400 + Italic 400 (если доступно).
- Cinzel — 400, 600, 700 (для иерархии в UI).
- Cormorant Garamond — 400, 500, 600 (fallback для кириллицы/читабельности).
- JetBrains Mono — 400, 500.

### C.2 Масштаб и ритм (ориентиры)

Синхрон с приложением:

- Базовый кегль страницы: `clamp(14px, 0.25vw + 13px, 16px)`.
- Межстрочный интервал body: **1.55**.
- Hero H1 (ориентир): `clamp(1.75rem, 1.2rem + 2.5vmin, 3.25rem)` с `line-height: 1.05–1.1`.
- Drop-cap (если используешь): отдельный span, кегль `clamp(2rem, 1.65rem + 1.6vmin, 3.5rem)`, цвет `--crimson`, лёгкий `text-shadow` как в `.topbar-title .drop-cap`.
- Мелкий uppercase UI (как `.reader-btn`): кегль **10px**, `letter-spacing: 0.16em`, `text-transform: uppercase` — только для коротких меток.

### C.3 Сглаживание

```css
-webkit-font-smoothing: antialiased;
text-rendering: optimizeLegibility;
```

### C.4 Языки

Стек учитывает латиницу → кириллицу → арабскую → CJK. Для чисто кириллического маркетинга, если blackletter режет глаз, допустимо смягчить H1 до **Cormorant Garamond** 600 — но тогда сохрани blackletter хотя бы в логотипной строке или одном слове бренда.

---

## Часть D — цветовая система

### D.1 Общие принципы

- **60%** — фон пергамента / тёмной «страницы» (`--parch-*`).
- **30%** — поверхности карточек, панелей (`--parch-vellum` или `--parch-2` / `--parch-3`).
- **10%** — акценты CTA и рубрика (`--crimson`, `--gold` как вторичный акцент).
- Чернило (`--ink*`) для текста; не использовать чистый `#000` на светлом и чистый `#fff` на тёмном как основной текст (в токенах уже учтено).

### D.2 Chronicle (светлая тема) — `:root`

| Токен | Hex / RGBA | Смысл |
|-------|--------------|--------|
| `--parch-1` | `#f3e6c4` | Основной фон страницы |
| `--parch-2` | `#e9d6a8` | Тень старения, градиент низа |
| `--parch-3` | `#d8c08a` | Более глубокие складки |
| `--parch-vellum` | `#fbf2d6` | «Свежий» пергамент: шапка, карточки |
| `--parch-edge` | `#b69755` | Обводки, разделители |
| `--parch-shadow` | `rgba(74, 48, 16, 0.18)` | Мягкие тени |
| `--ink` | `#2a1a0a` | Основной текст |
| `--ink-soft` | `#4a3018` | Вторичный текст |
| `--ink-faint` | `#7a5a2c` | Третичный / плейсхолдеры |
| `--ink-ghost` | `rgba(74, 48, 16, 0.45)` | Отключённое, водяные знаки |
| `--crimson` | `#9c2a1f` | Рубрика, акцент, опасность/важное |
| `--crimson-deep` | `#6c1a12` | Hover / давление |
| `--crimson-glow` | `rgba(156, 42, 31, 0.18)` | Свечения, подсветки |
| `--azure` | `#2a3f6b` | Лазурь (ссылки, холодный акцент; в Inquisition не переопределён) |
| `--sage` | `#5b6d3a` | Травяной зелёный (успех / выбранное состояние в UI) |
| `--gold` | `#b78319` | Бронза / золото рамок |
| `--gold-bright` | `#d8a838` | Блик |
| `--gold-soft` | `rgba(183, 131, 25, 0.22)` | Подложки свечения |
| `--rule` | `1px solid var(--parch-edge)` | Тонкая линия |
| `--rule-thick` | `2px solid var(--ink)` | Тяжёлая граница (хедер) |

### D.3 Inquisition (тёмная тема) — `body.inquisition-mode` / `[data-theme="dark"]`

Переопределяются токены пергамента, чернил, рубрики и золота. **`--azure` и `--sage` остаются от светлой корневой палитры** — на лендинге проверь контраст; при необходимости заведи `--azure-dark` / `--sage-dark` локально для ссылок на тёмном фоне (см. D.5).

| Токен | Hex / RGBA | Смысл |
|-------|------------|--------|
| `--parch-1` | `#1a0d0a` | Глубокий фон («застывшая кровь») |
| `--parch-2` | `#2a1a15` | Тень |
| `--parch-3` | `#3a2420` | Ещё глубже |
| `--parch-vellum` | `#2d1a16` | Поверхность панелей |
| `--parch-edge` | `#6b4a40` | Кайма бронза-кровь |
| `--ink` | `#e8d4c8` | Основной светлый текст |
| `--ink-soft` | `#c8b8a8` | Вторичный |
| `--ink-faint` | `#9a7a60` | Третичный |
| `--ink-ghost` | `rgba(232, 212, 200, 0.35)` | Приглушённое |
| `--crimson` | `#d4533d` | Яркая кардинальская рубрика |
| `--crimson-deep` | `#a02a1f` | Углубление |
| `--crimson-glow` | `rgba(212, 83, 61, 0.25)` | Свечение |
| `--gold` | `#8b5a2b` | Тёмная бронза |
| `--gold-bright` | `#a06a3b` | Блик |
| `--gold-soft` | `rgba(139, 90, 43, 0.18)` | Подложка |

### D.4 Фоновые градиенты (для parity с приложением)

**Chronicle — базовый слой `.parchment-bg`:**

- Вертикаль: `linear-gradient(180deg, var(--parch-1) 0%, var(--parch-2) 100%)`.
- Виньетки (упрощённо): эллипсы с тёплыми `rgba(255, 240, 200, …)` и `rgba(120, 70, 20, …)` как в приложении.
- Grain: псевдоэлементы с мелкими `radial-gradient` точками и `repeating-linear-gradient` под углами **27deg** и **117deg**, `mix-blend-mode: multiply` на верхнем шуме.

**Inquisition — `body.inquisition-mode` фон:**

- Вертикаль: `linear-gradient(180deg, #1a0d0a 0%, #2a1615 100%)`.
- Виньетки с `rgba(80, 30, 20, …)`, `rgba(40, 15, 10, …)`, и т.д. (см. исходный CSS).
- Точки grain с оттенком `rgba(212, 83, 61, 0.06–0.12)`.

### D.5 Контраст и доработки для лендинга

| Комбинация | Заметка |
|--------------|---------|
| `--azure` на `--parch-1` (Inquisition) | Проверить контраст для ссылок; при провале AA осветлить ссылку до смеси ближе к `#9ebdff` или подчёркивание 2px |
| `--sage` на тёмном | При использовании как фона кнопки — текст на `--parch-vellum` или `--ink` по контрасту |
| Фокус | `outline: 2px solid var(--crimson); outline-offset: 2px` или золотая обводка `var(--gold-bright)` на тёмном |

---

## Часть E — компоненты и паттерны UI

### E.1 Кнопки

- **Primary:** фон `--crimson`, текст на светлом пергаменте `#fbf2d6` или `var(--parch-vellum)`; hover к `--crimson-deep`; тень лёгкая тёплая.
- **Secondary:** прозрачный или `--parch-vellum`, бордер `1px solid var(--ink)` (светлая) / `var(--crimson)` (тёмная шапка как в app).
- **Ghost / link:** текст `--azure` или `--crimson` по контексту; underline по hover.

### E.2 Карточки секций

- Фон: `var(--parch-vellum)` (Chronicle) / `rgba(45, 26, 22, 0.85)` с border `1px solid var(--parch-edge)` (Inquisition).
- `box-shadow`: деликатный, опирайся на `--parch-shadow` / в тёмной — `rgba(212, 83, 61, 0.15)`.

### E.3 Хедер лендинга (аналог `.topbar`)

- Нижняя граница: в Chronicle `2px solid var(--ink)` + тонкая линия; в Inquisition нижняя граница **crimson** как в `body.inquisition-mode .topbar`.

### E.4 Разделители

Используй `--rule` и `--rule-thick` для согласованности с приложением.

---

## Часть F — отступы, сетка, радиусы

В приложении много «книжной» геометрии без сильного скругления. Рекомендация для лендинга:

- Контейнер: `max-width: 1100–1200px`, боковые отступы `clamp(16px, 4vw, 40px)`.
- Вертикальный ритм секций: **72–96px** между крупными блоками на desktop, **48px** на mobile.
- Скругления: **0–4px** (острые углы) или до **8px** только у инпутов — не «Material 16px».
- Gap в сетках фич: **16–24px**.

---

## Часть G — иконки и иллюстрации

- Стиль: гравюра, деревянная гравировка, печать, сургуч, **quill / mace** как в приложении (два режима бренда).
- Цвет SVG: `currentColor` + заливки из `--ink` / `--crimson` / `--gold`.
- Избегать 3D-глосси и градиентных мешей «стартап 2016».

---

## Часть H — моушн

- Переход темы: `transition: background-color 0.25s ease, color 0.25s ease` на `body`.
- Hover кнопок: **0.2s ease**.
- Появление секций: при желании `opacity + translateY(8px)` **0.35–0.5s** с `prefers-reduced-motion: reduce` → отключить.

---

## Часть I — SEO и мета

- Title: «VTS — локальная транскрипция и суммаризация видео».
- Description: офлайн, приватность, Whisper, Markdown, без облака.
- Open Graph: превью с пергаментной текстурой + логотип.

---

## Часть J — готовый блок `:root` для копипаста

```css
:root {
  --parch-1:        #f3e6c4;
  --parch-2:        #e9d6a8;
  --parch-3:        #d8c08a;
  --parch-vellum:   #fbf2d6;
  --parch-edge:     #b69755;
  --parch-shadow:   rgba(74, 48, 16, 0.18);

  --ink:            #2a1a0a;
  --ink-soft:       #4a3018;
  --ink-faint:      #7a5a2c;
  --ink-ghost:      rgba(74, 48, 16, 0.45);

  --crimson:        #9c2a1f;
  --crimson-deep:   #6c1a12;
  --crimson-glow:   rgba(156, 42, 31, 0.18);
  --azure:          #2a3f6b;
  --sage:           #5b6d3a;
  --gold:           #b78319;
  --gold-bright:    #d8a838;
  --gold-soft:      rgba(183, 131, 25, 0.22);

  --font-display: "UnifrakturMaguntia", "MedievalSharp", "Cormorant Garamond", serif;
  --font-script:  "MedievalSharp", "UnifrakturMaguntia", "Cormorant Garamond", serif;
  --font-body:    "IM Fell English", "MedievalSharp", "Cormorant Garamond", Georgia, serif;
  --font-roman:   "Cinzel", "IM Fell English", "Cormorant Garamond", serif;
  --font-mono:    "JetBrains Mono", "IBM Plex Mono", ui-monospace, monospace;

  --rule: 1px solid var(--parch-edge);
  --rule-thick: 2px solid var(--ink);
}

[data-theme="dark"] {
  --parch-1:        #1a0d0a;
  --parch-2:        #2a1a15;
  --parch-3:        #3a2420;
  --parch-vellum:   #2d1a16;
  --parch-edge:     #6b4a40;

  --ink:            #e8d4c8;
  --ink-soft:       #c8b8a8;
  --ink-faint:      #9a7a60;
  --ink-ghost:      rgba(232, 212, 200, 0.35);

  --crimson:        #d4533d;
  --crimson-deep:   #a02a1f;
  --crimson-glow:   rgba(212, 83, 61, 0.25);

  --gold:           #8b5a2b;
  --gold-bright:    #a06a3b;
  --gold-soft:      rgba(139, 90, 43, 0.18);

  --rule-thick: 2px solid var(--crimson);
}
```

Используй `data-theme="dark"` или класс `inquisition-mode` на `<html>` — как удобнее в стеке; логика та же, что у `body.inquisition-mode` в приложении.

---

## Часть K — чеклист перед публикацией

- [ ] Переключатель темы и сохранение выбора
- [ ] Все интерактивные элементы с `:focus-visible`
- [ ] Lighthouse accessibility ≥ 90 (ориентир)
- [ ] Нет горизонтального скролла на 320px
- [ ] Критический CSS или preload для шрифтов display (чтобы не «прыгал» layout)
- [ ] Реальные ссылки на релизы / репозиторий

---

*Документ сгенерирован для репозитория `localvideotranscriber` и отражает `electron/renderer/style.css` на момент составления.*
