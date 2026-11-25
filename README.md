# findex

Пошукова система, яку робимо протягом курсу Python. Кожна лаба додає щось нове, тег `lab-01`, `lab-02` і т.д.

## Lab 01 — ітератори, генератори, корпус

### Корпус

18 книг з Project Gutenberg (добірка з NLTK), приблизно 12 МБ. Одна книга = один документ.
Папка `data/` не в git (вона в `.gitignore`), щоб скачати корпус:

```
uv run python scripts/get_corpus.py
```

### Запуск

```
uv sync
uv run pytest
uv run python -m findex.stats data/
uv run python -m findex.stats data/ --limit 5
uv run python -m findex.stats data/ --eager
```

### Файли

- `src/findex/corpus.py` — `iter_documents(root)`, генератор, читає `.txt` файли по одному
- `src/findex/tokenize.py` — `tokenize(text)`, генератор токенів
- `src/findex/stats.py` — статистика (кількість документів, токенів, словник, топ-50, час, пам'ять)
- `tests/test_tokenize.py` — тести

### Правила токенізації

Спочатку `unicodedata.normalize("NFC")`, потім апострофи `’` і `ʼ` замінюються на `'`, потім `casefold()`.
Далі `re.finditer` з регуляркою `[^\W\d_]+(?:['-][^\W\d_]+)*|\d+`.

- слово — це літери (будь-які, кирилиця теж)
- **апострофи** всередині слова залишаються: `don't`, `п'ять`. На краях слова (лапки) — ні
- **дефіси** всередині слова залишаються: `well-known`. `--` це розділювач
- **цифри** — окремий токен, від літер відділяються: `mp3` → `mp`, `3`; `3.14` → `3`, `14`
- все інше (пунктуація, `_`) — розділювачі
- `casefold` замість `lower`, бо `Straße` → `strasse`
- NFC потрібен, бо `é` може бути одним символом або `e` + знак наголосу, без NFC це різні токени

### Eager vs lazy

Eager версія спеціально все тримає в списках: спочатку всі документи в список, потім всі токени в список списків, потім рахує.
Lazy — через генератори.

Весь корпус:

| Version | Documents | Peak memory | Elapsed |
|---|---|---|---|
| eager (lists) | 18 | 122.7 MB | 3.63 s |
| lazy (generators) | 18 | 9.8 MB | 3.73 s |

`--limit 5`:

| Version | Documents | Peak memory | Elapsed |
|---|---|---|---|
| eager (lists) | 5 | 70.5 MB | 2.09 s |
| lazy (generators) | 5 | 9.8 MB | 2.04 s |

В eager версії в пам'яті одночасно лежить текст усіх книг і список з усіх 2.1 млн токенів, а кожен токен це окремий об'єкт `str` (навіть `the` займає ~50 байт) плюс посилання в списку. Тому пам'ять росте разом з корпусом (5 книг — 70 МБ, 18 — 123 МБ).
В lazy версії в кожен момент є тільки один документ і `Counter`, токен рахується і одразу зникає. Тому пам'ять однакова і для 5, і для 18 книг. Нулем вона не буде, бо найбільша книга (Біблія, ~4 МБ) повністю читається в пам'ять разом з копіями після `normalize` і `casefold`, і ще `Counter` на ~48 тисяч слів.

### Проблеми

- У двох книгах (`chesterton-ball.txt`, `shakespeare-caesar.txt`) є биті байти і `read_text` падав з `UnicodeDecodeError`. Зробив `try/except` — якщо помилка, пише warning і читає ще раз з `errors="replace"`.
- `--limit` спочатку хотів зробити як `docs[:5]`, але генератор не можна різати, `TypeError`. Тому `itertools.islice`.
- В eager версії спочатку зробив `list(iter_documents(root))[:limit]`, і з `--limit 5` вона все одно читала всі 18 книг, тобто порівняння було нечесне. Переробив на `list(islice(...))`.

## Lab 02 — інвертований індекс: словники, хешування, пам'ять

### Що зроблено

- `src/findex/models.py` — `Posting`, `DocMeta` (`@dataclass(frozen=True, slots=True)`, хешовані, можна класти в `set`), `PlainPosting` (без slots, тільки для порівняння) і `Index`
- `src/findex/index.py` — `build_index()` і CLI `python -m findex.index`
- `src/findex/search.py` — булевий пошук (AND / OR / NOT), merge двома вказівниками і варіант через `set`, CLI `python -m findex.search`
- `src/findex/store.py` — `save` / `load` у двох форматах: pickle (`.bin`) і json (`.json`)
- `scripts/memory_study.py` — таблиця M4, `scripts/bench_search.py` — merge vs set, `scripts/experiments.py` — експерименти з REPL
- `tests/test_index.py` — тести (побудова, сортування, хешованість, merge == set, пошук, save/load для всіх варіантів)

Класи лежать в окремому `models.py`, а не в `index.py`: при запуску `python -m findex.index` модуль стає `__main__`, і pickle записав би клас як `__main__.Posting`, який потім не знайдеться з `findex.search`.

### Документи = абзаци

18 книг — це лише 18 документів, і найдовший список postings мав би довжину 18, тоді порівнювати merge і set або пам'ять немає сенсу. Тому за замовчуванням кожен **абзац** (текст між порожніми рядками) — окремий документ (`--unit para`). `--unit book` залишає по книзі на документ.

Корпус: 47 887 документів, 48 249 термів, 1 552 841 postings.

### Запуск

```
uv run python -m pytest
uv run python -m findex.index data/ --out index.bin
uv run python -m findex.index data/ --out index.json
uv run python -m findex.index data/ --out index_array.bin --kind array
uv run python -m findex.index data/ --out index_pos.bin --positions
uv run python -m findex.search index.bin "whale ship NOT sea"
uv run python -m findex.search index.bin "elizabeth OR emma NOT darcy" --engine set
uv run python scripts/memory_study.py data/
uv run python scripts/bench_search.py index.bin
uv run python scripts/experiments.py
```

`uv run pytest` у мене на Windows падає з `uv trampoline failed to canonicalize script path`, тому запускаю через `python -m pytest`.

### Як будується індекс

Один прохід: документи йдуть генератором (як в Lab 1), для кожного рахується `Counter(tokenize(text))`, і коли документ закінчився — кожен `(term, tf)` додається в `postings[term]` (`defaultdict(list)`). Оскільки `doc_id` тільки ростуть, кожен список postings вже відсортований, `sort()` не треба. Росте тільки індекс, тексти не накопичуються.

- `postings: dict[str, list[Posting]]`
- `doc_lengths: dict[int, int]` — кількість токенів у документі (для BM25 в Lab 3)
- `doc_meta: dict[int, DocMeta]` — шлях і заголовок (`назва_книги #номер_абзацу: перший рядок`)

З `--positions` у кожному `Posting` ще є `positions: tuple[int, ...]` — номери токенів у документі.

### Запити

Зліва направо, без пріоритетів: `a b` = a AND b, `a OR b c` = (a OR b) AND c, `a NOT b` = a AND NOT b, `NOT a` = всі документи крім a. Оператори пишуться великими літерами, слова проходять через той самий `tokenize`.

Приклад:

```
> uv run python -m findex.search index.bin "whale ship NOT sea" --limit 3
found: 63 documents
  [40253] melville-moby_dick.txt #38: "While the whale is floating at the stern of the ship, they
  [40256] melville-moby_dick.txt #41: "We set sail from the Elbe, wind N.E. in the ship called The
  [40285] melville-moby_dick.txt #70: "My God!  Mr. Chace, what is the matter?"  I answered, "we h
  ... and 60 more
load: 10.92 s, search (set): 0.27 ms
peak memory: 381.3 MB
```

`load` тут довгий, бо CLI міряє пам'ять через `tracemalloc`, а він сповільнює створення мільйонів об'єктів приблизно в 3 рази. Чисті часи завантаження — в таблиці нижче. Той самий запит з `index_array.bin`: load 0.56 s, peak 78.4 MB.

### Merge vs set

Комп'ютер: Intel i7-11700K, Windows 10, Python 3.13. `timeit`, найкращий з 5 замірів, тільки сам алгоритм (списки doc_id вже витягнуті).

| Випадок | Терми (df) | AND merge | AND set | OR merge | OR set |
|---|---|---:|---:|---:|---:|
| 2 найчастіші | `the` (35572), `and` (33096) | 4.27 ms | 4.10 ms | 4.73 ms | 4.23 ms |
| 2 найрідші | `1816` (1), `twenty-one` (1) | 0.1 µs | 0.3 µs | 0.3 µs | 0.3 µs |
| частий + рідкий (на початку) | `the` (35572), `1816` (1) | 0.2 µs | 864.8 µs | 130.2 µs | 1.97 ms |
| частий + рідкий (в кінці) | `the` (35572), `filter'd` (1) | 2.79 ms | 873.1 µs | 3.37 ms | 1.99 ms |

- Обидва способи O(len(a) + len(b)): merge проходить обидва списки, set будує дві хеш-таблиці (+ `sorted` результату).
- **Два великих списки** — майже нічия, set трохи швидший. Merge — це цикл `while` на Python, кожен крок інтерпретується, а `set()` будується в C. Перевагу C частково з'їдає те, що set хешує кожне число і потім сортує результат.
- **Два рідкісних** — все за долі мікросекунди, merge трохи швидший (немає витрат на створення set).
- **Частий + рідкий** — найцікавіше. Merge AND зупиняється, коли короткий список закінчився: якщо рідкий doc_id на початку — 0.2 µs проти 865 µs у set (set все одно хешує всі 35 тисяч чисел). Але якщо рідкий doc_id в кінці, merge мусить пройти майже весь довгий список по одному кроку на Python, і тоді set у 3 рази швидший. Справжні двигуни для цього роблять skip pointers / galloping (стрибати по довгому списку), і тоді merge виграє.
- OR завжди мусить видати всі елементи, тому там set (на C) швидший.

### Memory study (M4)

`uv run python scripts/memory_study.py data/`, той самий корпус (1 552 841 postings). Peak і "в RAM" — через `tracemalloc` (current після побудови = скільки займає готовий індекс). Load — без `tracemalloc`.

| Postings representation | Peak memory (build) | Index in RAM | Build | pickle: size | pickle: save | pickle: load | json: size | json: save | json: load |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `list[Posting]`, plain `@dataclass` | 178.1 MB | 176.2 MB | 7.98 s | 38.2 MB | 1.72 s | 1.09 s | 21.3 MB | 4.96 s | 1.73 s |
| `list[Posting]`, `slots=True` | 118.8 MB | 117.0 MB | 8.12 s | 29.3 MB | 5.84 s | 3.18 s | 21.3 MB | 5.36 s | 1.78 s |
| `array('I')` пари (doc_ids, tfs) | **43.0 MB** | **41.2 MB** | 4.92 s | 18.9 MB | 0.25 s | **0.15 s** | 18.6 MB | 1.09 s | 0.40 s |
| `slots=True` + positions | 197.7 MB | 195.9 MB | 11.11 s | 35.1 MB | 6.72 s | 3.68 s | 30.5 MB | 9.97 s | 2.52 s |

**Куди йдуть байти.** Спільна частина для всіх рядків — словник на 48 тисяч термів (рядок-ключ ~50–60 байт + місце в хеш-таблиці) і `doc_meta`/`doc_lengths` на 48 тисяч документів (об'єкт `DocMeta`, рядок заголовка ~100 байт, записи в двох dict) — разом приблизно 15–20 MB (оцінка). Решта — самі postings. Самі числа `doc_id` і `tf` майже нічого не коштують: `tf` майже завжди < 256 (маленькі int у Python кешовані, один об'єкт на всіх), а `doc_id` — один об'єкт int на документ, спільний для всіх його термів. Тобто платимо за обгортки:

- **plain dataclass**: (176 − ~18) / 1.55 млн ≈ **100 байт на posting**: 8 байт вказівник у списку + запас списку при рості, і сам об'єкт. `sys.getsizeof` показує лише 48, але це не все — в Python 3.13 атрибути без slots зберігаються в окремому масиві "inline values" (з запасом під ключі класу), і він не входить у `getsizeof`. Якщо звернутися до `obj.__dict__`, Python створить справжній dict на 296 байт — у старих версіях так було в кожного об'єкта.
- **slots**: ≈ **63 байти на posting**: об'єкт 56 байт (заголовок + 3 слоти по 8 байт + службове для gc) + 8 байт вказівник у списку. Тому slots в 1.5 рази менше. В теорії лаби написано 2–3×, але це для старих версій Python; з 3.11+ звичайні об'єкти вже компактніші, тому різниця менша. Те саме видно в чистому експерименті (1 млн об'єктів з 3 полями): 130 MB проти 92 MB, 1.41×.
- **array('I')**: ≈ **15 байт на posting**: 4 байти doc_id + 4 байти tf, плюс запас масиву при рості і по два об'єкти `array` (~80 байт кожен) + tuple на кожен з 48 тисяч термів. Жодного Python-об'єкта на posting. Без спільної частини це в **~6.5× менше**, ніж plain (≈100 → ≈15 байт), а весь індекс — у 4.3× менше.
- **positions**: tuple з номерами токенів на кожен posting (заголовок tuple + 8 байт на кожну позицію + int-об'єкти для позицій > 256) — індекс росте з 117 до 196 MB, в 1.7×; pickle-файл 29.3 → 35.1 MB, json 21.3 → 30.5 MB. Поки позиції не використовуються (фразовий пошук — Lab 3), тому за замовчуванням вимкнено (`--positions`).

**Файли.** Pickle plain більший за slots (38 vs 29 MB): для звичайного об'єкта зберігається dict атрибутів, а для slots — тільки значення. Але slots-pickle вантажиться втричі довше: для `frozen + slots` dataclass генерує `__setstate__` на Python, який викликається 1.5 млн разів, а plain просто оновлює `__dict__` на C. Array — це просто байти, pickle пише буфер як є, тому 0.15 s. JSON однаковий для plain і slots (там тільки `[doc_id, tf]`), вантажиться швидше за pickle-slots, але зберігається повільно.

### Безпека pickle

`pickle.load` на чужому файлі — це виконання чужого коду. Pickle — не формат даних, а маленька програма: об'єкт може через `__reduce__` сказати "щоб мене відновити, виклич `os.system('...')`", і `pickle.load` це виконає. Тому pickle використовується тільки для нашого власного індексу, який ми самі збудували. Файл, скачаний з інтернету чи отриманий по мережі — тільки json (або інший формат, де є лише дані). Це також написано в коментарі в `store.py`.

### Експерименти (`scripts/experiments.py`)

- `sys.getsizeof(dict)` стрибає після 1, 6, 11, 22, 43 вставок (64 → 224 → 352 → 632 → 1168 → 2264 байт) — це resize, коли таблиця заповнена на ~2/3.
- Клас з `__eq__` без `__hash__` → `TypeError: unhashable type: 'Bad'`. З `__hash__`, що повертає 42: 10 000 перевірок `in` — 2.636 s, з нормальним `hash(self.x)` — 0.002 s (в ~1300 разів повільніше, бо всі ключі колізують і `in` стає O(n)).
- `hash("hello")` у двох процесах: `5833023210499059324` і `-5983259463751001900`. З `PYTHONHASHSEED=0` обидва рази `-2096571579003691106`. Тому хеші рядків не можна зберігати у файл.
- 1 млн об'єктів з 3 полями: 130.1 MB без slots, 92.0 MB зі slots (1.41×).
- `array('I', range(10**6))` — 3.9 MB, `list(range(10**6))` — `getsizeof` показує 7.6 MB, але це тільки масив вказівників. Разом з самими int-об'єктами (по 28 байт) — 38.1 MB.

### Проблеми

- `defaultdict(lambda: ...)` не можна запікліти (lambda не серіалізується), тому в кінці `build_index` перетворюю на звичайний `dict`. Заодно пошук неіснуючого терма не додає порожні записи.
- Перший замір load у `memory_study.py` давав 6.3 s для slots, а окремо — 3 s. Виявилося, що старий індекс ще був у пам'яті, і gc під час завантаження щоразу обходив і його мільйони об'єктів. Тепер індекс видаляється перед замірами load.
- `tracemalloc` сповільнює `pickle.load` приблизно в 3 рази (3.2 s → 10.9 s), тому в таблиці load міряється без нього.
- `¶` у заголовках у консолі Windows виводився кракозябрами, замінив на `#`.
- Перший варіант бенчмарку брав рідкий терм `1816` з doc_id 0, і merge виглядав у 4000 разів швидшим. Додав випадок, де рідкий терм в кінці, — там уже виграє set.
- Stretch (mmap-формат, gap encoding) не робив.
