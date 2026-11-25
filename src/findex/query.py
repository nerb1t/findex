"""Мова запитів: python AND (async OR await) NOT java "event loop"

Граматика (рекурсивний спуск, одна функція на одне правило):

    or_expr  := and_expr ("OR" and_expr)*
    and_expr := not_expr (["AND"] not_expr)*      # AND можна не писати
    not_expr := "NOT" not_expr | atom
    atom     := WORD | "фраза в лапках" | "(" or_expr ")"

AND сильніше за OR, тому "a OR b c" = Or(a, And(b, c)).
Оператори пишуться ВЕЛИКИМИ літерами, інакше це звичайне слово.
"""

import re
from dataclasses import dataclass

from findex.merge import ENGINES, Engine, merge_and
from findex.models import DocId, Index
from findex.tokenize import tokenize


class QueryError(ValueError):
    pass


# ---------- вузли дерева ----------
# frozen dataclass дає __eq__ (тести порівнюють дерева) і __hash__ (дерево
# можна використати як ключ в lru_cache).


@dataclass(frozen=True)
class Node:
    def __and__(self, other: "Node") -> "And":
        return And(self, other)

    def __or__(self, other: "Node") -> "Or":
        return Or(self, other)

    def __invert__(self) -> "Not":
        return Not(self)

    def evaluate(self, index: Index, engine: Engine = "merge") -> list[DocId]:
        """Відсортований список doc_id, які підходять під запит."""
        raise NotImplementedError

    def terms(self) -> tuple[str, ...]:
        """Терми для ранжування (те, що під NOT, не рахується)."""
        return ()


@dataclass(frozen=True)
class Term(Node):
    text: str

    def evaluate(self, index: Index, engine: Engine = "merge") -> list[DocId]:
        return list(index.doc_ids(self.text))

    def terms(self) -> tuple[str, ...]:
        return (self.text,)

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class Phrase(Node):
    words: tuple[str, ...]

    def evaluate(self, index: Index, engine: Engine = "merge") -> list[DocId]:
        if not index.positions:
            raise QueryError("phrase search needs an index built with --positions")
        and_ = ENGINES[engine][0]
        # 1) документи, де є всі слова
        ids: list[DocId] = list(index.doc_ids(self.words[0]))
        for w in self.words[1:]:
            ids = and_(ids, index.doc_ids(w))
        if not ids:
            return []
        # 2) для кожного слова: doc_id -> позиції
        by_doc = [{p.doc_id: p.positions for p in index[w]} for w in self.words]
        # 3) фраза є, якщо слово i стоїть на позиції p + i.
        # Зсуваємо позиції попереднього слова на +1 і робимо merge з наступним.
        result: list[DocId] = []
        for d in ids:
            cur = by_doc[0][d]
            for i in range(1, len(self.words)):
                cur = merge_and([p + 1 for p in cur], by_doc[i][d])
                if not cur:
                    break
            if cur:
                result.append(d)
        return result

    def terms(self) -> tuple[str, ...]:
        return self.words

    def __str__(self) -> str:
        return '"' + " ".join(self.words) + '"'


@dataclass(frozen=True)
class And(Node):
    left: Node
    right: Node

    def evaluate(self, index: Index, engine: Engine = "merge") -> list[DocId]:
        and_, _, not_ = ENGINES[engine]
        left = self.left.evaluate(index, engine)
        if isinstance(self.right, Not):
            # a AND NOT b: не будуємо доповнення до b (це майже всі документи)
            return not_(left, self.right.child.evaluate(index, engine))
        return and_(left, self.right.evaluate(index, engine))

    def terms(self) -> tuple[str, ...]:
        return self.left.terms() + self.right.terms()

    def __str__(self) -> str:
        return f"({self.left} AND {self.right})"


@dataclass(frozen=True)
class Or(Node):
    left: Node
    right: Node

    def evaluate(self, index: Index, engine: Engine = "merge") -> list[DocId]:
        or_ = ENGINES[engine][1]
        return or_(self.left.evaluate(index, engine), self.right.evaluate(index, engine))

    def terms(self) -> tuple[str, ...]:
        return self.left.terms() + self.right.terms()

    def __str__(self) -> str:
        return f"({self.left} OR {self.right})"


@dataclass(frozen=True)
class Not(Node):
    child: Node

    def evaluate(self, index: Index, engine: Engine = "merge") -> list[DocId]:
        not_ = ENGINES[engine][2]
        return not_(index.all_doc_ids(), self.child.evaluate(index, engine))

    def __str__(self) -> str:
        return f"NOT {self.child}"


# ---------- лексер ----------

LEX_RE = re.compile(r'"[^"]*"|\(|\)|[^\s()"]+')
OPERATORS = ("AND", "OR", "NOT", "(", ")")


type Token = str | Node  # оператор ("AND", "(", ...) або вже готовий вузол


def lex(query: str) -> list[Token]:
    """Рядок -> список, де оператори - рядки, а слова і фрази - вже вузли Term/Phrase."""
    tokens: list[Token] = []
    for raw in LEX_RE.findall(query):
        if raw in OPERATORS:
            tokens.append(raw)
        elif raw.startswith('"'):
            words = tuple(tokenize(raw.strip('"')))
            if len(words) == 1:
                tokens.append(Term(words[0]))
            elif words:
                tokens.append(Phrase(words))
        else:
            # одне "слово" може дати кілька токенів (mp3 -> mp, 3) або жодного (---)
            node: Node | None = None
            for w in tokenize(raw):
                node = Term(w) if node is None else node & Term(w)
            if node is not None:
                tokens.append(node)
    return tokens


# ---------- парсер ----------


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def next(self) -> Token | None:
        tok = self.peek()
        self.pos += 1
        return tok

    def parse(self) -> Node:
        if not self.tokens:
            raise QueryError("empty query")
        node = self.or_expr()
        if self.peek() is not None:
            raise QueryError(f"unexpected {self.peek()!r}")
        return node

    def or_expr(self) -> Node:
        node = self.and_expr()
        while self.peek() == "OR":
            self.next()
            node = node | self.and_expr()
        return node

    def and_expr(self) -> Node:
        node = self.not_expr()
        # поки не кінець, не OR і не ")" - це ще один операнд AND
        while self.peek() not in (None, "OR", ")"):
            if self.peek() == "AND":
                self.next()
            node = node & self.not_expr()
        return node

    def not_expr(self) -> Node:
        if self.peek() == "NOT":
            self.next()
            return ~self.not_expr()
        return self.atom()

    def atom(self) -> Node:
        tok = self.next()
        if tok == "(":
            node = self.or_expr()
            if self.next() != ")":
                raise QueryError("missing ')'")
            return node
        if isinstance(tok, Node):
            return tok
        raise QueryError(f"expected a word, got {tok!r}")


def parse(query: str) -> Node:
    return Parser(lex(query)).parse()
