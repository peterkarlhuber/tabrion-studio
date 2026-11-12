"""Read-only guards that Studio adds on top of the database's own (03 section 5.1, STU-009, threats T-06 and T-13).

The hard boundary is in the database: SQLite opens the file with ``mode=ro`` and ``PRAGMA query_only=ON`` and allows
no attached database (tabrion ``Profile(read_only=True)``); PostgreSQL sessions have
``default_transaction_read_only=on`` and every Studio statement runs in ``Database.read()`` (``BEGIN READ ONLY``,
re-checked by tabrion before ``COMMIT``). The role's privileges stay the only limit for functions with side effects
(``dblink``, extensions), so production profiles should use a role without write privileges (03 section 5.1).

This module adds two guards:

* :func:`sqlite_authorizer`, an allowlist for ``sqlite3.Connection.set_authorizer``: reading, transactions, functions
  other than ``load_extension``, and pragmas that only read (or set ``query_only`` on). ``ATTACH``, ``DETACH`` (and
  so ``VACUUM INTO``, which attaches its target), every write, every schema change (also of temporary objects),
  ``ANALYZE`` and ``REINDEX`` are denied (STU-T-024).
* :func:`check_statement`, which allows one statement by its first word (an allowlist, M-1/M-2/I-2 of
  ``stu-011-review.md``: ``SELECT``, ``WITH``, ``VALUES``, ``TABLE``, ``EXPLAIN`` of a query, ``SHOW``, ``PRAGMA``)
  and refuses a text with more than one statement and every statement of the transaction-control classes
  (``BEGIN``, ``COMMIT``, ``SET TRANSACTION``, ``SET SESSION CHARACTERISTICS``, ``RESET``,
  ``SET default_transaction_read_only``, ...). PostgreSQL runs a multi-statement text in one round trip,
  so ``COMMIT; BEGIN READ WRITE; INSERT ...`` would end Studio's read-only transaction and write: the probe in the
  STU-009 tests shows it. With one statement per ``read()``, a write fails in the database. ``COPY``, ``LOAD``,
  ``NOTIFY``, ``LISTEN``, ``LOCK``, ``VACUUM``, ``CHECKPOINT`` and ``CLUSTER`` are refused too.

The text checks are a user-experience layer (03 section 5.1). The boundary is in the database and the protocol: every
PostgreSQL statement is a prepared statement (``prepare_threshold=0``, extended query protocol), so the server refuses
a text with several commands whatever the split thinks (H-1 of ``stu-009-review.md``: with
``standard_conforming_strings = off`` the split saw one statement where the server ran six); SQLite's ``execute``
refuses several statements itself. ``SET standard_conforming_strings`` and ``SET backslash_quote`` are refused too,
and so is a call of ``set_config``, ``pg_notify`` or a session-level advisory lock function anywhere in the statement:
the check reads the statement's tokens (comments and literals left out, quoted identifiers unquoted, both ways of
lexing ``'...'``), so ``"set_config"(`` and ``set_config/**/(`` are caught (R-L1 of the re-review). It remains a
best-effort check: a function of the database's own that calls ``set_config`` is not visible in the text, and only
the role's privileges limit what such functions do. ``DO`` blocks and the functions that run a query given as text
(``query_to_xml``, ``cursor_to_xml``, ``ts_stat``, ``ts_rewrite``, ``crosstab``, ``dblink``) are refused, since the
check cannot see inside their literal (R2-L1 of re-review 2). The query engine (STU-011) must send user SQL through
:func:`check_statement`, one ``read()`` per statement, and always through ``ConnectionRegistry.get`` (which checks the
handle again).
"""

from __future__ import annotations

import re
import sqlite3

from tabrion.sql import split_statements

__all__ = ["StatementRefused", "check_statement", "first_word", "sqlite_authorizer"]

_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_RECURSIVE,
    }
)

#: Pragmas that take an argument but only read (the argument names a table or an index, or a row limit).
_READING_PRAGMAS = frozenset(
    {
        "table_info",
        "table_xinfo",
        "table_list",
        "index_info",
        "index_xinfo",
        "index_list",
        "foreign_key_list",
        "foreign_key_check",
        "integrity_check",
        "quick_check",
    }
)

#: Pragmas that do something even without an argument.
_ACTING_PRAGMAS = frozenset({"optimize", "wal_checkpoint", "incremental_vacuum", "shrink_memory", "analysis_limit"})
_ON = frozenset({"on", "1", "true", "yes"})


def sqlite_authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
    """``SQLITE_OK`` for what a read-only Studio connection may do, ``SQLITE_DENY`` for everything else."""
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        # tabrion disables extension loading; denying the function as well keeps it off if that ever changes.
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() == "load_extension" else sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA:
        name = (arg1 or "").lower()
        if name in _ACTING_PRAGMAS:
            return sqlite3.SQLITE_DENY
        if arg2 is None or name in _READING_PRAGMAS:
            return sqlite3.SQLITE_OK
        # tabrion's read() sets query_only ON before each BEGIN; turning it off is denied.
        if name == "query_only" and arg2.strip().strip("'\"").lower() in _ON:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


class StatementRefused(Exception):
    """A statement Studio does not run in read-only mode; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


#: First words of the transaction-control classes (03 section 5.1) and of statements that change the session's role.
_CONTROL = frozenset(
    {"begin", "start", "commit", "end", "rollback", "abort", "savepoint", "release", "prepare", "reset", "discard"}
)
_SESSION_SETTINGS = frozenset(
    {
        "transaction_read_only",
        "default_transaction_read_only",
        "session_authorization",
        "authorization",
        "role",
        # These change how the server lexes string literals, so a text split and the server could disagree on
        # where a statement ends (H-1 of stu-009-review.md).
        "standard_conforming_strings",
        "backslash_quote",
    }
)
#: Functions Studio refuses wherever the text calls them. ``set_config()`` reaches every setting above from inside a
#: SELECT, where the SET check cannot see it; ``pg_notify()`` reaches other sessions like ``NOTIFY``; a session-level
#: advisory lock stays held on the pooled connection after the transaction (I-R1 of the re-review). The
#: transaction-level ``pg_advisory_xact_lock*`` functions end with Studio's transaction and stay allowed.
#: Functions that run a query given as text are refused too, since the check cannot see inside a literal (R2-L1 of
#: re-review 2): ``query_to_xml`` and ``cursor_to_xml`` with their schema forms, ``ts_stat`` and ``ts_rewrite``
#: (core), ``crosstab`` (tablefunc) and ``dblink`` (dblink).
_REFUSED_FUNCTIONS = frozenset(
    {
        "set_config",
        "pg_notify",
        "pg_advisory_lock",
        "pg_advisory_lock_shared",
        "pg_try_advisory_lock",
        "pg_try_advisory_lock_shared",
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        "cursor_to_xmlschema",
        "ts_stat",
        "ts_rewrite",
        "crosstab",
        "crosstab2",
        "crosstab3",
        "crosstab4",
        "dblink",
        "dblink_exec",
        "dblink_open",
        "dblink_fetch",
        "dblink_send_query",
    }
)
#: A run of white space (:data:`_SPACE`) and ``--`` comments; such a comment ends at ``\n`` or ``\r`` in PostgreSQL,
#: only at ``\n`` in SQLite.
#: Possessive, so the match never backtracks.
_BLANK = re.compile(r"[ \t\n\r\f\v]*+(?:--[^\n\r]*+[ \t\n\r\f\v]*+)*+")
_SQLITE_BLANK = re.compile(r"[ \t\n\r\f\v]*+(?:--[^\n]*+[ \t\n\r\f\v]*+)*+")
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_\u0080-\U0010ffff][A-Za-z0-9_\u0080-\U0010ffff]*)?\$")
#: A Unicode-escaped quoted identifier (``U&"\0073et_config"``): Studio does not decode them, so it refuses them.
_UNICODE_IDENTIFIER = "\x00u&"


def _is_word_start(char: str) -> bool:
    return char == "_" or char.isalpha() or not char.isascii()


def _is_word_char(char: str) -> bool:
    return _is_word_start(char) or char.isdigit() or char == "$"


def _string_end(sql: str, start: int, *, backslash: bool) -> int:
    """The index after the string literal whose opening quote is at ``start`` (the text's end if it is open)."""
    index, size = start + 1, len(sql)
    while index < size:
        char = sql[index]
        if backslash and char == "\\":
            index += 2
        elif char == "'":
            if sql.startswith("''", index):
                index += 2
            else:
                return index + 1
        else:
            index += 1
    return size


#: White space as both servers read it (PostgreSQL's ``space``, SQLite's ``CC_SPACE``); any other character, also a
#: non-ASCII space, is part of a token, so it cannot hide where the first word starts.
_SPACE = frozenset(" \t\n\r\f\v")
#: The ways to lex a text that the check reads (R-H1 of the delta re-review): PostgreSQL with and without backslash
#: escapes in ``'...'`` (``standard_conforming_strings``), and SQLite (``--`` ends only at ``\n``, block comments do
#: not nest, ``[...]`` and backticks quote identifiers, no dollar quoting). A text must give the same first word in
#: all of them, and every check holds for each.
_READINGS = ((False, False), (True, False), (False, True))


def _lex(sql: str, *, backslash_strings: bool, sqlite: bool = False) -> list[tuple[str, bool]]:
    """The tokens of ``sql`` (see :func:`_tokens`), each with whether it was a quoted identifier."""
    tokens: list[tuple[str, bool]] = []
    index, size = 0, len(sql)
    while index < size:
        char = sql[index]
        if char in _SPACE or sql.startswith("--", index):
            # White space and line comments in one match (R2-L1 of delta re-review 2: two finds per comment that
            # scanned to the end made the lexer quadratic).
            skipped = (_SQLITE_BLANK if sqlite else _BLANK).match(sql, index)
            index = max(skipped.end(), index + 1) if skipped is not None else index + 1
        elif sql.startswith("/*", index):
            if sqlite:
                close = sql.find("*/", index + 2)
                index = size if close < 0 else close + 2
                continue
            depth, index = 1, index + 2
            while index < size and depth:
                if sql.startswith("/*", index):
                    depth, index = depth + 1, index + 2
                elif sql.startswith("*/", index):
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
        elif char == "'":
            index = _string_end(sql, index, backslash=backslash_strings and not sqlite)
        elif char == '"' or (sqlite and char == "`"):
            end = index + 1
            parts = []
            while end < size:
                close = sql.find(char, end)
                if close < 0:
                    parts.append(sql[end:])
                    end = size
                    break
                parts.append(sql[end:close])
                if sql.startswith(char * 2, close):
                    parts.append(char)
                    end = close + 2
                else:
                    end = close + 1
                    break
            tokens.append(("".join(parts), True))
            index = end
        elif sqlite and char == "[":
            close = sql.find("]", index + 1)
            end = size if close < 0 else close + 1
            tokens.append((sql[index + 1 : close if close >= 0 else size], True))
            index = end
        elif not sqlite and char == "$" and (match := _DOLLAR_TAG.match(sql, index)) is not None:
            close = sql.find(match.group(), match.end())
            index = size if close < 0 else close + len(match.group())
        elif _is_word_start(char):
            end = index + 1
            while end < size and _is_word_char(sql[end]):
                end += 1
            word = sql[index:end].lower()
            if not sqlite and word == "e" and sql.startswith("'", end):
                index = _string_end(sql, end, backslash=True)
                continue
            if not sqlite and word == "u" and sql.startswith('&"', end):
                tokens.append((_UNICODE_IDENTIFIER, False))
            tokens.append((word, False))
            index = end
        else:
            tokens.append((char, False))
            index += 1
    return tokens


def _tokens(sql: str, *, backslash_strings: bool, sqlite: bool = False) -> list[str]:
    """The words and symbols of ``sql``, without white space, comments and literals.

    Unquoted words are lowercased; a quoted identifier gives its exact content (``"set_config"`` -> ``set_config``).
    ``backslash_strings`` reads ``'...'`` the way the server does with ``standard_conforming_strings = off``;
    ``E'...'`` always takes backslash escapes. ``sqlite`` lexes comments and quotes the way SQLite does. Linear in the
    length of ``sql``.
    """
    return [token for token, _ in _lex(sql, backslash_strings=backslash_strings, sqlite=sqlite)]


def _leading_words(sql: str, *, backslash_strings: bool, sqlite: bool) -> list[str]:
    """The first words of ``sql`` after leading ``(``, from the lexer (R-H1); a word that is a quoted identifier or a
    symbol ends them, and ``[]`` means the text does not start with a word."""
    tokens = _lex(sql, backslash_strings=backslash_strings, sqlite=sqlite)
    index = 0
    while index < len(tokens) and tokens[index] == ("(", False):
        index += 1
    words: list[str] = []
    for token, quoted in tokens[index : index + 4]:
        if quoted or not _is_word_start(token[:1] or "0"):
            break
        words.append(token)
    return words


def first_word(sql: str) -> str:
    """The first word of ``sql`` as every reading of :data:`_READINGS` sees it, ``""`` if they disagree or there is
    none. ``connections`` uses it to choose the cursor; :func:`check_statement` refuses a text without one."""
    seen = {
        tuple(_leading_words(sql, backslash_strings=backslash, sqlite=sqlite)[:1]) for backslash, sqlite in _READINGS
    }
    return seen.pop()[0] if len(seen) == 1 and () not in seen else ""


def _calls_refused_function(sql: str) -> bool:
    """Whether ``sql`` calls a function of :data:`_REFUSED_FUNCTIONS` (or holds a Unicode-escaped identifier).

    The text is read twice, with and without backslash escapes in ``'...'``, so a literal cannot hide a call from
    whichever way the server lexes it (R-L1 of the re-review: ``"set_config"(`` and ``set_config/**/(``).
    """
    for backslash_strings in (False, True):  # PostgreSQL's functions, so PostgreSQL's readings
        tokens = _tokens(sql, backslash_strings=backslash_strings)
        if _UNICODE_IDENTIFIER in tokens:
            return True
        for position, token in enumerate(tokens[:-1]):
            if token in _REFUSED_FUNCTIONS and tokens[position + 1] == "(":
                return True
    return False


#: Statements with an effect outside the transaction's data, or none a read-only view needs (``COPY ... TO`` writes a
#: server file under ``pg_write_server_files``; ``LOAD`` loads a library; ``NOTIFY`` reaches other sessions). ``DO``
#: runs a code block the check cannot see into (R2-L1 of re-review 2), and a read-only view does not need it.
_OTHER_EFFECTS = frozenset(
    {"copy", "load", "notify", "listen", "unlisten", "lock", "vacuum", "checkpoint", "cluster", "do"}
)


#: The first words a statement may have (M-1, M-2 and I-2 of ``stu-011-review.md``): an allowlist, since a read-only
#: editor needs only queries, their plans, ``SHOW`` and SQLite's pragmas. Everything else is refused before it reaches
#: the database, also what the database itself would refuse or allow without harm. A denylist missed two session
#: effects that outlive Studio's one-statement transaction: ``DECLARE ... CURSOR WITH HOLD`` (materialized at
#: ``COMMIT``, after the cancel hook is detached, so neither the timeout nor a cancel stops it; also ``FETCH``,
#: ``MOVE``, ``CLOSE``) and a plain ``SET`` (it stays on the pooled connection: ``SET statement_timeout = 0``). The
#: write statements (``INSERT``, ``CREATE``, ...) were never needed either; the read-only session stays the boundary
#: for them, and the STU-T-010 tests send them below this check to prove it. ``PRAGMA`` is SQLite's only way to read
#: its settings; the authorizer allows only reading pragmas (and PostgreSQL has no such statement).
_FIRST_WORDS = frozenset({"select", "with", "values", "table", "explain", "show", "pragma"})
#: What ``EXPLAIN`` may explain: a query (``EXPLAIN ANALYZE`` runs it, so no other statement).
_QUERY_WORDS = frozenset({"select", "with", "values", "table"})
#: Words between ``EXPLAIN`` and the statement it explains (PostgreSQL's old option syntax, SQLite's ``QUERY PLAN``).
_EXPLAIN_OPTIONS = frozenset({"analyze", "analyse", "verbose", "query", "plan"})


def _explains_a_query(tokens: list[str]) -> bool:
    """Whether ``tokens`` (``EXPLAIN`` first) explain a query: options in parentheses or as words, then the query."""
    index = 1
    if tokens[index : index + 1] == ["("]:
        depth = 0
        while index < len(tokens):
            depth += {"(": 1, ")": -1}.get(tokens[index], 0)
            index += 1
            if depth == 0:
                break
    while index < len(tokens) and tokens[index] in _EXPLAIN_OPTIONS:
        index += 1
    while index < len(tokens) and tokens[index] == "(":
        index += 1
    return index < len(tokens) and tokens[index] in _QUERY_WORDS


def _check_words(words: list[str]) -> None:
    first = words[0]
    if first in _CONTROL:
        raise StatementRefused("studio.statement_transaction")
    if first in _OTHER_EFFECTS:
        raise StatementRefused("studio.statement_refused")
    if first == "set":
        rest = [word for word in words[1:] if word not in ("session", "local")]
        if rest[:1] == ["transaction"] or words[1:3] == ["session", "characteristics"]:
            raise StatementRefused("studio.statement_transaction")
        if rest[:1] and rest[0] in _SESSION_SETTINGS:
            raise StatementRefused("studio.statement_transaction")
    if first not in _FIRST_WORDS:
        raise StatementRefused("studio.statement_refused")


def _explains_a_query_or_is_none(tokens: list[str]) -> bool:
    start = 0
    while start < len(tokens) and tokens[start] == "(":
        start += 1
    return tokens[start : start + 1] != ["explain"] or _explains_a_query(tokens[start:])


def check_statement(sql: str) -> str:
    """The one statement of ``sql``; :class:`StatementRefused` for none, several, a transaction-control statement or
    one whose first word is not in the allowlist (:data:`_FIRST_WORDS`)."""
    if "\0" in sql:  # libpq ends the text at NUL, so the server would run less than was checked (R2-I2)
        raise StatementRefused("studio.statement_refused")
    statements = split_statements(sql)
    if not statements:
        raise StatementRefused("studio.statement_empty")
    if len(statements) > 1:
        raise StatementRefused("studio.statement_multiple")
    statement = statements[0]
    if not first_word(statement):  # no word, or the readings disagree where the statement starts (R-H1)
        raise StatementRefused(
            "studio.statement_empty" if not _tokens(statement, backslash_strings=False) else "studio.statement_refused"
        )
    if _calls_refused_function(statement):
        raise StatementRefused("studio.statement_refused")
    for backslash, sqlite in _READINGS:
        _check_words(_leading_words(statement, backslash_strings=backslash, sqlite=sqlite))
        if not _explains_a_query_or_is_none(_tokens(statement, backslash_strings=backslash, sqlite=sqlite)):
            raise StatementRefused("studio.statement_refused")
    return statements[0]
