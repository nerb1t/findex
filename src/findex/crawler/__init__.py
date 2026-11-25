"""Асинхронний краулер (Lab 6).

USER_AGENT - чесний: хто ми і де про нас прочитати. Сайт може нас заблокувати
за ним у robots.txt або написати автору.
"""

from typing import Final

USER_AGENT: Final = (
    "findex-crawler/0.6 (student search-engine project; +https://github.com/nerb1t/findex)"
)
