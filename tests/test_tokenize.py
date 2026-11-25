import inspect

from findex.corpus import iter_documents
from findex.tokenize import tokenize


def toks(text):
    return list(tokenize(text))


def test_case():
    assert toks("Hello WORLD hello") == ["hello", "world", "hello"]


def test_casefold():
    assert toks("Straße") == ["strasse"]


def test_cyrillic():
    assert toks("Привіт, Світе!") == ["привіт", "світе"]


def test_combining_accent():
    assert toks("café") == toks("café")


def test_punctuation():
    assert toks("one,two...three!") == ["one", "two", "three"]


def test_empty():
    assert toks("") == []


def test_apostrophe():
    assert toks("don't п'ять п’ять") == ["don't", "п'ять", "п'ять"]


def test_hyphen():
    assert toks("well-known -- a") == ["well-known", "a"]


def test_digits():
    assert toks("mp3 in 2024") == ["mp", "3", "in", "2024"]


def test_generators():
    assert inspect.isgeneratorfunction(tokenize)
    assert inspect.isgeneratorfunction(iter_documents)
