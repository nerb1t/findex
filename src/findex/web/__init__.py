"""Веб: API + сторінка пошуку (Lab 7).

uvicorn --factory findex.web:create_app     (або findex serve)
"""

from findex.web.app import create_app

__all__ = ["create_app"]
