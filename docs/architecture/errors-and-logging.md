# Errors and logging (STU-004)

Two rules hold for every error path in Studio (03 section 4.5, threat T-05, SQ-04):

1. **The page gets a code and fixed texts, nothing else.** No exception message, no path, no stack trace, no
   secret and no value from the request or from a database server reaches the browser.
2. **The log gets the facts, never the values.** Records keep routes, codes, durations and the frames of a
   traceback, but never SQL parameter values, result rows, passwords, tokens, full connection URLs or exception
   messages.

The code is in `src/tabrion_studio/problems.py` (problem documents), `logs.py` (redaction, formats, files) and
`request_log.py` (the request log and the catch-all for unexpected errors).

## Problem JSON

Every error answer is a problem document with the media type `application/problem+json`:

```json
{"code": "access.session_required", "kind": "access", "status": 401, "title": "Not signed in",
 "detail": "This request needs a Studio session.",
 "hint": "Run tabrion-studio again; it opens Studio with a new sign-in link."}
```

| Field | Content |
|---|---|
| `code` | `access.*` (access protection), `studio.*` (Studio's own errors), from STU-009 on `db.<kind>` |
| `kind` | `access`, `request`, `not_found`, `config` or `internal`; for `db.<kind>` the Tabrion error kind |
| `status` | the HTTP status, also sent as the status line |
| `title`, `detail`, `hint` | fixed English texts from the catalog (`hint` may be `null`); the page shows its own de/en text for `code` |
| `log_id` | only for `studio.internal_error`: 12 random hex digits, also in the log record |
| `sqlstate`, `position` | reserved for database errors (STU-011): five characters `[0-9A-Z]`, a non-negative integer |

`problems.problem(code)` is the only way to build one. It takes a catalog code and, optionally, a log ID, a SQLSTATE
and a position, each checked against its format; anything else raises. So no caller can put free text into an
answer. The catalog (`problems.CATALOG`) lists every code with its status and texts; a test checks that every code
the code base sends is in it and that no text contains a placeholder or anything that looks like a path.

Where the answers come from:

- **Refusals of the access middleware** (`access.host_not_allowed`, `access.session_required`, ...). The texts no
  longer repeat request data (before STU-004, `access.forwarded_not_allowed` named the forwarded headers).
- **Route errors** (`access.bad_request`, `access.launch_used`, `studio.bad_nonce`, ...).
- **Framework errors:** a missing file or an unknown path (`studio.not_found`, 404), a wrong method
  (`studio.method_not_allowed`, 405, with `Allow`), any other `HTTPException` (`studio.bad_request`). A request that
  fails FastAPI's validation gets `studio.invalid_request` (422). FastAPI's default answer would echo the offending
  input values; Studio's names none.
- **Unexpected exceptions:** `RequestLogMiddleware` catches every exception that escapes a route or a middleware,
  logs it once at ERROR with a new log ID and answers `studio.internal_error` with that log ID and the security
  headers. The exception is not raised further, so Uvicorn never logs it unredacted.

### Database errors (STU-009, STU-011)

`problems.DB_KINDS` is the set of `tabrion.errors.Kind` values (no mirror since STU-009). `problems.db_problem(error)`
builds the `db.<kind>` problem from `tabrion.errors.classify` only: the kind selects fixed texts of
`problems.DB_CATALOG` (status, title, detail, hint), and the SQLSTATE (five characters `[0-9A-Z]`) is the only part
taken from the error. The server's message, its `DETAIL`, constraint and object names and every value stay out of the
answer; `tabrion.errors.safe_message` is for the log only.

The log handlers run Studio's `RedactionFilter` first (an exception becomes its types and frames, without any
message) and then tabrion's `DbErrorRedactionFilter`, which rewrites database errors in the arguments and removes
`DETAIL:`, `HINT:`, `CONTEXT:` and `[parameters:` parts from the message; since tabrion 0.2.0 also a `DETAIL:`,
`HINT:`, `CONTEXT:` or `QUERY:` part in the middle of a one-line message (TD-056, pinned by
`test_a_database_detail_in_the_middle_of_a_line_is_redacted`). The other order would leave exception
messages in the traceback text (`tests/security/test_canary.py`).

## Logging

`configure_logging` installs two handlers on the root logger, so Studio's loggers (`tabrion_studio.*`), Uvicorn's
(`log_config=None`) and asyncio's all pass the same redaction:

| Handler | Format | Level | Where |
|---|---|---|---|
| file | JSON lines | INFO | `<log dir>/studio-<key>.log`, one file per project instance (named like its info file) |
| console | one text line per record | WARNING | stderr of the `tabrion-studio` process |

It also takes over the loggers that carry exception text, `uvicorn`, `uvicorn.error`, `uvicorn.access`,
`uvicorn.asgi` and `asyncio` (`logs.OWNED_LOGGERS`, TD-055): their own handlers are removed, they propagate to the
root logger again, and the redaction filters sit on the loggers themselves. Uvicorn's default `log_config` (any
`uvicorn.Config` built without `log_config=None` in the same process, or another program's `dictConfig`) gives
`uvicorn` a stderr handler and `propagate=False`; without the takeover, a failed lifespan's traceback text would
bypass Studio's handlers and be printed as it is. A record logged *at* one of these loggers is scrubbed by the
logger filters before any handler, also one that a later `dictConfig` adds to them. The limit: a logger filter does
not run for records of child loggers (`uvicorn.error.x`) or of other loggers, so after a later `dictConfig` such a
record reaches Uvicorn's re-added handler unredacted, and with `propagate=False` again Uvicorn's records no longer
reach Studio's log file. `serve()` runs no `dictConfig` and Uvicorn logs only at the owned names, so the
`tabrion-studio` command does not hit either case (TD-055). `LogSetup.close()` restores the earlier state.

- **Log directory:** `--log-dir`, else `$TABRION_STUDIO_LOG_DIR`, else `<state dir>/logs` when the state directory
  was given with `--state-dir` or `$TABRION_STUDIO_STATE_DIR`, else the per-user log directory
  (`platformdirs.user_log_dir("tabrion-studio")`). The directory gets the checks of the state directory (0700,
  owner, no symlink or junction, the Windows profile rules). The command prints `Log: <file>` at start.
- **Files:** created 0600 with `O_NOFOLLOW`; a symlink, a file of another user or a non-regular file is refused, and
  an existing file of this user with a looser mode is set to 0600. If the directory or the file is unusable, Studio
  logs to the console only and says so there (it does not refuse to start).
- **Rotation:** at 5 MB, four older files kept (`.1` to `.4`), so at most 5 x 5 MB per project. Only one process
  writes a file (the instance lock), so rotation is safe on Windows too.
- **Fields:** `time` (UTC, ms), `level`, `logger`, `message` and, where set, `event`, `code`, `log_id`, `method`,
  `route`, `status`, `duration_ms`, `profile_id`, `query_id`, `error_kind`, plus `exception` (the safe traceback).
  Any other `extra` field is dropped.

### Redaction

`RedactionFilter` runs on every record before a handler formats it:

- The message is formatted once and scrubbed: every value registered with `register_secret` becomes `[redacted]`
  (the control token, every launch token, session ID and CSRF value of this process; registered where they are
  created), URL passwords become `***` (`postgresql://user:***@host/db`, up to the last `@`, so a password with `@`
  or `/` is masked whole), the values of `password=`, `pwd:`, `secret`, `token`, `api_key`, `private_key`, `csrf`
  style keys are removed, also as the end of a longer name (`db_password=`, `PGPASSWORD=`, `sslpassword=`,
  `user_token=`, `auth_secret:`) and as JSON and Python-repr keys, and so are `Bearer` credentials, the credentials
  of an `Authorization:` header (`Basic` and any other scheme) and Studio session cookies (review L-2). Every pattern
  has bounded repetitions, so a long hostile message is scrubbed in linear time.
- An exception passed as a `%s`/`%r` argument is formatted as its type only (`ValueError: [message redacted]`).
- **Exception text in the message itself (review L-1 of `stu-004-review.md`).** asyncio's default exception handler
  writes `repr(task)` or `repr(future)` (which contains `exception=ValueError('...')`) and the repr of callbacks with
  their arguments into the message. A record of the `asyncio` logger therefore keeps only a known fixed first line
  (`Task exception was never retrieved [details redacted]`, `Exception in callback [details redacted]`, otherwise
  `asyncio: [message redacted]`); its `exc_info` is still logged, frames only. A formatted traceback passed as the
  message (Starlette sends `traceback.format_exc()` when the lifespan fails, and Uvicorn logs it as text) is cut at
  `Traceback (most recent call last):` (`[traceback text redacted]`); Studio's lifespan logs the exception itself
  first, with `exc_info` (`event: studio.lifespan_failed`).
- An exception is formatted without any message: each exception of the chain (cause first, at most 8, cycles end)
  shows its frames and `module.Type: [message redacted]`. Notes (`add_note`) and local variables are never printed.
  A driver's message may hold a server `DETAIL` with row values (B-06-1), SQLAlchemy's `[parameters: ...]` or a
  connection URL with a password. Each exception keeps its first and last 20 frames (`... N more frames ...` in
  between), and the traceback of one record is capped at 32 KB (review L-3: a `RecursionError` wrote 160 KB).
- Control characters, line and paragraph separators and bidi controls are escaped (`\x0a`, `‮`) in the text
  output and in every extra field, so a value cannot forge a second log line; messages are capped at 4 KB and
  fields at 256 characters.

### Request log

`RequestLogMiddleware` logs one record (`event: http.request`) per request: the method, the route **template**
(`/api/control/{action}`), `static` for files of the built frontend or `<unmatched>`, the status, the duration and,
for a problem answer, its code. The raw path, the query string, headers, cookies and bodies are never logged: the
client chooses them. API requests are logged at INFO, the health check, the heartbeat and static files at DEBUG,
every 4xx and 5xx answer at least at INFO. Uvicorn's access log stays off.

### Not yet

- `log_sql = true` (settings, STU-006) and SQL text at DEBUG, cut to 2 KB, arrive with the query engine (STU-011).
- A `--log-level` option is not offered; the level is INFO for the file.

## Tests

| Test | What it proves |
|---|---|
| `tests/security/test_canary.py` (STU-T-008, -009) | a canary (`tabrion-canary-<uuid>`) in a connection URL password, a server `DETAIL`, `[parameters: ...]`, a chained cause, a note, a local variable, the query string, headers and the body is absent from the 500 answer and from both log outputs; the answer has no traceback, path or type name; the log has the log ID, the types and the frames; the process tokens are redacted in every field; the request log names routes, never query strings; validation errors do not echo input; asyncio's task, future and callback reprs and a failing lifespan (any app under Uvicorn, and Studio's own) leak no canary (review L-1) |
| `tests/security/test_log_process.py` | a real `tabrion-studio` process writes the 0600 JSON log in a 0700 folder; canaries in a bogus launch token, query strings and headers appear neither in the log file nor in the terminal; Uvicorn's records pass the same handler; no access log; a real process whose startup fails with a canary in asyncio's exception handler, in a callback argument and in the lifespan exception leaks it neither to the log file nor to the terminal (review L-1) |
| `tests/unit/test_problems.py` | catalog texts are fixed and plain; every code sent is in the catalog; `problem()` refuses free text in every argument; refusals and framework errors are problem documents (404, 405 with `Allow`) |
| `tests/unit/test_logs.py` | JSON and text formats, dropped extra fields, escaped control characters, private files, a symlinked log file and an unsafe folder refused, rotation to five 0600 files, the scrub patterns (with the forms of review L-2) and their linear time, safe tracebacks (chains, groups, cycles, the frame and size caps of L-3), asyncio and traceback text in messages, exceptions as arguments, broken records |
