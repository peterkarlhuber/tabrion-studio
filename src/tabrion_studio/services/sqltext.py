"""Named parameters in user SQL (03 section 2 C, STU-011): ``:name`` placeholders and what the driver gets.

The SQL editor sends a statement with ``:name`` placeholders and a value per name. SQLAlchemy's ``text()`` would read
``:name`` inside a string literal as a bind parameter as well (I-2 of ``stu-009-review.md``), so Studio never sends
user SQL through ``text()``. It scans the statement itself and hands the driver's own cursor a text in the driver's
parameter style:

* **PostgreSQL (psycopg):** each ``:name`` outside literals, quoted identifiers and comments becomes ``%(name)s``, and
  every ``%`` of the text becomes ``%%``. psycopg sends the values separately (a prepared statement; no value is
  ever put into the text). A cast (``::type``) is not a placeholder.
* **SQLite (sqlite3):** the text is sent unchanged; sqlite3 reads ``:name`` itself and skips literals and comments by
  SQLite's own rules. The scan only finds the names, so that a missing value is Studio's error.

The scan follows PostgreSQL's lexical rules with ``standard_conforming_strings = on``: ``'...'`` (``''`` inside),
``E'...'`` with backslash escapes, ``"..."``, ``$tag$...$tag$``, ``--`` and nested ``/* */`` comments; for SQLite
also ``[...]`` and backtick identifiers. Where the scan and the server disagree (a server with
``standard_conforming_strings = off``), the worst outcome is a placeholder the server does not expect, which it
refuses: values are never part of the text. ``a[1:n]`` (an array slice) reads ``:n`` as a placeholder; write
``a[1 : n]``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Literal

__all__ = [
    "MAX_PARAMETERS",
    "MAX_VALUE_CHARS",
    "BoundStatement",
    "Dialect",
    "ParameterError",
    "bind",
    "driver_text",
    "is_bound",
    "placeholders",
]

Dialect = Literal["postgresql", "sqlite"]

MAX_PARAMETERS = 100
MAX_VALUE_CHARS = 1_000_000
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_\u0080-\U0010ffff][A-Za-z0-9_\u0080-\U0010ffff]*)?\$")


class ParameterError(Exception):
    """Parameters that do not fit the statement; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BoundStatement:
    """A checked statement and what the driver's cursor gets for it."""

    sql: str  # the statement as the user wrote it (after check_statement)
    driver_sql: str
    values: dict[str, Any]


def _is_word_start(char: str) -> bool:
    return char == "_" or char.isalpha() or not char.isascii()


def _is_word_char(char: str) -> bool:
    return _is_word_start(char) or char.isdigit() or char == "$"


def _quoted_end(sql: str, start: int, quote: str, *, backslash: bool = False) -> int:
    """The index after the literal or identifier opened at ``start`` (doubled quotes inside; the end if open)."""
    index, size = start + 1, len(sql)
    while index < size:
        char = sql[index]
        if backslash and char == "\\":
            index += 2
        elif char == quote:
            if sql.startswith(quote * 2, index):
                index += 2
            else:
                return index + 1
        else:
            index += 1
    return size


def _comment_end(sql: str, start: int) -> int:
    if sql.startswith("--", start):
        ends = [position for position in (sql.find("\n", start), sql.find("\r", start)) if position >= 0]
        return min(ends) if ends else len(sql)
    depth, index, size = 1, start + 2, len(sql)
    while index < size and depth:
        if sql.startswith("/*", index):
            depth, index = depth + 1, index + 2
        elif sql.startswith("*/", index):
            depth, index = depth - 1, index + 2
        else:
            index += 1
    return index


def _scan(sql: str, dialect: Dialect) -> list[tuple[int, int, str]]:
    """``(start, end, name)`` of every ``:name`` placeholder outside literals, identifiers and comments."""
    found: list[tuple[int, int, str]] = []
    index, size = 0, len(sql)
    while index < size:
        char = sql[index]
        if sql.startswith(("--", "/*"), index):
            index = _comment_end(sql, index)
        elif char in "'\"":
            index = _quoted_end(sql, index, char)
        elif dialect == "sqlite" and char == "`":
            index = _quoted_end(sql, index, "`")
        elif dialect == "sqlite" and char == "[":
            close = sql.find("]", index + 1)
            index = size if close < 0 else close + 1
        elif dialect == "postgresql" and char == "$" and (match := _DOLLAR_TAG.match(sql, index)) is not None:
            close = sql.find(match.group(), match.end())
            index = size if close < 0 else close + len(match.group())
        elif char == ":":
            if sql.startswith("::", index):
                index += 2
                continue
            match = _NAME.match(sql, index + 1)
            if match is not None and (match.end() == size or not _is_word_char(sql[match.end()])):
                found.append((index, match.end(), match.group()))
                index = match.end()
            else:
                index += 1
        elif _is_word_start(char):
            end = index + 1
            while end < size and _is_word_char(sql[end]):
                end += 1
            if dialect == "postgresql" and sql[index:end] in ("e", "E") and sql.startswith("'", end):
                end = _quoted_end(sql, end, "'", backslash=True)
            index = end
        else:
            index += 1
    return found


def placeholders(sql: str, dialect: Dialect) -> list[str]:
    """The distinct placeholder names of ``sql``, in their order."""
    return list(dict.fromkeys(name for _, _, name in _scan(sql, dialect)))


def _value(value: object) -> Any:
    """A parameter value: text, number, boolean or null (03 section 2 C). JSON goes as text with a cast."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # JSON has neither, so no client sends them
            raise ParameterError("studio.query_params_invalid")
        return value
    if isinstance(value, str):
        if len(value) > MAX_VALUE_CHARS:
            raise ParameterError("studio.query_params_invalid")
        return value
    raise ParameterError("studio.query_params_invalid")


def driver_text(sql: str, dialect: Dialect) -> tuple[str, list[str]]:
    """The driver's text of ``sql`` and its distinct placeholder names (no values involved)."""
    spans = _scan(sql, dialect)
    names = list(dict.fromkeys(name for _, _, name in spans))
    if dialect == "sqlite":
        return sql, names
    parts: list[str] = []
    position = 0
    for start, end, name in spans:
        parts.append(sql[position:start].replace("%", "%%"))
        parts.append(f"%({name})s")
        position = end
    parts.append(sql[position:].replace("%", "%%"))
    return "".join(parts), names


def bind(sql: str, params: Mapping[str, object] | None, dialect: Dialect) -> BoundStatement:
    """The driver's text and values for ``sql`` (one checked statement) with ``params``.

    Every placeholder needs a value (``studio.query_param_missing``); a value is text, a number, a boolean or null
    (``studio.query_params_invalid``). Values without a placeholder are left out.
    """
    params = {} if params is None else params
    if len(params) > MAX_PARAMETERS or not all(isinstance(key, str) for key in params):
        raise ParameterError("studio.query_params_invalid")
    text, names = driver_text(sql, dialect)
    if len(names) > MAX_PARAMETERS:
        raise ParameterError("studio.query_params_invalid")
    if any(name not in params for name in names):
        raise ParameterError("studio.query_param_missing")
    return BoundStatement(sql, text, {name: _value(params[name]) for name in names})


def is_bound(statement: BoundStatement, checked: str, dialect: Dialect) -> bool:
    """Whether ``statement`` is what binding ``checked`` gives: its text, its driver text and one value per name.

    ``OpenConnection.stream`` uses it on the text it is about to execute (L-1 of ``stu-011-review.md``), so a
    ``BoundStatement`` built anywhere else cannot carry a driver text that was never checked.
    """
    text, names = driver_text(checked, dialect)
    return statement.sql == checked and statement.driver_sql == text and list(statement.values) == names
