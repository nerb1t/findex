"""Lab 8: навантаження для Scalene - цикл Lab 3 проти NumPy на "the" (повний корпус).

scalene run --cpu-only --profile-all -o scal.json scripts/scalene_search.py index_pos.bin
"""

import logging
import sys
from pathlib import Path

from findex.scoring import BM25
from findex.search import Searcher
from findex.store import load

logging.disable(logging.CRITICAL)
ix = load(Path(sys.argv[1]))
slow = Searcher(ix, vectorized=False)
fast = Searcher(ix)
for _ in range(150):
    slow.search("the", BM25(), 10)
    fast.search("the", BM25(), 10)
