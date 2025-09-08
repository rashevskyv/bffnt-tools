# BFFNT Tools (Switch-Toolbox auxiliary)

Цей каталог містить два утилітарні інструменти для BFFNT/BCFNT/BRFNT:

- `bffnt.py` — CLI-обгортка: розпаковує шрифти у читабельний набір PNG + `font.json` і пакує назад.
- `bffnt_viewer_qt.py` — швидкий переглядач аркушів і метрик на Qt/OpenGL з інтерактивним редагуванням ширин та мапінгу символів.

### Архітектура CLI/модулів
- `bffnt_common.py` — спільні парсери (FINF/TGLP/CWDH/CMAP), утиліти ендiанності та GX2/BC4 хелпери.
- `bffnt_unpack.py` — анпакер, експортує `unpack_bffnt(path, rotate180=False, flip_y=False)`.
- `bffnt_pack.py` — пакер, експортує `pack_from_json_folder(folder, out_path=None)`.
- `bffnt.py` — тонкий CLI, що викликає модулі пакера/анпакера.

Приклади CLI:
- Розпакування: `python tools/bffnt_py/bffnt.py fonts/CKingMain.bffnt`
- Пакування: `python tools/bffnt_py/bffnt.py pack fonts/CKingMain fonts/CKingMain_test.bffnt`

## Встановлення залежностей

- Python 3.9+
- Рекомендовано: `pip install -r tools/bffnt_py/requirements.txt`
  - PySide6>=6.5 (або альтернатива PyQt5>=5.15)
  - Pillow>=10.0 (перевірки/PNG у `bffnt.py`)

## bffnt.py — розпаковувач/пакувальник

Використання (розпаковка):

- Розпакувати все у теці (за замовчуванням — тека скрипта):

```
python tools/bffnt_py/bffnt.py          # шукає *.bffnt у теці скрипта
python tools/bffnt_py/bffnt.py --all    # шукає у поточній робочій теці
python tools/bffnt_py/bffnt.py --all -r # рекурсивно у поточній теці
```

- Розпакувати конкретний файл або теку:

```
python tools/bffnt_py/bffnt.py path/to/font.bffnt
python tools/bffnt_py/bffnt.py path/to/dir         # усі шрифти у теці
python tools/bffnt_py/bffnt.py -r path/to/dir      # рекурсивно
```

- Додаткові прапорці під час розпакування PNG:

```
--rotate180  # повернути аркуші на 180° (додає суфікс .rot180 до назви)
--flipY      # віддзеркалити по Y (додає суфікс .flipY до назви)
```

- Пакування назад (за замовчуванням — біт‑у‑біт з `file_b64` з `font.json`):

```
python tools/bffnt_py/bffnt.py pack <тека_з_font.json> [вихід.bffnt]
```

Вихід розпакування:

- `<name>/font.json` — метадані (FINF/TGLP/CMAP/CWDH, маповані гліфи та ширини)
- `<name>/sheet_<i>[.rot180][.flipY].png` — аркуші гліфів (як є, без рекомпресії)

Заувага: декодування аркушів підтримує BC4 (GX2). Інші формати можуть бути позначені як `НЕ_ДЕКОДОВАНО`.
\
Особливості `bffnt.py`:
- Без аргументів: шукає файли у теці скрипта.
- `--all` — шукає у поточній робочій теці, `-r/--recursive` — рекурсивно.
- Приймає і шляхи до тек: обробляє всі шрифти всередині (з `-r` — рекурсивно).
- Підтримка трансформацій PNG: `--rotate180`, `--flipY` (додають суфікси до імен файлів).
- Формати секцій: FINF, TGLP, CMAP, CWDH (читаються у `font.json`).
- Обмеження: декодування зображень — BC4 (GX2); інші формати помічаються як `НЕ_ДЕКОДОВАНО`.

## bffnt_viewer_qt.py — переглядач/редактор

Можливості:

- Швидкий QGraphicsView з OpenGL-бекендом (за наявності драйвера).
- Масштабування колесом миші (AnchorUnderMouse); панорамування середньою кнопкою.
- Фліп/поворот лише зображення (сітка/оверлеї не змінюються; фон композититься до чорного).
- Вибір комірки → перегляд і редагування ширин (Left/Glyph/Char) та мапінгу символа.
- Інтерактивні хендли: тягніть синю/червону/зелену межі мишею.
- Автовизначення ширин: поріг (Alpha only або премультиплікована яскравість), Adaptive+Quantile.
- Редагування мапінгу символа: Unicode-код <-> символ + превʼю з автосинхронізацією.
- Автозбереження: при переході між комірками/аркушами та при закритті вікна; статусбар показує Saved/Unsaved.
- Памʼять налаштувань: остання тека, масштаб, фліп/ротація, Auto pad/threshold, Alpha only, Adaptive/Quantile.
- Кольори: Left — синій гайд; Glyph — червона межа; Char — зелена межа; правило — Char ≤ Glyph, Glyph ≥ Left.

Запуск:

```
python tools/bffnt_py/bffnt_viewer_qt.py
```

## Формат BFFNT/BCFNT/BRFNT (огляд)

- Заголовок/сигнатура: `FFNT` (NX/Cafe), `CFNT` (Ctr), `RFNT/TNFR/RFNA` (Wii).
- Ендіанність і версія:
  - Після сигнатури: BOM (`0xFFFE` → LE), далі `HeaderSize` і `Version` (для FFNT/CFNT) або 16‑бітна версія (для RFNT*).
  - Визначення платформи в нашому скрипті: Cafe, Ctr, NX (для LE + версія ≥ 0x04010000).
- Секції (пошук за 4CC у файлі):
  - `FINF` — базова інформація про шрифт: висота/ширина, `ascent`, `line_feed`, дефолтні `left/glyph/char`, спосіб кодування, офсети на `TGLP/CWDH/CMAP`.
  - `TGLP` — розкладка гліфів у текстурних аркушах: `cell_width/height`, `max_char_width`, `base_line`, `sheet_size/count`, `format`, `rows/cols`, `sheet_width/height`, `sheet_data_off`.
    - У Cafe/NX аркуші зберігаються у плиточному форматі GPU (GX2). У Wii/Ctr структура інша; ми читаємо мінімально потрібні поля.
  - `CWDH` (ланцюжок блоків) — метрики ширин для індексів: на діапазон `start_idx..end_idx` подаються трійки `left (i8), glyph (u8), char (u8)`.
  - `CMAP` (ланцюжок блоків) — мапінг `codepoint → glyph_index` трьома методами:
    - Direct (діапазон з кроком), Table (масив), Scan (пари; на NX код 32‑бітний, інакше 16‑бітний).
- Індекси й розкладка по сітці: `per_sheet = rows * cols`; `sheet = index // per_sheet`; `rem = index % per_sheet`; `grid_x = rem % rows`; `grid_y = rem // rows`.

## Метод розпаковки/запаковки

- Розпаковка (bffnt.py):
  - Зчитує сигнатуру/ендіанність/версію; знаходить `FINF/TGLP/CWDH/CMAP`.
  - Для FFNT офсети секцій зберігаються як `target + 8` — компенсується під час парсингу.
  - Побудова `font.json` з полями `signature/platform/finf/tglp`, списком `glyphs` (код, символ, індекс, аркуш, координати сітки, ширини із `CWDH`).
  - Витяг аркушів із `TGLP`: якщо `format == 12 (BC4_UNORM)` і платформа Cafe/NX — виконується десвізл GX2 і декодування в PNG (RGBA з альфою із BC4).
    - Прапорці `--rotate180/--flipY` застосовуються до PNG (суфікси у назві, без модифікації джерела).
    - Інші формати поки не декодуються (помічаються як `НЕ_ДЕКОДОВАНО`).
  - В `font.json` додається `file_b64` з оригінальним файлом для біт‑ідентичного пакування та `png_ops` для довідки.

- Запаковка (режим `pack`):
  - За замовчуванням це байт‑у‑байт “repack”: пакер бере сирий файл із `file_b64` (або шукає оригінал поруч за `source_file`) і
    накладає зміни: CWDH (ширини), CMAP (відповідності) та, починаючи з цієї версії, заголовок FINF (поля висоти/лідів/дефолтів) з `font.json`.
  - Щоб примусово ігнорувати `file_b64` у `font.json` (коли ви підкладаєте інший `font.json` і хочете уникнути «прилипання» до старого базового файлу),
    додайте у `font.json`: `"ignore_file_b64": true`. Тоді пакер спробує взяти базовий файл з теки вище за `source_file` або ім'я теки.
  - Додатково (опційно) виконується перевірка PNG проти оригіналу: для кожного аркуша порівнюється канал альфи (або `L`) з віддекодованим оригіналом (після відкату суфіксів `.rot180/.flipY`). Це лише верифікація незмінності, не реконструкція.
  - Важливо: зміни в `font.json` (ширини/мапінг), а також PNG, наразі не збираються назад у новий BFFNT — для цього потрібен повноцінний пакувальник.

Обмеження й зауваги:
- Підтримка десвізлу/декодування аркушів — лише `BC4_UNORM (format 12)` на Cafe/NX (GX2). Інші формати позначаються як не підтримані.
- Пакування — лише біт‑ідентичне через `file_b64`; редактор змінює лише зовнішній `font.json` і PNG (для перегляду), а не вихідний BFFNT.

## Приклад `font.json`

Скорочений приклад з ключовими полями:

```json
{
  "signature": "FFNT",
  "bom": 65279,
  "version": 50331648,
  "header_size": 20,
  "platform": "Cafe",
  "source_file": "MyFont.bffnt",
  "finf": {
    "type": 2,
    "height": 76,
    "width": 62,
    "ascent": 59,
    "line_feed": 76,
    "alter_char_index": 0,
    "default_left": 0,
    "default_glyph": 62,
    "default_char": 62,
    "char_encoding": 1
  },
  "tglp": {
    "cell_width": 62,
    "cell_height": 74,
    "max_char_width": 62,
    "base_line": 58,
    "sheet_size": 524288,
    "sheet_count": 2,
    "format": 12,
    "rows": 16,
    "cols": 13,
    "sheet_width": 1024,
    "sheet_height": 1024
  },
  "glyphs": [
    {
      "codepoint": "U+0041",
      "char": "A",
      "index": 65,
      "sheet": 0,
      "grid_x": 1,
      "grid_y": 4,
      "width": { "left": 2, "glyph": 40, "char": 38 }
    }
  ],
  "sheet_png": ["sheet_0.png", "sheet_1.png"],
  // опційно: inline‑база для біт‑ідентичного repack (може бути опущено)
  "file_b64": "...",
  // опційно: заборонити використання file_b64 і шукати фізичний файл поруч
  "ignore_file_b64": true,
  "png_ops": { "rotate180": false, "flipY": false }
}
```

Довідка за полями:
- `signature/platform/bom/version/header_size` — базовий заголовок.
- `finf` — метрики шрифту і спосіб кодування символів.
- `tglp` — властивості аркушів і комірок; `format: 12` — BC4 (GX2, Cafe/NX).
- `glyphs[*]` — відображення символів на індекси гліфів та ширини:
  - `codepoint` — у вигляді `U+XXXX` (або довші для SMP), `char` — сам символ (може бути порожнім для службових кодів).
  - `index` — глобальний індекс гліфа у сітці всіх аркушів.
  - `sheet`, `grid_x`, `grid_y` — координати у відповідному аркуші.
  - `width.left/glyph/char` — метрики CWDH (байтові в оригіналі; тут як числа).
- `sheet_png` — імена PNG, згенерованих з аркушів.
- `file_b64` — сирий BFFNT у base64 (для біт‑ідентичного repack). Якщо задати `ignore_file_b64: true`, поле буде проігноровано.
- `png_ops` — прапори трансформацій, застосованих до PNG при розпаковці.

## Схема індексації гліфів на сітці

Нехай `rows = R`, `cols = C`, `per_sheet = R*C`.

```
index = sheet*per_sheet + (grid_y*rows + grid_x)

sheet_0:
  (0,0)->0,  (1,0)->1,  ... (R-1,0)->R-1
  (0,1)->R,  (1,1)->R+1, ...

sheet_1:
  (0,0)->per_sheet, (1,0)->per_sheet+1, ...
```

У редакторі: лівий верхній піксель комірки має координати `(1 + grid_x*(cell_width+1), 1 + grid_y*(cell_height+1))` — враховано 1‑піксельні відступи між комірками.

## Структура блоків CWDH/CMAP (байтові поля)

CWDH (Widths chain):

```
offset  size  field              notes
0x00    4     'CWDH'
0x04    u32   section_size
0x08    u16   start_index
0x0A    u16   end_index          count = end_index - start_index + 1
0x0C    u32   next_offset        якщо ≠0: абсолютний target + 8 у FFNT (у коді коригується як next-8)
0x10    ...   entries[count]:
               i8 left, u8 glyph, u8 char
```

CMAP (Codepoint map chain):

```
offset  size  field                   notes
0x00    4     'CMAP'
0x04    u32   section_size
0x08    u16/u32 code_begin            NX: u32, інакше u16
0x0A    u16/u32 code_end              NX: u32, інакше u16
0x0C    u16   mapping_method          0=Direct, 1=Table, 2=Scan
0x0E    u16   padding
0x10    u32   next_offset             якщо ≠0: див. примітку як у CWDH

// Direct (method=0):
0x14    u16   char_offset
        for cc in [code_begin..code_end]: idx = (cc - code_begin) + char_offset (якщо < 0xFFFF)

// Table (method=1):
0x14    ...   s16 idx[code_begin..code_end]

// Scan (method=2):
0x14    u16   count
0x16    u16   padding (тільки NX)
0x18    ...   entries[count]:
           NX:  u32 codepoint, s16 idx, u16 padding
           ін.: u16 codepoint, s16 idx
```

Примітки:
- Для FFNT офсети на наступний блок зберігаються як `target + 8` — у коді коригуються до фактичної позиції (`next-8`).
- Значення `idx = -1` у CMAP/Table/Scan означає «немає гліфа» для цього коду.
- Поля left/glyph/char у CWDH — байтові у вихідному файлі; у `font.json` записуються як числа.

## Скріншоти UI (приклади)

Скріншоти не зберігаються у репозиторії, щоб уникати великих бінарних файлів. Ви можете додати їх локально у `docs/images/` і посилатися так:

- Основне вікно редактора: `docs/images/viewer_main.png`
- Автопідбір ширин: `docs/images/auto_width.png`

Markdown‑посилання:

```
![BFFNT Viewer](../docs/images/viewer_main.png)
![Auto Width](../docs/images/auto_width.png)
```

Керування:
- Миша: ЛКМ — вибір комірки; СКМ — панорамування; колесо — масштаб.
- Клавіатура: Ctrl+Стрілки — навігація по комірках (працює з будь‑якого фокуса, wrap‑around); PageUp/PageDown — перемикання аркушів.

Поради:
- PNG із альфою: краще вмикати "Alpha only"; без альфи використовуйте поріг яскравості (eff = L*alpha).
- Adaptive + Quantile допомагає відсікати шум/фон при автопошуку ширини.
- `Auto pad` зменшує Char відносно Glyph: Char = max(0, Glyph − pad).

Відомі обмеження:
- Сітка/оверлеї не трансформуються разом із зображенням (за дизайном).
- Якщо PNG‑аркуш має інші формати, переглядач покаже лише композитоване зображення; автоширина спирається на альфу/яскравість.

Зберігання:
- Редактор змінює лише `font.json` у вибраній теці. PNG‑файли не змінюються.
- Налаштування зберігаються у QSettings під ключем `SwitchToolbox/BFFNTViewerQt`.

Встановлення залежностей (скорочено):

```
pip install -r tools/bffnt_py/requirements.txt
```

Запуск (скорочено):

```
python tools/bffnt_py/bffnt_viewer_qt.py
```
