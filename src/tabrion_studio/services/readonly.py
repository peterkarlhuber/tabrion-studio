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
* :func:`check_statement`, which refuses a text with more than one statement and every statement of the
  transaction-control classes (``BEGIN``, ``COMMIT``, ``SET TRANSACTION``, ``SET SESSION CHARACTERISTICS``,
  ``RESET``, ``SET default_transaction_read_only``, ...). PostgreSQL runs a multi-statement text in one round trip,
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

__all__ = ["StatementRefused", "check_statement", "sqlite_authorizer"]

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


_LEADING = re.compile(r"(?:\s+|--[^\n]*(?:\n|$))+")
_WORDS = re.compile(r"[A-Za-z_]+")
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


def _tokens(sql: str, *, backslash_strings: bool) -> list[str]:
    """The words and symbols of ``sql``, without white space, comments and literals.

    Unquoted words are lowercased; a quoted identifier gives its exact content (``"set_config"`` -> ``set_config``).
    ``backslash_strings`` reads ``'...'`` the way the server does with ``standard_conforming_strings = off``;
    ``E'...'`` always takes backslash escapes. Linear in the length of ``sql``.
    """
    tokens: list[str] = []
    index, size = 0, len(sql)
    while index < size:
        char = sql[index]
        if char.isspace():
            index += 1
        elif sql.startswith("--", index):
            ends = [position for position in (sql.find("\n", index), sql.find("\r", index)) if position >= 0]
            index = min(ends) if ends else size
        elif sql.startswith("/*", index):
            depth, index = 1, index + 2
            while index < size and depth:
                if sql.startswith("/*", index):
                    depth, index = depth + 1, index + 2
                elif sql.startswith("*/", index):
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
        elif char == "'":
            index = _string_end(sql, index, backslash=backslash_strings)
        elif char == '"':
            end = index + 1
            parts = []
            while end < size:
                close = sql.find('"', end)
                if close < 0:
                    parts.append(sql[end:])
                    end = size
                    break
                parts.append(sql[end:close])
                if sql.startswith('""', close):
                    parts.append('"')
                    end = close + 2
                else:
                    end = close + 1
                    break
            tokens.append("".join(parts))
            index = end
        elif char == "$" and (match := _DOLLAR_TAG.match(sql, index)) is not None:
            close = sql.find(match.group(), match.end())
            index = size if close < 0 else close + len(match.group())
        elif _is_word_start(char):
            end = index + 1
            while end < size and _is_word_char(sql[end]):
                end += 1
            word = sql[index:end].lower()
            if word == "e" and sql.startswith("'", end):
                index = _string_end(sql, end, backslash=True)
                continue
            if word == "u" and sql.startswith('&"', end):
                tokens.append(_UNICODE_IDENTIFIER)
            tokens.append(word)
            index = end
        else:
            tokens.append(char)
            index += 1
    return tokens


def _calls_refused_function(sql: str) -> bool:
    """Whether ``sql`` calls a function of :data:`_REFUSED_FUNCTIONS` (or holds a Unicode-escaped identifier).

    The text is read twice, with and without backslash escapes in ``'...'``, so a literal cannot hide a call from
    whichever way the server lexes it (R-L1 of the re-review: ``"set_config"(`` and ``set_config/**/(``).
    """
    for backslash_strings in (False, True):
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


def _strip_leading(sql: str) -> str:
    """``sql`` without leading white space, line comments and (nested) block comments."""
    text = sql
    while True:
        match = _LEADING.match(text)
        if match:
            text = text[match.end() :]
        if not text.startswith("/*"):
            return text
        depth, index = 0, 0
        while index < len(text):
            if text.startswith("/*", index):
                depth, index = depth + 1, index + 2
            elif text.startswith("*/", index):
                depth, index = depth - 1, index + 2
                if depth == 0:
                    break
            else:
                index += 1
        if depth != 0:
            return ""
        text = text[index:]


def check_statement(sql: str) -> str:
    """The one statement of ``sql``; :class:`StatementRefused` for none, several or a transaction-control statement."""
    statements = split_statements(sql)
    if not statements:
        raise StatementRefused("studio.statement_empty")
    if len(statements) > 1:
        raise StatementRefused("studio.statement_multiple")
    words = [word.lower() for word in _WORDS.findall(_strip_leading(statements[0])[:200])[:4]]
    if not words:
        raise StatementRefused("studio.statement_empty")
    first = words[0]
    if first in _CONTROL:
        raise StatementRefused("studio.statement_transaction")
    if first in _OTHER_EFFECTS:
        raise StatementRefused("studio.statement_refused")
    if _calls_refused_function(statements[0]):
        raise StatementRefused("studio.statement_refused")
    if first == "set":
        rest = [word for word in words[1:] if word not in ("session", "local")]
        if rest[:1] == ["transaction"] or words[1:3] == ["session", "characteristics"]:
            raise StatementRefused("studio.statement_transaction")
        if rest[:1] and rest[0] in _SESSION_SETTINGS:
            raise StatementRefused("studio.statement_transaction")
    return statements[0]
