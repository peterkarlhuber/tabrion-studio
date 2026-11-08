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
3. **Lock taken:** this process becomes the server. It binds `127.0.0.1` (a free port chosen by the OS, or
   `--port N`), runs Uvicorn with one worker, and once it accepts connections writes
   `<state_dir>/studio-<key>.json` (mode 0600, written atomically) with PID, port, instance ID and a random control
   token. Then it prints the URL and opens the system browser (unless `--no-browser`).
4. **Lock held by another process:** a server for this folder runs already. The command reads the info file, checks
   that `GET /api/health` on that port answers with the same instance ID, asks the server for a launch URL over
   `POST /api/control/launch` and opens it. It never starts a second server.

The state directory is `platformdirs.user_state_dir("tabrion-studio")` (override: `--state-dir` or
`TABRION_STUDIO_STATE_DIR`); it is created with mode 0700, files with 0600, and symlinks are refused.

## Control endpoint

`POST /api/control/{launch,shutdown}` needs `Authorization: Bearer <control token>`. The token is only in the 0600
info file. A web page in the browser cannot read that file and cannot send the header cross-origin without a CORS
preflight, which the server never grants. The CLI's control client never uses an HTTP proxy. With STU-003 the launch
URL carries the one-time launch token in its fragment (STU-ADR-0002); the control channel stays as it is.

## Stopping

| Way | Behavior |
|---|---|
| Ctrl+C (SIGINT), SIGTERM, Ctrl+Break on Windows | graceful Uvicorn shutdown, exit code 0 |
| `tabrion-studio --stop [PROJECT]` | `POST /api/control/shutdown`, then waits until the lock is free |
| idle timeout (default 30 min, `--idle-timeout 0` = off) | every request counts as activity; the open tab sends a heartbeat (`GET /api/health`) every 60 s; the SSE heartbeat of STU-011 replaces it |

On the way out the server removes its info file (only if it is still its own) and releases the lock. Closing
database engines and rolling back open transactions join this path with STU-009.

## Start time

The server stack (FastAPI, Uvicorn) is imported only by the process that serves; a second start that reuses the
running instance does not load it. The process tests measure from spawning the process to the first health answer
(STU-002 budget: under 2 s).

## Room for later work

The app factory (`tabrion_studio.app.create_app`) is where the security middleware of STU-003, the problem-JSON
handlers of STU-004 and the API routers (`tabrion_studio.api` → `services` → `profiles`/`security`, 05 section 3.2)
plug in. Write paths, including the later Studio 1.1 Schema Designer (change request 14 section 3.5), sit behind a
server-side flag (STU-ADR-0003); nothing in the process model assumes a read-only server.
