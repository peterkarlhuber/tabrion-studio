# Process model (STU-002)

```
tabrion-studio [PROJECT] [--port N] [--no-browser] [--idle-timeout MINUTES]
tabrion-studio --stop [PROJECT]
```

## One server per project folder

1. The command resolves `PROJECT` (default: the current folder) and derives a key from the hash of its resolved
   path.
2. It takes an exclusive, non-blocking OS lock on `<state_dir>/studio-<key>.lock` (`flock` on Linux and macOS,
   `msvcrt.locking` on Windows). The OS releases the lock when the process ends, also after a crash, so a stale lock
   never blocks the next start.
3. **Lock taken:** this process becomes the server. It first removes any info file left by a crashed instance, since
   that file may name a port that another program now uses. It then listens on `127.0.0.1` (a free port chosen by
   the OS, or `--port N`) and binds, without listening, `[::1]` on the same port (see *Loopback addresses*). It runs
   Uvicorn with one worker, and once it accepts connections writes `<state_dir>/studio-<key>.json` (mode 0600,
   written atomically) with PID, port, instance ID and a random control token. Then it prints the URL and opens the
   system browser (unless `--no-browser`).
4. **Lock held by another process:** a server for this folder runs already. The command reads the info file, checks
   that `GET /api/health` on that port answers with the same instance ID, and makes the server prove that it knows
   the control token (see *Control endpoint*). Only then does it ask for a launch URL over
   `POST /api/control/launch`. It opens that URL only if it starts with `http://127.0.0.1:<port>/` of that instance.
   It never starts a second server.

The state directory is `platformdirs.user_state_dir("tabrion-studio")` (override: `--state-dir` or
`TABRION_STUDIO_STATE_DIR`). It is created with mode 0700 and files with mode 0600, and symlinks are refused. Studio
refuses an existing directory, and never silently repairs it, in these cases:

- on POSIX, another user owns it, or the group or others may write to it (review F-10). Read bits for others are
  removed, since no secret has been written yet;
- on POSIX, the lock file is not a regular file or belongs to another user;
- on Windows, the directory or a folder between it and the user profile is a junction or another reparse point;
- on Windows, the directory is outside `%USERPROFILE%` and `%LOCALAPPDATA%`, and its owner or ACL is not private: an
  allowing entry names a principal other than the user, SYSTEM, Administrators, CREATOR OWNER or OWNER RIGHTS, or there is a NULL
  DACL (review F-07). The default directory lies in `%LOCALAPPDATA%`, whose inherited ACL is private.

## Control endpoint

`POST /api/control/{launch,shutdown}` needs `Authorization: Bearer <control token>`. The token is only in the 0600
info file. A web page in the browser cannot read that file and cannot send the header cross-origin without a CORS
preflight, which the server never grants. The CLI's control client never uses an HTTP proxy. With STU-003 the launch
URL carries the one-time launch token in its fragment (STU-ADR-0002); the control channel stays as it is.

**Server proof (review F-01).** Between a crash and the next start, or in the moment of a shutdown, an info file can
name a port that another local program has taken. That program can copy the instance ID into its health answer. So
before the client sends the token, it sends a random 128-bit nonce to `GET /api/control/proof?nonce=<hex>`, and the
server answers `HMAC-SHA256(token, "tabrion-studio-proof:" + nonce)`. The client compares the answer in constant time
and stops with an error if it is wrong (`ImpostorError`). The token is never sent, and no URL from that program is
opened. A proof cannot be replayed, because each request uses a new nonce. Defense in depth closes the window from
both sides as well:

- the info file is removed as soon as the lock is taken;
- the server removes it at the start of its shutdown, before Uvicorn closes the socket, and again on every way out;
- the client opens a launch URL only if it starts with `http://127.0.0.1:<port>/` of that instance and contains no
  whitespace or backslash.

## Loopback addresses (review F-08)

Browsers resolve `localhost` to `::1` first. Studio therefore binds `[::1]:<port>` too (`IPV6_V6ONLY`, not
listening): no other program can then listen there while Studio runs, and a connection to it is refused, so a
browser that tries `localhost` falls back to `127.0.0.1`. With a free port, Studio picks another one if `[::1]` has
that port in use; with `--port N`, it refuses to start. On a host without IPv6 loopback, nothing can listen on
`[::1]` anyway, and the IPv4 listener is used alone. On Windows both sockets use `SO_EXCLUSIVEADDRUSE`. Studio itself
prints and opens only `http://127.0.0.1:<port>/` URLs, never `localhost` ones; the Host allowlist of STU-003 decides
whether `localhost` is accepted at all (see docs/TODO.md).

## Stopping

| Way | Behavior |
|---|---|
| Ctrl+C (SIGINT), SIGTERM, SIGHUP (terminal closed) on POSIX | graceful Uvicorn shutdown, exit code 0 |
| Ctrl+Break, and closing the console window (CTRL_CLOSE), on Windows | both arrive as SIGBREAK: graceful shutdown, exit code 0 |
| `tabrion-studio --stop [PROJECT]` | `POST /api/control/shutdown` (after the server proof), then waits until the lock is free |
| idle timeout (default 30 min, `--idle-timeout 0` = off) | see *Idle timeout* |

The server removes its info file (only if it is still its own) as soon as the shutdown starts and again on every way
out, then releases the lock (review F-04). Closing database engines and rolling back open transactions join this path
with STU-009.

**Residual risk:** SIGKILL, a crash, a power cut or Windows ending a console process that does not finish within
its CTRL_CLOSE grace period (about 5 s) leave the info file behind. Its token is dead, and the next start removes the
file as soon as it takes the lock; until then, the server proof keeps any program on that port from receiving a
token.

## Idle timeout (review F-06)

Only two kinds of request count as activity:

- an authenticated control request (valid Bearer token);
- `GET /api/heartbeat` with `Sec-Fetch-Site: same-origin`. A browser sets that header itself, and a page cannot
  forge it, so only a page of Studio's own origin keeps Studio alive. The open tab sends a heartbeat at once and then
  every 60 s; the SSE heartbeat of STU-011 replaces it.

Health checks, static files, failed control requests, heartbeats from other sites (`cross-site`, `same-site`) and
requests without the header (other local programs) never postpone the idle exit. Once STU-003 exists, requests with
a valid session count as well.

## Start time

The server stack (FastAPI, Uvicorn) is imported only by the process that serves; a second start that reuses the
running instance does not load it. The process tests measure from spawning the process to the first health answer
(STU-002 budget: under 2 s).

## Room for later work

The app factory (`tabrion_studio.app.create_app`) is where the security middleware of STU-003, the problem-JSON
handlers of STU-004 and the API routers (`tabrion_studio.api` → `services` → `profiles`/`security`, 05 section 3.2)
plug in. Write paths, including the later Studio 1.1 Schema Designer (change request 14 section 3.5), sit behind a
server-side flag (STU-ADR-0003); nothing in the process model assumes a read-only server.
