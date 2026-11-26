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
- Самі оптимізації (M2).

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
