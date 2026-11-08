# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- Project skeleton: Python package `tabrion_studio` (hatchling, ruff, mypy strict, import-linter) and the React 19 /
  TypeScript 5.9 / Vite 8 frontend in `frontend/` (eslint, prettier, strict `tsc`, vitest) with a placeholder page
  (STU-001).
- `tabrion-studio` command and process model: loopback-only server on a free port, one instance per project folder
  (a second start reuses it), idle exit after 30 minutes, `--stop`, clean shutdown on Ctrl+C and SIGTERM (STU-002).
- CI (`ci.yml`, `ci-ok.yml` with the commit status `ci-ok`, `nightly.yml`) on Linux, macOS and Windows; the wheel
  build hook that requires the built frontend; a wheel smoke test without Node (STU-018).
- License policy check with negative probes and CycloneDX SBOMs for the Python runtime and the frontend (STU-031).

### Security

- Review of PR #1 (`stu-002-018-review.md`): the control client makes the server prove the control token (HMAC over a
  client nonce) before it sends the token, and opens only launch URLs of that instance; stale info files are removed
  when the lock is taken and at the start of a shutdown (F-01). SIGHUP and Windows CTRL_CLOSE stop Studio cleanly
  (F-04). Only authenticated control requests and same-origin heartbeats (`GET /api/heartbeat`) reset the idle timer
  (F-06). Unsafe state directories are refused: group- or world-writable or foreign-owned on POSIX (F-10), outside
  the profile without a private ACL or behind a junction on Windows (F-07). `[::1]` on the same port is held (F-08).
  ci-ok reports NOT OK for unreviewed changes to `.github/workflows/**` or `.github/scripts/**` (label
  `workflow-change-reviewed`, F-03) and retries the status post, failing loudly (F-11).
