"""Export of a query result or a table as CSV or JSON, streamed (03 sections 2 D and 6, STU-021).

An export runs one read-only statement like every Studio statement (``OpenConnection.prepare`` checks it, ``stream``
runs it in one ``Database.read()``, PostgreSQL through a server-side cursor) and writes the rows out as they come:

* **Bounded memory.** The rows are fetched in batches that follow the largest row so far (the query engine's
  :func:`.queries.next_batch_rows`), each batch is encoded at once, and the encoded chunks pass to the HTTP response
  through a queue of :data:`QUEUE_CHUNKS` chunks of about :data:`CHUNK_BYTES`. A slow client makes the producer wait
  (and the database cursor with it); nothing is collected in memory. STU-021's acceptance: 1 million rows without a
  peak above 100 MB.
* **Limits.** At most ``max_rows`` rows (default and maximum :data:`MAX_EXPORT_ROWS`); the export stops after
  :data:`EXPORT_TIMEOUT_S` (a cancel, as for a query); one export per connection and :data:`MAX_PARALLEL` at a time
  (each holds one of the connection's two sessions while it runs). A client that goes away cancels the export.
* **CSV** (``text/csv``, UTF-8, a header row of column names, RFC 4180 quoting): ``NULL`` is the empty field, bytes
  are base64, dates and times ISO 8601, JSON values JSON text. A text value that a spreadsheet would read as a
  formula (it starts with ``=``, ``+``, ``-``, ``@``, tab or carriage return) gets a leading ``'`` (CSV injection),
  unless the request turns ``escape_formulas`` off; so does base64 of bytes, which may start with ``+``. Numbers,
  dates and JSON values are never changed (a negative number stays a number). A cut export cannot say so in CSV:
  the row count tells.
* **JSON** (``application/json``): ``{"columns": [...], "rows": [[...], ...], "truncated": null | "rows"}``; rows as
  arrays, so two columns of the same name stay apart. Bytes are ``{"base64": ...}``, decimals, dates, times and
  UUIDs text, ``NaN`` and infinities text.

What the first chunk needs (the statement check, the parameters, the object, the first fetch) happens before the
response starts, so a refusal is a problem document; an error after that ends the stream early, and the log has it.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping, Sequence
import csv
from dataclasses import dataclass, field
import datetime
import decimal
import io
import json
import logging
import math
import queue
import re
import threading
from typing import Any, cast
import uuid

from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.queries import next_batch_rows, raw_size
from tabrion_studio.services.sqltext import BoundStatement
from tabrion_studio.services.tabrion_compat import Cancel, ResultCursor

__all__ = [
    "CHUNK_BYTES",
    "EXPORT_TIMEOUT_S",
    "MAX_EXPORT_ROWS",
    "Export",
    "ExportError",
    "Exports",
]

logger = logging.getLogger("tabrion_studio.exports")

MAX_EXPORT_ROWS = 1_000_000
EXPORT_TIMEOUT_S = 600.0
MAX_PARALLEL = 2
CHUNK_BYTES = 256 * 1024
QUEUE_CHUNKS = 8
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
_FILE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_END = object()


class ExportError(Exception):
    """An export that cannot start; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _iso(value: datetime.date | datetime.time) -> str:
    return value.isoformat()


def _text(value: object) -> str:
    """A value as CSV text (``None`` is handled by the caller)."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, datetime.date | datetime.time):
        return _iso(value)
    if isinstance(value, dict | list | tuple):
        return json.dumps(_plain(value), ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _plain(value: object) -> Any:
    """A value as a JSON value (recursively for arrays and JSON documents)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime.date | datetime.time):
        return _iso(value)
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, decimal.Decimal | uuid.UUID | datetime.timedelta):
        return str(value)
    return str(value)


class _Encoder:
    def __init__(self, export_format: str, columns: Sequence[str], escape_formulas: bool) -> None:
        self.format = export_format
        self.columns = list(columns)
        self.escape = escape_formulas
        self.rows = 0
        self._buffer = io.StringIO()
        self._csv = csv.writer(self._buffer, lineterminator="\r\n")

    def head(self) -> None:
        if self.format == "csv":
            self._csv.writerow([self._cell(name) for name in self.columns])
        else:
            self._buffer.write('{"columns":' + json.dumps(self.columns, ensure_ascii=False) + ',"rows":[')

    def _cell(self, value: str) -> str:
        return "'" + value if self.escape and value.startswith(_FORMULA_STARTS) else value

    def _field(self, value: object) -> str:
        if value is None:
            return ""
        text = _text(value)
        # Only text and bytes (base64 may start with "+") can be read as a formula; a negative number stays a number.
        return self._cell(text) if isinstance(value, str | bytes | bytearray | memoryview) else text

    def row(self, row: Sequence[Any]) -> None:
        if self.format == "csv":
            self._csv.writerow([self._field(value) for value in row])
        else:
            if self.rows:
                self._buffer.write(",")
            self._buffer.write("\n" + json.dumps([_plain(value) for value in row], ensure_ascii=False))
        self.rows += 1

    def tail(self, truncated: bool) -> None:
        if self.format == "json":
            self._buffer.write("\n]," + '"truncated":' + ('"rows"' if truncated else "null") + "}\n")

    def take(self, minimum: int = 0) -> bytes | None:
        if self._buffer.tell() <= minimum:
            return None
        data = self._buffer.getvalue().encode("utf-8")
        self._buffer.seek(0)
        self._buffer.truncate()
        return data


@dataclass
class Export:
    """One running export: :meth:`chunks` gives the encoded bytes; :meth:`close` stops it (from any thread)."""

    filename: str
    media_type: str
    _queue: queue.Queue[object] = field(repr=False)
    _hook: Cancel = field(repr=False)
    _stopped: threading.Event = field(repr=False)
    _done: threading.Event = field(repr=False)

    def chunks(self) -> Iterator[bytes]:
        """The encoded chunks; raises the export's error, or :class:`ExportError` (``studio.export_stopped``) when
        it was stopped before its end (the timeout)."""
        while True:
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._done.is_set() and self._queue.empty():
                    raise ExportError("studio.export_stopped") from None
                continue
            if item is _END:
                return
            if isinstance(item, BaseException):
                raise item
            yield cast(bytes, item)

    def close(self) -> None:
        if not self._done.is_set():
            self._stopped.set()
            self._hook.cancel()


@dataclass
class Exports:
    """Starts exports of open connections."""

    registry: ConnectionRegistry
    explorer: Explorer
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _running: dict[str, int] = field(default_factory=dict, repr=False)

    def start(
        self,
        connection_id: object,
        export_format: object,
        *,
        sql: object = None,
        params: Mapping[str, object] | None = None,
        table: object = None,
        max_rows: object = None,
        escape_formulas: object = True,
    ) -> Export:
        """Check the request and start the export; raises :class:`ExportError`, ``StatementRefused``,
        ``ParameterError`` or the connection's errors."""
        if export_format not in ("csv", "json"):
            raise ExportError("studio.export_invalid")
        rows = MAX_EXPORT_ROWS if max_rows is None else max_rows
        if type(rows) is not int or not 1 <= rows <= MAX_EXPORT_ROWS or not isinstance(escape_formulas, bool):
            raise ExportError("studio.export_invalid")
        connection = self.registry.get(connection_id)
        statement, base_name = self._statement(connection, sql, params, table)
        with self._lock:
            if sum(self._running.values()) >= MAX_PARALLEL or self._running.get(connection.id):
                raise ExportError("studio.export_busy")
            self._running[connection.id] = 1
        export = Export(
            filename=f"{base_name}.{export_format}",
            media_type="text/csv; charset=utf-8" if export_format == "csv" else "application/json",
            _queue=queue.Queue(maxsize=QUEUE_CHUNKS),
            _hook=Cancel(),
            _stopped=threading.Event(),
            _done=threading.Event(),
        )
        worker = threading.Thread(
            target=self._produce,
            args=(export, connection, statement, export_format, rows, escape_formulas),
            name="studio-export",
            daemon=True,
        )
        worker.start()
        return export

    def _statement(
        self, connection: OpenConnection, sql: object, params: Mapping[str, object] | None, table: object
    ) -> tuple[BoundStatement, str]:
        if (sql is None) == (table is None):
            raise ExportError("studio.export_invalid")
        if isinstance(sql, str):
            return connection.prepare(sql, params), "export"
        if not isinstance(table, dict) or not all(isinstance(table.get(key), str) for key in ("schema", "name")):
            raise ExportError("studio.export_invalid")
        schema, name = table["schema"], table["name"]
        reflected = self.explorer.columns(connection.id, schema, name)  # studio.object_not_found if it is not there
        if not reflected.columns:  # a sequence, or an object without columns
            raise ExportError("studio.export_invalid")
        columns = ", ".join(_quote(column["name"]) for column in reflected.columns)
        text = f"SELECT {columns} FROM {_quote(schema)}.{_quote(name)}"  # noqa: S608 - reflected, quoted names
        return connection.prepare(text), _FILE_NAME.sub("_", name)[:100] or "export"

    def _produce(
        self,
        export: Export,
        connection: OpenConnection,
        statement: BoundStatement,
        export_format: str,
        max_rows: int,
        escape_formulas: bool,
    ) -> None:
        timer = threading.Timer(EXPORT_TIMEOUT_S, export.close)
        timer.daemon = True
        timer.start()

        def put(item: object) -> None:
            while not export._stopped.is_set():
                try:
                    export._queue.put(item, timeout=0.1)
                    return
                except queue.Full:
                    continue
            raise _Stopped

        def consume(cursor: ResultCursor) -> int:
            encoder = _Encoder(export_format, [column.name for column in cursor.columns], escape_formulas)
            encoder.head()
            batch_rows, largest, truncated = 1, 1, False
            while not export._stopped.is_set():
                batch = cursor.fetch(min(batch_rows, max_rows + 1 - encoder.rows))
                if not batch:
                    break
                largest = max(largest, *(raw_size(row) for row in batch))
                batch_rows = next_batch_rows(len(batch), largest)
                for row in batch:
                    if encoder.rows >= max_rows:
                        truncated = True
                        break
                    encoder.row(row)
                    chunk = encoder.take(CHUNK_BYTES)
                    if chunk is not None:
                        put(chunk)
                if truncated:
                    break
            if export._stopped.is_set():
                raise _Stopped
            encoder.tail(truncated)
            chunk = encoder.take()
            if chunk is not None:
                put(chunk)
            return encoder.rows

        try:
            rows = connection.stream(statement, consume, cancel=export._hook)
            put(_END)
            logger.info("export finished", extra={"event": "studio.export_finished", "rows": rows})
        except _Stopped:
            logger.info("export stopped", extra={"event": "studio.export_stopped"})
        except Exception as exc:
            if export._stopped.is_set():
                logger.info("export stopped", extra={"event": "studio.export_stopped"})
            else:
                logger.info("export failed", extra={"event": "studio.export_failed", "error": type(exc).__name__})
                try:
                    export._queue.put(exc, timeout=5)
                except queue.Full:
                    export.close()
        finally:
            timer.cancel()
            export._done.set()
            with self._lock:
                self._running.pop(connection.id, None)


class _Stopped(Exception):
    """The export was closed (the client went away, or the timeout)."""
