"""Migration status of the Levuvo formats (03 section 2 F, STU-024): Nodestro, Axentra and Orkexa on PostgreSQL.

The three platforms keep their own version tables. Studio reads them and the migration files of the project
folder, and **runs nothing**:

* **Nodestro** (``nodestro_schema_migrations``: ``version``, ``name`` = file name, ``checksum``): forward-only SQL
  files ``NNNN_name.sql`` of ``nodestro_runtime/migrations/postgres``. The checksum is the SHA-256 of the file's
  text (UTF-8, line ends as Python's ``read_text`` gives them); a table of a release before checksums has no
  ``checksum`` column.
* **Axentra** (``axentra_schema_migrations``: ``namespace``, ``version``, ``name``, ``checksum``): SQL files
  ``NNNN_name.sql`` per namespace; Axentra's own namespace ``axentra`` is ``axentra/data/sql``. Product namespaces
  are registered in code (``database.extra_migrations``), which Studio does not run: they show what the database
  recorded, without files (``state: no_files``).
* **Orkexa** (``<prefix>orkexa_schema``: ``version``): the schema is code (``schema_steps``), so there is no file to
  compare; the newest version is ``SCHEMA_VERSION`` in ``orkexa/backends/postgres/schema.py``, which Studio reads as
  text (a regular expression, never an import).

Where the files are: the first folder found, inside the project folder, of ``src/<package path>``,
``<package path>`` and the ``site-packages`` of a virtual environment in the project (``.venv``, ``venv``). The same
rules as for the Alembic files of STU-015 (:mod:`tabrion_studio.config.code_trust`) apply: the folder must lie inside
the project after links are followed, every file must be a regular file and not a link, at most 2 000 files and
256 KB per file. Since nothing is imported or executed, these files need no code trust (threat T-16 does not apply);
they are shown as text only.

Per format and namespace, ``state`` is ``current``, ``pending`` (files the database has not applied), ``newer`` (the
database has versions the files do not know), ``changed`` (an applied file whose name or checksum differs) or
``no_files``; ``steps`` lists each version with ``status`` ``applied``, ``pending``, ``unknown`` or ``changed`` and,
for a pending step, the file name, checksum and the first 2 000 characters of its SQL as the runner reads it, every
line end ``\n`` (``preview``).

One version table that cannot be read never fails the whole answer (F-1 of ``stu-024-review.md``): a table the role
may not read (no ``USAGE`` on its schema or no ``SELECT`` on it), a table without the expected columns and types, or
a read that fails gives that table's entry ``state: unreadable`` with ``problem`` (a problem document, else
``null``); the other formats and tables are read as usual. Orkexa tables are only names Orkexa can create
(``PREFIX`` of ``orkexa/backends/postgres/_sql.py`` followed by ``orkexa_schema``).
"""

from __future__ import annotations

from dataclasses import dataclass
import glob
import hashlib
import logging
import os
from pathlib import Path
import re
import stat
from typing import Any

from tabrion.errors import Kind, TabrionError

from tabrion_studio.config.files import ConfigFileError, read_plain_file
from tabrion_studio.config.locations import contained
from tabrion_studio.problems import db_kind_problem, db_problem, problem
from tabrion_studio.services.connections import OpenConnection
from tabrion_studio.services.readonly import StatementRefused

__all__ = ["MAX_FILES", "PREVIEW_CHARS", "levuvo_formats", "sql_checksum"]

MAX_FILES = 2_000
PREVIEW_CHARS = 2_000
MAX_ROWS = 10_000
_FILE_NAME = re.compile(r"(\d{4})_([a-z0-9_]+)\.sql")
_NODESTRO_FILES = "nodestro_runtime/migrations/postgres"
_AXENTRA_FILES = "axentra/data/sql"
_ORKEXA_SOURCE = "orkexa/backends/postgres/schema.py"
_ORKEXA_VERSION = re.compile(r"^SCHEMA_VERSION\s*=\s*(\d{1,6})\s*$", re.M)
_VENVS = (".venv", "venv")
#: Orkexa's version table: its table prefix (``PREFIX`` in ``orkexa/backends/postgres/_sql.py``) + ``orkexa_schema``.
_ORKEXA_TABLE = re.compile(r"[a-z][a-z0-9_]{0,30}_orkexa_schema")
#: Each table with whether the role may read it and its columns (``name type``); privileges are checked here, so a
#: table the role may not read is reported, not queried.
_TABLES_SQL = (
    "SELECT n.nspname, c.relname, "
    "pg_catalog.has_schema_privilege(n.oid, 'USAGE') AND pg_catalog.has_table_privilege(c.oid, 'SELECT'), "
    "ARRAY(SELECT a.attname || ' ' || pg_catalog.format_type(a.atttypid, NULL) FROM pg_catalog.pg_attribute a "
    "WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum) "
    "FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p') AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
    "AND (c.relname IN ('nodestro_schema_migrations', 'axentra_schema_migrations') "
    "OR c.relname ~ '^[a-z][a-z0-9_]{0,30}_orkexa_schema$') "
    "ORDER BY n.nspname, c.relname LIMIT 100"
)
_INTEGER = frozenset({"smallint", "integer", "bigint"})
#: The columns each format reads; ``None``: any type (read as text). Nodestro's ``checksum`` is optional.
_COLUMNS: dict[str, dict[str, frozenset[str] | None]] = {
    "nodestro": {"version": _INTEGER, "name": None},
    "axentra": {"namespace": None, "version": _INTEGER, "name": None, "checksum": None},
    "orkexa": {"version": _INTEGER},
}

logger = logging.getLogger(__name__)


def _runner_text(data: bytes) -> str:
    """A migration file as the runners read it: ``Path.read_text`` (UTF-8, every line end ``\\n``). The same on every
    platform, whatever line ends the file was written with."""
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def sql_checksum(data: bytes) -> str:
    """The checksum Nodestro and Axentra record: SHA-256 of the text as the runners read it (:func:`_runner_text`)."""
    return hashlib.sha256(_runner_text(data).encode("utf-8")).hexdigest()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _select(columns: str, schema: str, table: str, rest: str = "") -> str:
    """``SELECT columns FROM "schema"."table" rest``: the names come from the catalog (``_TABLES_SQL``) and are
    quoted as identifiers; ``columns`` and ``rest`` are constants of this module."""
    return f"SELECT {columns} FROM {_quote(schema)}.{_quote(table)} {rest}".rstrip()  # noqa: S608


@dataclass(frozen=True)
class Table:
    schema: str
    name: str
    readable: bool
    columns: dict[str, str]  # column name: type as format_type gives it


class _Unreadable(Exception):
    def __init__(self, doc: dict[str, Any]) -> None:
        super().__init__(doc["code"])
        self.doc = doc


def _check(table: Table, format_name: str) -> None:
    """Raise :class:`_Unreadable` if the role may not read ``table`` or it lacks the columns ``format_name`` reads."""
    if not table.readable:
        raise _Unreadable(db_kind_problem(Kind.PERMISSION).to_dict())
    for column, types in _COLUMNS[format_name].items():
        found = table.columns.get(column)
        if found is None or (types is not None and found not in types):
            raise _Unreadable(problem("studio.migrations_table").to_dict())


def _rows(connection: OpenConnection, sql: str) -> list[tuple[Any, ...]]:
    """``connection.fetch(sql)``; a failure raises :class:`_Unreadable` with its problem."""
    try:
        return connection.fetch(sql)
    except StatementRefused as exc:
        raise _Unreadable(problem(exc.code).to_dict()) from exc
    except TabrionError as exc:
        raise _Unreadable(db_problem(exc).to_dict()) from exc


def _unreadable(entry: dict[str, Any], format_name: str, exc: Exception) -> dict[str, Any]:
    """``entry`` as ``state: unreadable``; ``exc`` is :class:`_Unreadable`, or a value the conversion refused."""
    doc = exc.doc if isinstance(exc, _Unreadable) else problem("studio.migrations_table").to_dict()
    logger.info(
        "version table not readable",
        extra={"event": "studio.levuvo_unreadable", "format": format_name, "code": doc["code"]},
    )
    return {**entry, "current": None, "state": "unreadable", "steps": [], "problem": doc}


@dataclass(frozen=True)
class MigrationFile:
    version: int
    name: str
    file: str
    checksum: str
    preview: str


@dataclass(frozen=True)
class FileSet:
    folder: str  # relative to the project, with "/"
    files: dict[int, MigrationFile]
    issue: str | None = None


def _candidates(project: Path, package_path: str) -> list[str]:
    found = [f"src/{package_path}", package_path]
    for venv in _VENVS:
        pattern = os.path.join(glob.escape(str(project)), venv, "lib", "python3*", "site-packages", package_path)
        found += sorted(os.path.relpath(path, project) for path in glob.glob(pattern))
        found.append(f"{venv}/Lib/site-packages/{package_path}")  # Windows
    return found


def _inside_dir(project: Path, relative: str) -> Path | None:
    root = Path(os.path.abspath(project))
    if not contained(relative, root, [root], [root.resolve()]):
        return None
    path = Path(os.path.realpath(root / relative))
    try:
        return path if stat.S_ISDIR(os.lstat(path).st_mode) else None
    except OSError:
        return None


def _file_set(project: Path, package_path: str) -> FileSet | None:
    for relative in _candidates(project, package_path):
        folder = _inside_dir(project, relative)
        if folder is not None:
            return _read_files(folder, relative.replace(os.sep, "/"))
    return None


def _read_files(folder: Path, relative: str) -> FileSet:
    names = sorted(entry.name for entry in os.scandir(folder) if _FILE_NAME.fullmatch(entry.name))
    if len(names) > MAX_FILES:
        return FileSet(relative, {}, "migrations.too_many_files")
    files: dict[int, MigrationFile] = {}
    for file_name in names:
        match = _FILE_NAME.fullmatch(file_name)
        if match is None:
            continue
        try:
            data = read_plain_file(folder / file_name)
        except ConfigFileError as exc:
            return FileSet(relative, {}, exc.code)
        if data is None:
            continue
        try:
            checksum = sql_checksum(data)
        except UnicodeDecodeError:
            return FileSet(relative, {}, "config.not_utf8")
        text = _runner_text(data)  # the preview shows the text the checksum covers, with the same line ends everywhere
        files[int(match.group(1))] = MigrationFile(
            int(match.group(1)), match.group(2), file_name, checksum, text[:PREVIEW_CHARS]
        )
    return FileSet(relative, files)


def _orkexa_expected(project: Path) -> tuple[int | None, str | None]:
    for relative in _candidates(project, _ORKEXA_SOURCE.rpartition("/")[0]):
        folder = _inside_dir(project, relative)
        if folder is None:
            continue
        try:
            data = read_plain_file(folder / "schema.py")
        except ConfigFileError:
            return None, None
        if data is None:
            continue
        match = _ORKEXA_VERSION.search(data.decode("utf-8", errors="replace"))
        return (int(match.group(1)) if match else None), f"{relative.replace(os.sep, '/')}/schema.py"
    return None, None


def _compare(files: FileSet | None, applied: list[tuple[int, str, str | None]], *, file_names: bool) -> dict[str, Any]:
    """``applied``: (version, name as recorded, checksum or None). ``file_names``: Nodestro records the file name,
    Axentra only the name part."""
    if files is None or files.issue is not None:
        recorded_steps = [{"version": v, "name": n, "status": "applied"} for v, n, _ in applied]
        return {
            "files": None if files is None else files.folder,
            "files_issue": None if files is None else files.issue,
            "state": "no_files",
            "steps": recorded_steps,
            "problem": None,
        }
    recorded = {version: (name, checksum) for version, name, checksum in applied}
    steps: list[dict[str, Any]] = []
    for version in sorted(set(recorded) | set(files.files)):
        found = files.files.get(version)
        record = recorded.get(version)
        if found is None and record is not None:
            steps.append({"version": version, "name": record[0], "status": "unknown"})
        elif found is not None and record is None:
            steps.append({
                "version": version, "name": found.name, "file": found.file, "status": "pending",
                "checksum": found.checksum, "preview": found.preview,
            })  # fmt: skip
        elif found is not None and record is not None:
            name_ok = record[0] == (found.file if file_names else found.name)
            checksum_ok = record[1] is None or record[1] == found.checksum
            steps.append({
                "version": version, "name": found.name, "file": found.file,
                "status": "applied" if name_ok and checksum_ok else "changed",
                "checksum_recorded": record[1] is not None,
            })  # fmt: skip
    statuses = {step["status"] for step in steps}
    state = next(
        (
            name
            for status, name in (("changed", "changed"), ("unknown", "newer"), ("pending", "pending"))
            if status in statuses
        ),
        "current",
    )
    return {"files": files.folder, "files_issue": None, "state": state, "steps": steps, "problem": None}


def _files(files: FileSet | None) -> dict[str, Any]:
    return {"files": None if files is None else files.folder, "files_issue": None if files is None else files.issue}


def _orkexa_state(current: int, expected: int | None) -> str:
    if expected is None:
        return "no_files"
    return "current" if current == expected else ("pending" if current < expected else "newer")


def levuvo_formats(connection: OpenConnection, project: Path) -> list[dict[str, Any]]:
    """The Levuvo formats found in the database of ``connection`` or in the project folder (PostgreSQL only)."""
    if connection.engine != "postgresql":
        return []
    tables = [
        Table(str(schema), str(name), readable is True, dict(str(column).split(" ", 1) for column in columns or ()))
        for schema, name, readable, columns in connection.fetch(_TABLES_SQL)
    ]
    found: list[dict[str, Any]] = []
    nodestro_files = _file_set(project, _NODESTRO_FILES)
    axentra_files = _file_set(project, _AXENTRA_FILES)
    nodestro_tables = [table for table in tables if table.name == "nodestro_schema_migrations"]
    for table in nodestro_tables:
        entry: dict[str, Any] = {"format": "nodestro", "schema": table.schema, "table": table.name}
        try:
            _check(table, "nodestro")
            columns = "version, name, checksum" if "checksum" in table.columns else "version, name, NULL"
            rows = _rows(connection, _select(columns, table.schema, table.name, f"ORDER BY version LIMIT {MAX_ROWS}"))
            applied = [(int(v), str(n), None if c is None else str(c)) for v, n, c in rows]
        except (_Unreadable, ValueError, TypeError) as exc:
            found.append(_unreadable({**entry, **_files(nodestro_files)}, "nodestro", exc))
            continue
        found.append({
            **entry, "current": max((v for v, _, _ in applied), default=0),
            **_compare(nodestro_files, applied, file_names=True),
        })  # fmt: skip
    if not nodestro_tables and nodestro_files is not None:
        found.append({"format": "nodestro", "schema": None, "table": None, "current": 0,
                      **_compare(nodestro_files, [], file_names=True)})  # fmt: skip
    axentra_tables = [table for table in tables if table.name == "axentra_schema_migrations"]
    for table in axentra_tables:
        entry = {"format": "axentra", "schema": table.schema, "table": table.name}
        by_namespace: dict[str, list[tuple[int, str, str | None]]] = {}
        try:
            _check(table, "axentra")
            rows = _rows(
                connection,
                _select(
                    "namespace, version, name, checksum",
                    table.schema,
                    table.name,
                    f"ORDER BY namespace, version LIMIT {MAX_ROWS}",
                ),
            )
            for namespace, version, name, checksum in rows:
                by_namespace.setdefault(str(namespace), []).append((int(version), str(name), str(checksum)))
        except (_Unreadable, ValueError, TypeError) as exc:
            found.append(_unreadable({**entry, "namespace": None, **_files(axentra_files)}, "axentra", exc))
            continue
        if axentra_files is not None:
            by_namespace.setdefault("axentra", [])
        for namespace, applied in sorted(by_namespace.items()):
            files = axentra_files if namespace == "axentra" else None
            found.append({
                **entry, "namespace": namespace, "current": max((v for v, _, _ in applied), default=0),
                **_compare(files, applied, file_names=False),
            })  # fmt: skip
    if not axentra_tables and axentra_files is not None:
        found.append({"format": "axentra", "schema": None, "table": None, "namespace": "axentra", "current": 0,
                      **_compare(axentra_files, [], file_names=False)})  # fmt: skip
    expected, source = _orkexa_expected(project)
    for table in tables:
        if not _ORKEXA_TABLE.fullmatch(table.name):
            continue
        entry = {
            "format": "orkexa", "schema": table.schema, "table": table.name,
            "prefix": table.name[: -len("orkexa_schema")], "expected": expected, "files": source, "files_issue": None,
        }  # fmt: skip
        try:
            _check(table, "orkexa")
            current = int(_rows(connection, _select("coalesce(max(version), 0)", table.schema, table.name))[0][0])
        except (_Unreadable, ValueError, TypeError) as exc:
            found.append(_unreadable(entry, "orkexa", exc))
            continue
        found.append({**entry, "current": current, "state": _orkexa_state(current, expected), "steps": [],
                      "problem": None})  # fmt: skip
    return found
