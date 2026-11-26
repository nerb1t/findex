# Lab 8, M1: профілювання (до будь-яких змін)

Машина: i7-11700K, Windows 10, Python 3.13 (GIL). Корпус: 18 книг, 47 887 документів, 1.55 млн postings.

Flame graphs (py-spy, 200 вибірок/с): [`flame-index.svg`](flame-index.svg) — `findex index data/gutenberg --executor serial`, [`flame-search.svg`](flame-search.svg) — завантаження `index_pos.bin` + 200 запитів `the white whale` (3 терми).

```
uv run python -m cProfile -o index.prof -m findex index data/gutenberg -o ix.bin --executor serial
uv run py-spy record -s -r 200 -o docs/flame-index.svg -- .venv/Scripts/python.exe -m findex index data/gutenberg ...
```

(`-s`: на Windows `.venv/Scripts/python.exe` — це лаунчер uv, справжній інтерпретатор запускається як дочірній процес, і без `--subprocesses` py-spy пише «Failed to find python version».)

## `findex index` (cProfile, 8.9 s під профайлером)

| # | Місце | tottime | cumtime | Гіпотеза |
|---|---|---:|---:|---|
| 1 | `tokenize()` + `Counter` (`_count_elements`) | 1.19 s | 2.1 s | Генератор віддає 2.2 млн токенів по одному: на кожен — `m.group()`, новий `str`, `yield`, а `Counter` хешує його. Сама регулярка (`finditer`) — нативний код, а ціна — у Python-обгортці на кожен токен. Перевірити: `TOKEN_RE.findall(text)` одним викликом |
| 2 | `merge` → генератор `Posting(d + offset, tf)` | 0.62 s | 1.42 s | 1.78 млн викликів `__init__` frozen dataclass — чистий Python (Lab 5: це і є межа Амдала). Масиви NumPy замість об'єктів `Posting` прибирають цей крок повністю |
| 3 | `Index.__getstate__` (pickle) | 0.97 s | 0.97 s | Перепакування 1.5 млн `Posting` назад в `array('I')` списковими виразами (Lab 7). Якщо індекс одразу зберігає NumPy-масиви, пакувати нема чого: `np.save`/буфер |

## `search`, 200 запитів `the white whale` (cProfile, 2.6 s)

| # | Місце | tottime | cumtime | Гіпотеза |
|---|---|---:|---:|---|
| 1 | `Index.__setstate__` (завантаження) | 1.33 s | 2.34 s | Для одного CLI-запиту це 90% часу: збираємо 1.5 млн об'єктів `Posting` з масивів. NumPy-постинги вантажаться як суцільні буфери без жодного об'єкта на posting |
| 2 | `Searcher.search` — цикл по postings | 0.27 s | 0.49 s (2.4 ms/запит) | Для кожного терма проходимо всі його postings (`the` — 35 572) і перевіряємо `p.doc_id in scores`, хоча документів у результаті лише 38. Python-цикл по об'єктах: кандидат на векторизацію (`np.isin` / маска + `scores[ids] += ...`) |
| 3 | `BM25.__call__` (64 800 викликів) | 0.05 s | 0.17 s | Окремий виклик на кожен posting, і щоразу заново рахується `idf` (`df` → `len`). Масивний вираз рахує `idf` один раз на терм і всі бали разом |

Окремо: `logging.isEnabledFor` — 0.15 s на 402 виклики (`log.info` у `search` і `log.debug` у `@timed`). Під профайлером це завищено, але логування на гарячому шляху варто перевірити.

## Ще не зроблено

- Розподіл «час у Python / нативний час» через Scalene (на Windows Scalene працює обмежено) — наступний крок.

## pytest-benchmark (база до M2)

`tests/test_bench.py`: синтетичний корпус (2 "книги" по 1500 абзаців, слова за законом Ципфа), `build_index` з позиціями і `search` з BM25. У звичайному `pytest` бенчмарки вимкнені (`--benchmark-disable`: один виклик, лише як тест). В CI окремий крок їх вмикає і падає, якщо медіана перевищила щедру стелю (`build_index` < 1.5 s, `search` < 20 ms): так видно грубу регресію, а не шум між машинами.

```
uv run python -m pytest tests/test_bench.py --benchmark-enable --benchmark-only
```

| Бенчмарк | min, ms | median, ms | max, ms | rounds |
|---|---:|---:|---:|---:|
| `search("the")` | 2.67 | 2.94 | 3.80 | 305 |
| `search("the white whale")` | 4.57 | 5.02 | 8.30 | 160 |
| `build_index(positions=True)`, 3000 документів | 223.8 | 235.0 | 239.2 | 5 |

# M2: NumPy-скорер

`src/findex/vector.py`. Постинги всього індексу лежать трьома суцільними масивами: `ids: int32[P]`, `tfs: int32[P]` (терм за термом) і `span: term -> (start, end)`. Довжини документів — один `int32[N]`. Будуються один раз з `Index` (304 мс на повному корпусі, у веб-сервісі — у потоці під час старту). Для кожного терма запиту бал **усіх** його документів рахується одним виразом:

```python
norm = 1 - b + b * doc_len[ids] / avg_len                   # fancy indexing
part = idf * tf * (k1 + 1) / (tf + k1 * norm)               # ufunc-и над масивом
scores[pos] += part                                          # scatter-add
```

Топ-k — `np.argpartition` (O(n)), а сортуємо лише k переможців (`np.lexsort`: бал за спаданням, при рівних — менший doc_id, як `heapq.nlargest` у Lab 3).

**Той самий рейтинг.** `tests/test_vector.py` порівнює NumPy і цикл Lab 3 на 10 запитах (AND, OR, NOT, фраза, неіснуюче слово) × 3 скорери × k ∈ {1, 5, 10, 1000}: однакові і `doc_id`, і бали — **точно**, без `approx`. Порядок арифметичних дій той самий, що в `scoring.py`, тож float-и збігаються до біта. Будь-який інший `Scorer` (не `BM25`/`TfIdf`) іде старим циклом.

**Таблиця** (`uv run python scripts/bench_numpy.py index_pos.bin`, BM25, k=10, медіана з 20–200 запусків; булевий фільтр закешований в обох):

| Query | | Docs matched | Postings scanned | Lab 3 scorer, ms | NumPy scorer, ms | Speedup |
|---|---|---:|---:|---:|---:|---:|
| common term | `the` | 35 572 | 35 572 | 31.598 | 2.023 | **15.6×** |
| rare term | `leviathan` | 81 | 81 | 0.085 | 0.053 | 1.6× |
| very rare term | `1816` | 1 | 1 | 0.007 | 0.033 | **0.2×** |
| 3-term query | `the white whale` | 108 | 36 750 | 1.329 | 0.395 | 3.4× |
| 3-term OR | `sea OR ship OR whale` | 1 732 | 2 027 | 1.828 | 0.173 | 10.6× |

**Чому так.**

- **Частий терм — 15.6×.** У Lab 3 на кожен з 35 572 postings — ітерація циклу, `p.doc_id in scores`, виклик `BM25.__call__`, який заново рахує `idf`, і новий `float`-об'єкт. У NumPy це кілька проходів по суцільних масивах у C без жодного Python-об'єкта на posting.
- **Рідкісний терм — 1.6×, дуже рідкісний — навіть 0.2× (повільніше).** Для 1–81 postings корисної роботи майже немає, і час з'їдають накладні витрати: кожен виклик NumPy (`np.zeros`, `searchsorted`, `astype`, індексація) коштує мікросекунди на створення масиву й перевірку типів. Цикл Lab 3 на одному posting — 7 мкс, NumPy — 33 мкс. Векторизація окупається тоді, коли на виклик припадає багато елементів.
- **3 терми AND — лише 3.4×.** Документів у відповіді 108, але `the` дає 36 тисяч postings. Lab 3 цикл по ним дешевий (лише `in scores`, скорер викликається 324 рази), тож виграш скромніший. Перша версія NumPy рахувала бали для всіх 47 887 документів і давала лише 1.3×. Тепер для нечисленних кандидатів масив балів має довжину `len(cands)`, а зайві postings відкидає `np.searchsorted` (двійковий пошук у C). Для частих — навпаки, щільний масив на весь корпус і `scores[cands]` у кінці (поріг: кандидатів > 1/8 корпусу).
- **OR з трьох термів — 10.6×.** 2 027 postings і всі вони в результаті: та сама картина, що з частим термом, тільки менша.

**pytest-benchmark до і після M2** (той самий синтетичний корпус, медіана):

| Бенчмарк | до (цикл Lab 3) | після (NumPy) | |
|---|---:|---:|---:|
| `search("the")` | 2.94 ms | 0.142 ms | 20.7× |
| `search("the white whale")` | 5.02 ms | 0.210 ms | 23.9× |
| `build_index` | 235 ms | 203 ms | не змінювали (різниця — шум) |
