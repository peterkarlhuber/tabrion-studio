# STU-ADR-0003: Writes behind a server-side flag until G-S2

**Status:** proposed; E-S1 decided (2026-11-07 23:26Z): the code may be written, enabling it needs G-S2. Studio 1.0
is complete without the Data Editor (STU-026 is a separate, gated package). **Amended by
[STU-ADR-0005](0005-crdbx-write-and-admin-gates.md) (CR-DBX, 2026-11-13):** one flag and one gate per area (G-S2 Data
Editor, G-S3 Schema Designer, G-S4 Database Manager), and a refused write answers 400/405, with 403 only for the write
mode.

## Decision

- Write code is built and tested behind a server-side flag (`writes_enabled=false`, not switchable from the
  frontend) and enabled only after G-S2.
- After G-S2: writing only for profiles with `environment != prod` (no code path for `prod` in 1.0); unlocking per
  connection and session with the typed profile name, expiring after 15 minutes of inactivity; changes collected as
  parameterized statements with an optimistic lock, diff preview and one transaction; every statement must affect
  exactly one row, otherwise rollback; destructive classes need an extra confirmation; Studio never changes a schema
  on its own; a local change log without values.

## Consequences

Without G-S2 there is no executable write path; a test calls every write endpoint with the flag off and expects
~~403/404~~ 400 or 405, and 403 only for the write mode (owner decision of 2026-11-13 on STU-054; STU-ADR-0005). ~~The
later Studio 1.1 Schema Designer (change request 14 section 3.5) uses the same flag and gate model.~~ The Schema
Designer is Studio 1.2 with its own flag and gate G-S3, and the Database Manager Studio 1.3 with G-S4 (STU-ADR-0005).
