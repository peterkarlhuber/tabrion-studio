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
refuses several statements itself. ``SET standard_conforming_strings``, ``SET backslash_quote`` and ``set_config(``
anywhere in the text are refused too. The query engine (STU-011) must send user SQL through :func:`check_statement`,
one ``read()`` per statement, and always through ``ConnectionRegistry.get`` (which checks the handle again).
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
#: ``set_config()`` reaches every setting above from inside a SELECT, where the SET check cannot see it.
_SET_CONFIG = re.compile(r"set_config\s*\(", re.IGNORECASE)
#: Statements with an effect outside the transaction's data, or none a read-only view needs (``COPY ... TO`` writes a
#: server file under ``pg_write_server_files``; ``LOAD`` loads a library; ``NOTIFY`` reaches other sessions).
_OTHER_EFFECTS = frozenset({"copy", "load", "notify", "listen", "unlisten", "lock", "vacuum", "checkpoint", "cluster"})


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
    if _SET_CONFIG.search(statements[0]):
        raise StatementRefused("studio.statement_refused")
    if first == "set":
        rest = [word for word in words[1:] if word not in ("session", "local")]
        if rest[:1] == ["transaction"] or words[1:3] == ["session", "characteristics"]:
            raise StatementRefused("studio.statement_transaction")
        if rest[:1] and rest[0] in _SESSION_SETTINGS:
            raise StatementRefused("studio.statement_transaction")
    return statements[0]
