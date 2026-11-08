# TODO: tabrion-studio

> Open work for this repository: release blockers, owner actions, the open Studio work packages, temporary
> measures and technical debt. The program backlog is the source of truth for scope and acceptance criteria
> (planning documents 03, 05, 07, 08 and 09; program status in `tabrion/docs/program/STATUS.md`, K-12). "TABRION
> <WP-ID>" names a work package of that backlog.
>
> **Last full review:** 2026-11-08 against branch `claude/project-thread-rdo6g3-stu-conditions` (the conditions of
> `stu-004-review.md` and `stu-006-review.md`), before that against branch `claude/project-thread-rdo6g3-stu006` (TABRION STU-006:
> configuration, profiles and project trust, U-1) and `claude/project-thread-rdo6g3-stu004` (TABRION STU-004: Problem
> JSON and logging, PR #7); before that against branch `claude/project-thread-rdo6g3` (the review recommendations U-1..U-5
> of `stu-005-051-review.md`; PR #5: STU-005 and STU-051 with S-1 and S-4 of `stu-003-review.md`; PR #4: STU-003 and
> R-1..R-7 of `stu-002-018-review.md`; PR #1: STU-001, STU-002, STU-018, STU-031).

## Summary

| Item | Priority | Status |
|---|---|---|
| TS-001 add the tabrion dependency with STU-009 | P1 | Blocked (tabrion v0.1.0) |
| OA-001 make `ci-ok` a required check; check the ci-ok run link before every merge | P0 | Open (owner) |
| OA-002 independent review of the process model | P1 | Accepted (re-review at 988bc9d) |
| OA-003 create the label `workflow-change-reviewed` | P1 | Open (owner) |
| OA-004 independent review of STU-003 (access protection) | P0 | Accepted (`stu-003-review.md`, PR #4 merged) |
| OA-005 independent review of STU-005/STU-051 (app shell, dialogs, S-1, S-4) | P1 | Accepted (`stu-005-051-review.md`, PR #5 merged) |
| OA-006 independent workflow-change review of the `e2e` CI job (U-3), then the label `workflow-change-reviewed` | P1 | Accepted (`stu-follow-up-6-review.md`, PR #6 merged) |
| OA-007 independent review of STU-004 (Problem JSON, log redaction) | P1 | Accepted (`stu-004-review.md`; C-1 done in PR #9, `stu-conditions-rereview.md`) |
| OA-009 independent review of STU-007 (secret management, C-3) | P0 | Accepted with conditions (`stu-007-review.md`, PR #10 merged); C-1 (M-1) and I-1..I-3 done in the STU-008 PR, I-4, I-5 and L-1 are TD-010 |
| OA-010 independent review of STU-008 (Studio writes its own profiles and settings) | P1 | Accepted (`stu-008-review.md`, PR #11 merged); L-1..L-3 and I-1..I-4 done in the STU-008 follow-up PR |
| OA-008 independent review of STU-006 (project trust, D-01, U-1) | P1 | Accepted (`stu-006-review.md`; C-1, C-2 done in PR #9, `stu-conditions-rereview.md`; C-3 done in the STU-007 PR) |
| STU-007 binding requirement from the review (C-3: the reader of `file:` secrets) | P0 | Done (STU-007 PR; `read_secret_file`) |
| STU-006 binding requirement from the review (U-1: trust dialog D-01 must not mislead) | P1 | Done (STU-006 PR; closes with OA-008) |
| STU-003 binding requirements from the review (F-02, F-05, F-08, F-09) | P0 | Done (PR #4) |
| TD-001 layer contract for `api`/`services` | P3 | Open |
| TD-002 sandboxed browsers cannot open the redirect file (review S-4) | P2 | In progress (hint done; fix open) |
| TD-003 Windows: DACL check inside the profile (review R-6) | P3 | Open |
| TD-004 fork pull requests in the workflow-change gate (review R-7) | P3 | Open |
| TD-005 spent launch URL in the browser history (review S-2) | P3 | Accepted risk (documented) |
| TD-006 who may set the label `workflow-change-reviewed` (review S-3) | P3 | Open |
| TD-007 development proxy and real-browser launch checks (review S-5) | P3 | Open (documented; QA-9) |
| TD-008 add a windows-latest CI job for the T-2 checks (review C-2) | P1 | Open (needs a workflow-change review) |
| TD-009 STU-004 review: remaining Low and Info items (L-2 rest, I-1..I-5, re-review R-2) | P3 | Open |
| TD-010 STU-006 and STU-007 reviews: remaining Low and Info items (T-1 rest, I-1, I-3..I-5; STU-007 I-4, I-5; R-1, T-4 rest, T-5 and STU-007 L-1 done) | P2 | Open |
| TD-011 STU-008 parts that wait for STU-009 or a later package (connection test, Connect, URL paste, file dialog, `openapi in sync` job) | P1 | Open |

Plus the open Studio work packages of M6 and the cross-repository checks in the tables below.

**Release blockers:** nothing is released from this repository before G-R (every public version needs the owner's
approval, plus a human security review for Studio, E-QA2). There is no release workflow yet.

Priorities: **P0** blocks a gate, a merge or a release; **P1** on the critical path of M6; **P2** in scope, schedule
soon; **P3** minor gap or debt. Statuses: `Open`, `In progress` (PR linked), `Blocked` (names the blocker),
`Needs decision`, `Accepted risk`.

## Temporary measures

### TS-001 · Add the tabrion dependency with the first API use (TABRION STU-009, RC-8)

- **Priority:** P1 High · **Status:** Blocked (tabrion v0.1.0, TABRION TAB-040) · **Refs:** RC-8, E-VER, 05 §3.2
- **What:** nothing in Studio uses the Tabrion API yet, so `pyproject.toml` has no tabrion dependency (no git pin, no
  pre-release specifier on master, RC-8; enforced by `tests/security/test_dependencies.py`).
- **Next step:** add `tabrion[postgres,sqlalchemy,migrations]>=0.1,<0.2` with the first WP that uses the Tabrion API
  (STU-009), after tabrion v0.1.0.
- **Also with STU-009 (binding, L-1 of `stu-008-review.md`):** every parameter Studio passes to the driver (later
  `sslrootcert`, `require_auth`, options) must be in `schema.TARGET_FIELDS`, the secret fingerprint, and a
  connection is built from `Profile` only, never from unknown keys.
- **Also with STU-009 (from STU-007):** show connection URLs only through `tabrion.safe_url()`, resolve passwords
  only through `Configuration.resolve_secret(id)` when connecting, and open project SQLite files only through
  `ProjectConfig.database_file` (review T-1).
- **Also with STU-009 (from STU-004):** replace the mirror `tabrion_studio.problems.DB_KINDS` with
  `tabrion.errors.Kind`, build `db.<kind>` problems from `tabrion.errors.classify` and `safe_message` only, and add
  `tabrion.errors.DbErrorRedactionFilter` to the log handlers next to Studio's `RedactionFilter`
  (`docs/architecture/errors-and-logging.md`, "Database errors").

## Owner actions

### OA-001 · Make `ci-ok` a required check (TABRION STU-018, GOV-3)

- **Priority:** P0 Critical · **Status:** Open · **Refs:** CI-FAST, 05 §1 (branch protection)
- **What:** `ci-ok.yml` runs from the default branch only (`workflow_run`), so it cannot judge the pull request
  that adds it. Once it is on master it posts the commit status `ci-ok` on every pull request head SHA.
- **Next step (owner):** after the merge, protect `master` (no force push, no deletion, no required reviews per
  the agent flow) and add the status check `ci-ok` as required. Optionally list the CI job names as required checks
  for the first pull request.
- **Before every merge (review R-1, TABRION STU-003 PR):** the latest `ci-ok` status on the head SHA must link
  (`target_url`) to a run of the ci-ok workflow for that SHA, and the run ID at the end of its description
  (`[ci-ok run <id>]`) must be that run. A workflow in any other branch could forge the status, so this applies to
  every pull request, not only to workflow changes (`docs/development/ci.md`).

### OA-002 · Independent review of the process model (TABRION STU-002, 07 §6)

- **Priority:** P1 High · **Status:** Accepted (re-review at 988bc9d) · **Refs:** 07 §6 review model, STU-ADR-0002,
  `tabrion-planung/reviews/stu-002-018-review.md`
- **What:** the independent review of PR #1 (at 4925486) covered the lock and info files, the control token, the
  loopback bind and the ci-ok gate. It came back ACCEPTED WITH CONDITIONS; the re-review at 988bc9d ACCEPTED, with
  condition C-3 (the STU-003 binding requirements) and the recommendations R-1..R-7.
- **Done in PR #1:** F-01 (server proof before the token is sent, launch URL check, stale info file removed when the
  lock is taken and at the start of a shutdown, regression tests with a fake server), F-04 (SIGHUP, CTRL_CLOSE, info
  file removed on every exit path), F-06 (only authenticated control requests and same-origin heartbeats reset the
  idle timer), F-07 (Windows: private ACL outside the profile, junctions refused), F-08 (`[::1]` held on the same
  port), F-10 (unsafe state directory and lock file refused), F-11 (status post retried, job fails loudly) and F-03
  (workflow-change gate with the label `workflow-change-reviewed`, CODEOWNERS as a recommendation).
- **Moved to STU-003:** F-02, F-05, F-08 (Host allowlist part) and F-09; see *STU-003 binding requirements*.
- **Recommendations of the re-review (TABRION STU-003 PR #4):** R-1 (check the ci-ok run link before every merge,
  `docs/development/ci.md`, OA-001), R-2 (the label counts only if set after the latest push), R-3 (all of
  `.github/` is guarded, minus the templates) and R-4 (proof and token on one keep-alive connection) are done; R-5
  is done with STU-003 (the heartbeat needs a session, the Host allowlist covers it, browser versions are in
  `docs/architecture/access-protection.md`); R-6 and R-7 are TD-003 and TD-004.

### OA-003 · Create the label `workflow-change-reviewed` (TABRION STU-018, review F-03)

- **Priority:** P1 High · **Status:** Open (owner) · **Refs:** `docs/development/ci.md` (workflow-change gate)
- **What:** ci-ok reports NOT OK for every pull request that changes a file under `.github/` (after PR #4; before
  it: `.github/workflows/**` and `.github/scripts/**`) without this label. After PR #4 the label counts only if it
  was set after the latest push (review R-2). Only writers can set labels.
- **Next step (owner):** create the label once (Issues, Labels). Set it only after the owner or an independent
  review has read the change under `.github/`, then re-run ci-ok. PR #4 itself changes `.github/scripts/**` and
  `.github/workflows/**`, so it stays NOT OK until the label is set. Optional: turn on "require review from code owners" for
  `.github/CODEOWNERS` once a second maintainer exists (it would block every agent pull request today).

### OA-004 · Independent review of STU-003 (TABRION STU-003, 07 §6, G-S1)

- **Priority:** P0 Critical · **Status:** Accepted (PR #4 merged) · **Refs:** 07 §6 (packages with a network or credential
  surface), condition C-3 of `stu-002-018-review.md`, `docs/architecture/access-protection.md`
- **What:** an independent review agent checks the access protection against the binding requirements below and the
  threat model (T-01, T-02, T-03, T-07, T-15), including the workflow-change gate changes (R-2, R-3), before the
  merge.
- **Result:** `stu-003-review.md` (planning folder, `reviews/`): ACCEPTED, no High or Medium finding; the `.github/`
  changes were accepted as a workflow-change review. Recommendations: S-1 is done with STU-005 (on 401 the page says
  "session ended, run `tabrion-studio` again" and stops the heartbeat; the cookie-tossing residual risk is in
  `docs/architecture/access-protection.md`), S-4 is TD-002 (raised to P2, hint done), S-2, S-3 and S-5 are TD-005,
  TD-006 and TD-007.

### OA-005 · Independent review of STU-005 and STU-051 (07 §6)

- **Priority:** P1 High · **Status:** Accepted · **Refs:** `docs/architecture/frontend.md`, review S-1 and S-4,
  `tabrion-planung/reviews/stu-005-051-review.md`
- **What:** the pull request adds the app shell, the design system, de/en, the status displays and the dialogs, and
  touches the access surface in three small places: the backend serves `index.html` for the view paths
  `/connections` and `/settings` (`CLIENT_ROUTES`, same middleware and headers), the page reacts to a lost session
  (S-1), and the command runs `xdg-settings` to detect a sandboxed browser (S-4, fixed arguments, 2 s timeout, no
  shell). No `.github/` file changes, so the workflow-change label is not needed.
- **Result:** `stu-005-051-review.md`: ACCEPTED (no security regression, 0 CSP violations in a real browser,
  untrusted strings render as text). Recommendations: U-1 is the STU-006 binding requirement below; U-2 (escape the
  action ID in the dialog's initial-focus selector), U-3 (the `e2e` CI job), U-4 (HEAD on the view paths) and U-5
  (the Chromium build mismatch, `docs/development/building.md`) are done in the follow-up pull request (OA-006).

### OA-006 · Independent workflow-change review of the `e2e` job (review U-3)

- **Priority:** P1 High · **Status:** Accepted (`stu-follow-up-6-review.md`, PR #6 merged) · **Refs:**
  `docs/development/ci.md` (workflow-change gate), `stu-005-051-review.md` U-3
- **What:** the follow-up pull request adds the job `e2e` to `.github/workflows/ci.yml` and to the mandatory checks in
  `.github/ci-manifest.json`. It builds the wheel, installs it into a fresh environment, installs the Chromium build of
  the locked Playwright (`npm run e2e:install`) and runs every Playwright suite except the screenshots; a final step
  fails the job if a required suite (`a11y`, `backend`, `contrast`, `dialogs`, `status`) did not pass or was skipped.
  Actions are pinned by SHA; `ci_ok.py` is unchanged. Because the pull request changes `.github/`, ci-ok reports
  NOT OK until the label is set.
- **Result:** `stu-follow-up-6-review.md`: ACCEPTED, including the workflow-change review of the `.github/` diff.
  Info notes W-1 (`retention-days` for the failure artifact) and W-2 (the browser download is not hash-pinned) need
  no change now.

### OA-007 · Independent review of STU-004 (TABRION STU-004, 07 §6)

- **Priority:** P1 High · **Status:** Accepted with conditions (`stu-004-review.md`, PR #7 merged) · **Refs:** 07 §6
  (Studio packages with a credential surface), T-05, SQ-04, `docs/architecture/errors-and-logging.md`
- **What:** the pull request turns every error answer into Problem JSON and adds the redacting, rotating logs and the
  request log. An independent review agent checks that no answer can carry an exception message, a path, a stack
  trace or request data, that the redaction holds for every logger (Studio, Uvicorn, asyncio) and every field, and
  the file handling of the log folder (0700, 0600, symlinks). No `.github/` file changes, so the workflow-change label
  is not needed.
- **Result:** ACCEPTED WITH CONDITIONS, no High finding. **C-1 (L-1, Medium) is done in the conditions PR**
  (PR #9, merged; re-review `stu-conditions-rereview.md`: accepted): records of the `asyncio` logger keep only a known fixed
  first line (no `repr` of a task, future or callback), a formatted traceback in a message is cut at
  `Traceback (most recent call last):`, Studio's lifespan logs its exception with `exc_info` (frames only), and an
  exception passed as a `%s`/`%r` argument formats as its type. Canary tests: `test_canary.py`
  (`test_asyncio_reprs_never_reach_a_log_line`, `test_a_failing_lifespan_of_any_app_logs_no_traceback_text`,
  `test_a_failing_studio_startup_is_logged_with_frames_only`) and the real process in `test_log_process.py`
  (`test_a_failing_startup_in_a_real_process_leaks_no_exception_text`). Also done: L-2 (key suffixes, `Authorization`,
  URL passwords up to the last `@`; all patterns bounded, linear time) and L-3 (20 + 20 frames per exception, 32 KB
  per record). The rest is TD-009.

### OA-008 · Independent review of STU-006 (TABRION STU-006, 07 §6)

- **Priority:** P1 High · **Status:** Accepted with conditions (`stu-006-review.md`, PR #8 merged) · **Refs:** 07 §6
  (credential surface: `env:`/`file:` references), T-08, `docs/architecture/configuration.md`, U-1 below
- **What:** project trust fails closed (links, `file:` outside, digest bound to the bytes on disk, damaged records),
  no value is echoed from a profile file, and D-01 meets U-1a..U-1d. No `.github/` file changes.
- **Result:** ACCEPTED WITH CONDITIONS, no High finding. Done in PR #9 (merged; re-review
  `stu-conditions-rereview.md`: accepted):
  - **C-1 (T-1):** `trusted` only if the digest matches **and** the current review equals the confirmed one;
    otherwise `changed` with the diff. `ProjectConfig.database_file(name)` re-checks a trusted SQLite profile when its
    file is opened; **STU-009 must call it and open the path it returns** (`test_project_trust.py`,
    `test_a_folder_that_becomes_a_link_out_of_the_project_needs_a_new_confirmation`,
    `test_the_database_file_is_rechecked_when_it_is_opened`).
  - **C-2 (T-2):** `config.network_path` for UNC, device, drive-relative, `file://host` and `/net`, `/Network` values;
    containment decided as text (`config/locations.py`) before any file-system access; no `Path.resolve()` on a value
    from the project file (`test_config_locations.py` with `PureWindowsPath` and `PurePosixPath`;
    `test_network_paths_are_refused_before_any_file_system_access`; on Windows
    `test_windows_never_resolves_a_unc_path_from_the_project_file`, which patches `nt._getfinalpathname` and runs in
    the `windows-latest` leg of the test matrix). The dedicated Windows CI job is TD-008.
  - **T-3:** deeply nested TOML is `config.too_deep`; a damaged or deeply nested trust record means untrusted; an
    unexpected load error makes the project `invalid` and `/api/profiles` still lists the user's profiles.
  - **T-4:** loopback by `ipaddress` plus the literal `localhost` (and socket folders), so `127.evil.example` gets
    `verify-full`.
  - **I-2:** D-01 shows "Unknown keys removed", says so after `studio.trust_stale`, and an `invalid` file gets a
    notice with its issues (de/en).
  - **C-3** is the STU-007 binding requirement below. The other Low and Info items are TD-010.

### OA-009 · Independent review of STU-007 (TABRION STU-007, 07 §6)

- **Priority:** P0 Critical · **Status:** Accepted with conditions (`stu-007-review.md`; PR #10 merged as master
  `0af1831`) · **Refs:** 07 §6 (credential surface), T-05, SQ-04,
  03 §4.4, `docs/architecture/secrets.md`, C-3 below
- **What:** an independent review agent checks that no secret reaches an answer, a log or an error, that there is no
  plain-text fallback without a keyring, that the `file:` reader meets C-3 at read time (no `expanduser`,
  containment, `O_NOFOLLOW` per component, owner, 0600, one link, no FIFO block), and the new routes behind the
  access middleware. No `.github/` file changes.
- **Result:** no High finding. Done in the STU-008 pull request, as its first commit:
  - **C-1 (M-1):** a stored secret (keyring or session) is bound to the connection target. The key is
    `user:<name>:<fingerprint>` or `project:<project key>:<name>:<fingerprint>`, where the fingerprint is 16 hex
    digits of the SHA-256 of engine, host, port (none = 5432), database, user and path
    (`schema.target_fingerprint`). A profile that keeps its name but points elsewhere has no stored secret
    (`studio.secret_missing`, the page asks again), and D-01 lists it under "Saved password not used"
    (`diff.connections.retargeted`). Test: `test_a_stored_password_does_not_follow_a_retargeted_project_profile`
    (the reproduced attack: `prod`, host changed, one re-trust).
  - **I-1:** secrets of 4 to 7 characters are registered too, in a set that is never evicted; shorter ones are not
    (redacting every "ab" would make the log unreadable). **I-3:** one leading BOM is removed from a `file:` secret.
  - **I-2:** the FIFO test fails after 5 s instead of hanging (`SIGALRM`); a deterministic link swap between the
    checks and the open (file and folder; kills K5 and K6); `env:`, session and short values are redacted; an API
    test resolves a project `file:` secret with the project and configuration folders as roots (K16); every Studio
    process a test starts uses the null keyring backend (`tests/support/studio.NO_KEYRING`).
  - **I-4, I-5 and L-1** are TD-010.

## STU-007 binding requirement (review C-3 of `stu-006-review.md`)

**Status: done in the STU-007 pull request** (`src/tabrion_studio/config/secrets.py`, `read_secret_file`; tests in
`tests/unit/test_secrets.py`; `docs/architecture/secrets.md`). TABRION STU-007 is accepted only if it meets it. The schema accepts `file:PATH` password references,
and today `file:~/...` passes, because it is read as project-relative (`<project>/~/...`): nothing calls
`expanduser`. The checks at load time (`config/locations.py`, `_check_secret_files`) only decide whether the
reference may be listed. The reader that STU-007 adds must, **at read time**:

- **never call `expanduser`** (or `expandvars`), **never URL-decode and never Unicode-normalize** the value
  (`%5C%5Chost`, `∕∕host` and `＼＼host` stay literal names inside the project; `stu-conditions-rereview.md`): the
  path is the text of the reference, relative to the project folder (or absolute);
- **re-check containment** with `config.locations.contained` (inside the project folder or the user's configuration
  folder, as text first, then the link walk), immediately before the open, and again on the exact path that is
  opened;
- **open with `O_NOFOLLOW`** (plus `O_NONBLOCK` until `S_ISREG` is checked, so a FIFO cannot block the server) and
  check the opened file with `fstat`: a regular file, `st_nlink == 1`, **owned by the current user and mode 0600**
  (no group or other bit; POSIX), the same device and inode as the checked path; on Windows, no reparse point and a
  private DACL (TD-003);
- read at most a small fixed size, strip one trailing newline, register the value with
  `tabrion_studio.logs.register_secret` and never log, return or echo it; every refusal is a code without the path's
  content.

Most `.env` and `.git/config` files are 0644, so the 0600 rule also blocks the case of review T-5 where a project
names the user's own secret inside the project folder.

## STU-006 binding requirement (review U-1 of PR #5)

**Status: done in the STU-006 pull request** (`docs/architecture/configuration.md`, section *The trust dialog D-01*):
U-1a list items with one `<code>` each, U-1b escapes `\uXXXX` (`frontend/src/lib/visible.ts`), U-1c 64 characters and
8 entries with "Show full value" and "and N more", U-1d the diff from the trust record. Tests: `StudioDialogs.test.tsx`
(one per point, hostile names), `e2e/dialogs.spec.ts` and `e2e/a11y.spec.ts` (`dialog-d01-hostile`),
`e2e/backend.spec.ts` (real server). It closes with the review OA-008.

TABRION STU-006 wires the trust dialog D-01 (`TrustProjectDialog`, `frontend/src/components/dialogs/StudioDialogs.tsx`)
to the project file. Today it joins the connection names, hosts and file references with `", "` into one sentence or
one `<code>`. All of them come from an untrusted project file (AT4, T-08), so a name such as `parts-dev, safe
(checked)`, or one with a newline or bidi control characters, can make the list look different from what it is. No
script runs (review P-2); the risk is UI spoofing of the trust decision. STU-006 is accepted only if:

- **U-1a, one item per entry.** Names, hosts and files are rendered as lists, each entry its own list item with its
  own `<code>`; nothing is joined into one string.
- **U-1b, control and bidi characters are visible.** C0 and C1 control characters (newlines and tabs included) and the
  bidi controls U+202A..U+202E, U+2066..U+2069 (plus U+200E, U+200F, U+061C) are shown as visible escapes (for
  example `\u202E`), never interpreted; the escaped form is also what the tests assert.
- **U-1c, length cap.** Each entry is cut to a fixed maximum length with a visible marker (and the full value
  available on request), and the number of entries shown is capped with "and N more"; the dialog's counts always
  report the real totals.
- **U-1d, the T-08 diff.** When the project file changed since it was trusted (`titleChanged`), the dialog shows the
  diff that T-08 requires (added, removed and changed connections, hosts and files) instead of only the new list.
- **Tests:** a Vitest test per point with hostile names (commas, newlines, U+202E, overlong values, many entries) and
  an e2e or axe check of the dialog with them.

## STU-003 binding requirements (review of PR #1)

TABRION STU-003 is accepted only if it meets these requirements of `stu-002-018-review.md` (condition C-3). PR #4
implements each of them with tests (`docs/architecture/access-protection.md`, section *Tests*); they close with the
merge after OA-004.

- **F-02, launch token not in a process argv.** Done: the browser opens the `file://` path of a 0600 redirect file
  (`<state_dir>/studio-<key>-launch-<random>.html`, meta refresh to `http://127.0.0.1:P/launch#TOKEN`), deleted when
  the token is redeemed, after 120 s, at shutdown and at the next start. `--print-url` is the explicit opt-in. Tests:
  `tests/security/test_launch.py::test_browser_argv_contains_no_token` (real process, fake `$BROWSER` that records
  its argv), `test_browser_is_opened_with_a_redirect_file`, `test_second_start_opens_a_new_redirect_file`,
  `test_print_url_is_the_explicit_opt_in`.
- **F-05, Host and Origin checks on every route.** Done: `AccessMiddleware` applies the Host allowlist to every route,
  including `/api/control/*`, `/api/control/proof`, `/api/heartbeat` and `/api/health`; the control endpoints refuse
  any request with `Origin` or a `Sec-Fetch-*` header. Tests: `tests/security/test_access.py::
  test_host_allowlist_on_every_route`, `test_control_rejects_browser_headers`, `test_foreign_origin_is_refused`.
- **F-08, `localhost` and `::1`.** Done: the allowlist accepts `127.0.0.1:<port>` only (`localhost` and `[::1]` are
  refused whether or not the `[::1]` guard is held; a failed IPv6 bind stays non-fatal). Test:
  `tests/security/test_launch.py::test_no_localhost_url_is_printed_or_opened`.
- **F-09, cookies are not port-scoped.** Done: every request with a session needs `X-Tabrion-CSRF`, whose value lives
  only in the page's `sessionStorage`; the cookie name carries the port. Tests:
  `test_cookie_alone_is_useless_without_csrf_header`, `test_csrf_value_never_travels_in_a_cookie`,
  `test_a_tossed_cookie_does_not_hide_the_real_session`.
- **F-06, follow-up (and R-5).** Done: every request with a valid session counts as activity; the heartbeat needs the
  session, so a forged `Sec-Fetch-Site` no longer keeps Studio alive. Tests: `tests/unit/test_app.py::
  test_requests_that_do_not_count_as_activity`, `test_heartbeat_with_a_session_counts_as_activity`.

## Open work packages (M6, Studio MVP 0.1)

| WP | Title | Priority | Depends on | Note |
|---|---|---|---|---|
| TABRION STU-003 | Access protection (launch token, cookie, Host/Origin, CSP, `no-store`) | P0 | STU-002 | Done (PR #4, review accepted) |
| TABRION STU-004 | Problem JSON and logging | P1 | STU-001, TAB-038 | Done (PR #7, OA-007 accepted; C-1 done in PR #9): Problem JSON for every error, redacting rotating logs, request log without query strings; canary tests STU-T-008/-009. No tabrion dependency (RC-8): database kinds mirrored until STU-009 (TS-001) |
| TABRION STU-005 | App shell, design system, i18n de/en | P1 | STU-001 | Done (PR #5, review accepted): shell, `--tb-` tokens, de/en, logo; axe without serious/critical (jsdom in `frontend unit`, Chromium with contrast in `e2e`) and the token contrast test light/dark |
| TABRION QA-9 | E2E framework (Playwright + axe) | P1 | STU-001 | Playwright, axe and the first specs are in `frontend/e2e/` (STU-005); the `e2e` CI job runs them in Chromium, `backend.spec.ts` against the built wheel (review U-3, PR #6); QA-9 adds the platforms e2e smoke (05 §3.5), Firefox and WebKit (TD-007) |
| TABRION STU-006 | Configuration, profiles, project trust | P1 | STU-004 | Done (PR #8, OA-008 accepted; C-1, C-2 done in PR #9): TOML schema v1, project trust (STU-T-016/-017), D-01 wired and meeting U-1. Reads only; writing profiles and settings comes with STU-008, `log_sql` takes effect with STU-011 |
| TABRION STU-007 | Secret management | P0 | STU-006 | Done (PR #10, OA-009 accepted with conditions; C-1, I-1..I-3 done in the STU-008 PR): keyring (no plain-text fallback, refused `fail`/`null`/`keyrings.alt`), `env:`, `file:` with C-3, session secrets bound to the connection target; `PUT`/`DELETE /api/profiles/{id}/secret`, `GET /api/secrets`; fake keyring in tests |
| TABRION STU-008 | Connection manager UI and connection test | P1 | STU-005, STU-007, STU-009 | Done except the parts that need STU-009 (PR #11, OA-010 accepted; review items in the follow-up PR): the manager (list, details, three-step assistant, delete, password), writing the user's `profiles.toml` and `settings.toml` (digest-bound, atomic, 0600, private folder), the settings view's queries and security. The connection test and "Connect" need STU-009 (TD-011) |
| TABRION STU-009 | Connection registry and read-only enforcement | P0 | STU-007, TAB-040 | adds `backend integration (PostgreSQL 17)`; engines are disposed on shutdown; opens a project SQLite file only through `ProjectConfig.database_file` (review T-1) |
| TABRION STU-051 | Status indicators and dialogs | P1 | STU-005 | Done (PR #5, review accepted): environment badge in header, tabs and title, D-01, D-02, D-06, D-08 with tested focus rules; their owners arrive with STU-006/008/011/012 |
| TABRION STU-010..STU-015 | Explorer, query engine, SQL editor, data viewer, diagnostics, migration status | P1/P0 | STU-009, TAB-042 | The "openapi in sync" CI job planned with STU-008 is TD-011; STU-011 replaces the polling heartbeat with SSE and applies the Origin check to it |
| TABRION STU-050, STU-052, STU-053 | UX acceptance, keyboard, accessibility audit | P1/P2 | | |
| TABRION STU-016 | MVP hardening and security review (G-S1) | P0 | all of the above | |
| TABRION STU-017 | MVP docs and tag 0.1.0 (no publication) | P1 | STU-016 | adds the manual, build-only release workflow |

Later: STU-020..STU-025 and STU-054 (Studio 1.0, without the Data Editor), STU-028 (release preparation, G-R);
STU-026 (Data Editor) stays locked behind G-S2 and outside the 1.0 acceptance; STU-027 (more UI languages) is a
later extension. The Studio 1.1 Schema Designer (change request 14 §3.5) is not planned here; the process model
and the server-side write flag (STU-ADR-0003) leave room for it.

### OA-010 · Independent review of STU-008 (TABRION STU-008)

- **Priority:** P1 High · **Status:** Accepted (`stu-008-review.md`, PR #11 merged as master `e8b1d05`) · **Refs:**
  03 §4.3, §6, 08 §4.2, §4.10,
  `docs/architecture/configuration.md` ("Writing the user's files")
- **What:** Studio now writes files. An independent review agent checks that it writes only its own `profiles.toml`
  and `settings.toml` (never the project's file), atomically with 0600 and `O_NOFOLLOW`, only into a private folder,
  only with the digest the page read and never over a file with issues; that a form never echoes a value (issues
  name fixed keys); that a typed password reaches only the secret route; and the connection manager's dialogs
  (focus, axe). No `.github/` file changes.
- **Result:** no Medium or High finding. Done in the STU-008 follow-up PR: L-1 (`sslmode` in the secret
  fingerprint; binding for STU-009 below), L-2 (a lone surrogate is `config.encoding`; a file that would not read
  back is 422 `studio.config_not_storable`, never a 500), L-3 (a refused keyring delete is logged and reported,
  `"secret_removed": false`; the test that kills W11), I-1 (the e2e server uses the null keyring), I-2 (`env:` says
  "set in the environment"), I-3 (the file is read again just before the replace; no lock, TD-011) and I-4 (the 0755
  to 0700 change is documented, as for the state folder).

## Cross-repository checks still to add

| Ref | What | Priority |
|---|---|---|
| TABRION QA-18 | `tests/packaging/test_independent.py`: import every tabrion module while orkexa, axentra, nodestro_* and tabrion_studio are blocked (05 §3.2) | P2 |
| TABRION QA-21 | full AC-7 check against tabrion's `__all__`/API contract and export of the used names to `tabrion/docs/api-consumers.md` (the AST check for private names is in place) | P2 |
| TABRION QA-22 | convention audit against orkexa/axentra (pins, manifest, Dependabot, branch protection, English, license, SECURITY.md) | P2 |

## Technical debt

### TD-001 · Layer contract for `api` → `services` → `profiles`/`security`

- **Priority:** P3 Low · **Status:** Open · **Refs:** 05 §3.2, AC-1
- **What:** the import-linter layer contract needs the `api`, `services` and `profiles` packages. STU-006 adds `api`
  and `config` (the profiles layer; `config` never imports `api`, the app, the server, the CLI or `security`); the
  `services` layer arrives with STU-009. Today the contracts forbid every Levuvo platform, keep the process model from importing the CLI,
  keep `tabrion_studio.security` (TABRION STU-003) below the app, the server, the process model and the CLI, and
  keep `tabrion_studio.problems` and `tabrion_studio.logs` (TABRION STU-004) below every other Studio module.

### TD-002 · Sandboxed browsers cannot open the redirect file (TABRION STU-003)

- **Priority:** P2 Medium (raised by review S-4: the Firefox snap is the default browser of Ubuntu desktop) ·
  **Status:** In progress · **Refs:** review F-02, `stu-003-review.md` S-4, `docs/architecture/access-protection.md`
- **What:** the browser is opened with a `file://` redirect file in the state directory. A browser in a sandbox that
  cannot read that folder (the Firefox snap on Ubuntu cannot read hidden folders such as `~/.local/state`; Flatpak
  browsers similarly) shows an error instead of Studio. Workaround: `--print-url`, or `--state-dir` in a folder the
  browser can read.
- **Done (STU-005 PR):** on Linux without `$BROWSER`, the command asks `xdg-settings get default-web-browser`, looks
  up the desktop file and prints the `--print-url` / `--state-dir` advice when it is a snap or a Flatpak
  (`src/tabrion_studio/browser.py`, `tests/unit/test_browser.py`).
- **Next step:** the fix itself: place the redirect file (only that file) in a private folder the sandbox can read,
  for example a non-hidden 0700 folder below the user's home, with the same checks as the state directory.

### TD-003 · Windows: check the DACL inside the profile too (review R-6)

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-002-018-review.md` R-6, `paths.py:_check_windows_dir`
- **What:** inside `%USERPROFILE%`/`%LOCALAPPDATA%` the state directory's DACL is not checked. A state directory in a
  synced or shared profile folder (for example `%USERPROFILE%\OneDrive\…`) would put the control token, and now the
  redirect files, there. The default (`%LOCALAPPDATA%`) is not synced.
- **Next step:** run `dacl_problem` inside the profile as well, or warn about known sync folders (STU-006 or STU-016).

### TD-004 · Fork pull requests in the workflow-change gate (review R-7)

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-002-018-review.md` R-7, `ci_ok_status.py:pull_numbers`
- **What:** for fork pull requests (no `pull_requests` in the `workflow_run` event) the gate relies on
  `GET /commits/{sha}/pulls` listing the open fork pull request. If GitHub does not list it, the gate fails closed
  ("no open pull request found"), which is safe but could block a fork pull request by mistake.
- **Next step:** check it on the first fork pull request; if needed, fall back to `GET /pulls?state=open` filtered by
  `head.sha`. Changing `ci_ok_status.py` needs the label (OA-003).

### TD-005 · Spent launch URL in the browser history (review S-2)

- **Priority:** P3 Low · **Status:** Accepted risk · **Refs:** `stu-003-review.md` S-2 (X-9),
  `docs/architecture/access-protection.md` (Launch)
- **What:** the global browser history (and a synced history) keeps `http://127.0.0.1:<port>/launch#<token>` and the
  redirect file's path. The token is worthless once redeemed; if the page's script never ran, it stays valid for up to
  120 s (`LAUNCH_TTL_SECONDS`).
- **Next step:** shorten `LAUNCH_TTL_SECONDS` once real-browser start times are known (QA-9 measures them).

### TD-006 · Who may set the label `workflow-change-reviewed` (review S-3)

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-003-review.md` S-3, `ci_ok_status.py:label_after_push`,
  OA-003
- **What:** the gate records the actor of the `labeled` event but does not restrict it: any user with triage or write
  access, including an agent token, satisfies it, and the label is not bound to a SHA. Until then the "who sets the
  label" rule in `docs/development/ci.md` is policy only.
- **Next step:** optionally keep an allowlist of logins whose `labeled` events count, and optionally require the
  label to follow a PR comment `reviewed <head sha>` by such a login. Changing `ci_ok_status.py` needs the label
  itself (OA-003) and an independent workflow-change review.

### TD-007 · Development proxy and real-browser launch checks (review S-5)

- **Priority:** P3 Low · **Status:** Open (documented) · **Refs:** `stu-003-review.md` S-5, `frontend/vite.config.ts`,
  `docs/architecture/access-protection.md` (Development server)
- **What:** the Vite development proxy rewrites `Origin` to the backend's origin. Fetch metadata, the CSRF header and
  `SameSite=Strict` still block cross-site use, and Vite checks `Host`, but the development setup is not for shared
  machines (now said in the docs and in `vite.config.ts`). The launch flow (`file://` meta refresh to http, the CSP)
  was checked in Chrome only.
- **Next step:** QA-9 adds Firefox and WebKit runs of the launch flow against the built wheel.

### TD-008 · Add a windows-latest CI job for the T-2 checks (review C-2 of `stu-006-review.md`)

- **Priority:** P1 High (before STU-009 and before any build is published, G-R) · **Status:** Open (needs a
  workflow-change review, OA-003) · **Refs:** `stu-006-review.md` T-2 and C-2, `config/locations.py`
- **What:** the review asks for a test in a Windows CI job that patches `nt._getfinalpathname` and asserts that it is
  never called for a UNC value from the project file. The test exists
  (`tests/security/test_project_trust.py::test_windows_never_resolves_a_unc_path_from_the_project_file`) and the pure
  path tests use `PureWindowsPath` on every platform; today the Windows test runs in the `windows-latest` leg of the
  test matrix. The conditions PR does not change `.github/`.
- **Next step:** add a windows-latest CI job (or a required step in the existing leg) that runs the T-2 tests by name
  and fails if they are skipped, like the `e2e` job's required-suite check; then the workflow-change review and the
  label.

### TD-009 · STU-004 review: remaining Low and Info items

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-004-review.md` L-2, I-1..I-5
- **L-2 (rest):** the scrub does not remove `passwd <value>` without `=` or `:`, the short key `pw=`, or URL-encoded
  `password%3D...`; matching those without false positives needs care (the regex is defence in depth only).
- **I-1:** above 512 registered secrets the oldest, the control token, is dropped first. Pin it (a set that is never
  evicted) or evict only session and launch values.
- **I-2:** a hard link at the log path is accepted and a FIFO would block the start. Refuse `st_nlink != 1`; open with
  `O_NONBLOCK`, check `S_ISREG`, then clear the flag.
- **I-3:** Windows has no `O_NOFOLLOW`: check the log file with `lstat` (reparse bit) before `os.open`.
- **I-4:** the refusal texts say "the state directory" also for the log folder; `warnings.warn` output goes to
  stderr outside the redacting handlers (`logging.captureWarnings` is not set).
- **I-5:** no test plants a log file owned by another user (mutant M11): `os.chown(path, 65534, 65534)` when running
  as root, otherwise skip.
- **R-2 of `stu-conditions-rereview.md`:** exception text still reaches the message in three ways: (a) the message
  *is* the exception (`logger.error(exc)`; Uvicorn does this on a startup `OSError`), (b) an exception nested in a list
  or tuple argument, (c) traceback text without the `Traceback (most recent call last):` line. None is used by Studio
  or reachable by a client today. Fix: replace a `BaseException` message by its type, apply the argument replacement
  recursively (depth-capped), and optionally cut non-Studio messages at `\n  File "`.
- **Observed in the conditions PR:** when the lifespan fails at startup, Uvicorn exits through `sys.exit(3)`, so
  `serve()` does not print "Tabrion Studio could not start." (its cleanup still runs). Catch `SystemExit` around
  `server.run` if the message matters.

### TD-010 · STU-006 review: remaining Low and Info items

- **Priority:** P2 Medium · **Status:** Open (T-4 rest, T-5 and STU-007 L-1 done in the D-01 flows PR) · **Refs:** `stu-006-review.md`
  T-1, T-4, T-5, I-1, I-3..I-5; `stu-007-review.md` L-1, I-4, I-5
- **T-1 (rest):** the trust POST binds to the bytes only. If only the file system changes between the dialog and the
  click, the new review is stored without having been shown (the next GET compares against it). Bind the POST to a
  digest of the review as well. The real target of a database path the user confirmed as *outside* is not recorded,
  so a link that later points elsewhere outside keeps it trusted; record the resolved path in the review.
- **T-4 (rest), done:** D-01 shows each connection's TLS mode and marks `weak_tls` (weaker than `verify-full` on a
  non-loopback host). The T-10 diagnostic finding for `disable` and `prefer` stays with STU-010.
- **T-5, done:** D-01 shows one line per PostgreSQL connection with the secret's source, `host:port` and the TLS mode,
  and marks `remote_host`.
- **I-1:** escape `\p{Default_Ignorable_Code_Point}` (U+200B, U+FEFF, U+00AD, tag characters) in `visible.ts`.
- **I-3:** open `.tabrion` with `O_DIRECTORY|O_NOFOLLOW` and `studio.toml` with `dir_fd` (one path walk); test the
  dev/ino compare (mutant T2); on Windows compare `GetFinalPathNameByHandle` with the expected path.
- **I-4:** check the configuration folder like the state folder (owner, 0700, no link): `--config-dir /tmp/x` would
  accept another user's `settings.toml`.
- **I-5:** name STU-T-021 (T-10) in the `verify-full` test or note that it belongs to STU-010.
- **STU-007 L-1, done:** the D-01 line reads "password from `env:X` / `file:Y` is sent to `host:port`" and marks
  well-known credential variables (`sensitive_variable`) and `file:` passwords outside the project
  (`secret_outside`). Not done: limiting project `file:` references to a subfolder such as
  `<config>/project-secrets/`; the marks are warnings, the user decides.
- **STU-007 I-4:** `select_backend` refuses a denylist of backends; a third-party plain-text backend configured
  through `PYTHON_KEYRING_BACKEND` or `keyringrc.cfg` would be accepted (user configuration only). Consider an
  allowlist: `keyring.backends.macOS`, `Windows`, `SecretService`, `libsecret`, `kwallet`.
- **STU-007 I-5:** on Windows the `file:` reader checks the components with `lstat` and then opens by path, so a swap
  in between is followed; owner and mode are not checked (the folder ACL decides, TD-003). After the open, compare
  `GetFinalPathNameByHandle` with the expected path and check that the handle is not a reparse point.
- **R-1 of `stu-conditions-rereview.md` (done in the STU-007 PR):** a *relative* link target that climbs to `/net`
  was checked only as text, so the walk `lstat`ed `/net/<host>` (on macOS an automount, a beacon). `safe_realpath` now
  checks every component against the network and automount forms before `lstat`
  (`test_a_relative_link_that_climbs_to_net_is_refused_before_lstat`). R-3 (refuse a leading `\??\` as text) is done
  as well.
- **PR #14 review (`stu-pr12-14-review.md`, accepted; open follow-ups):**
  - **L-1:** the D-01 password line starts with the connection name, which may be up to 200 characters and contain
    `:` and `→`. A hostile name can push the real `secret → host:port` behind the 64-character cap ("Show full
    value"). The marks, the TLS mode and the hosts and variables lists stay visible. Show the name, the secret and
    the destination as three separately capped parts, or put the destination first.
  - **I-1:** `sensitive_variable` is a heuristic. It does not mark `DATABASE_URL`, `STRIPE_KEY`, `KUBECONFIG`,
    `SENTRY_DSN` or `*_KEY` in general; consider `_KEY`, `_PASSWORD` and `DATABASE_URL`. Every `env:` variable is
    listed in D-01 anyway.
  - **I-2:** after the upgrade, a project trusted before #14 shows every connection as "changed" without a reason.
    Show a one-time note ("Studio now shows more about each connection; nothing in the file changed"), so that
    users do not learn to click through a changed D-01.
  - **I-3:** pin the prefix list itself with a name like `AWS_REGION` (mutant P12 survives, because
    `AWS_SECRET_ACCESS_KEY` also matches `SECRET`).

### TD-011 · STU-008 parts that wait for STU-009 or a later package

- **Priority:** P1 High · **Status:** Open · **Refs:** 08 §4.2, 03 §2 A, §6
- **Connection test and "Connect"** (`POST /api/profiles/{id}/test`, the test result in step 3 and in the details,
  D-02 before a PROD connection): they need the connection registry, `Database.health()` and
  `tabrion.postgres.role_info` (STU-009, TAB-040, RC-8). The buttons are shown disabled with a note until then.
- **"Save in: Project"** of the assistant is not offered: Studio 1.0 writes only its own files. A project profile is
  edited in `.tabrion/studio.toml`; the manager says so.
- **Pasting a `postgresql://` URL** (its password moved to the keyring) and showing URLs need `tabrion.safe_url()`
  (STU-009).
- **"Browse…" for a SQLite file** needs a file dialog in the backend; the path is typed until then.
- **Renaming a profile** (it would move its keyring entry) is refused (`config.rename_unsupported`); delete and
  create instead.
- **Favorites and recently opened** are a 1.0 feature (03 §2 A).
- **The `openapi in sync` CI job** (the first API types for the frontend) changes `.github/` and needs a
  workflow-change review (OA-003); the frontend parses the answers defensively (`src/api/profiles.ts`) until then.
- **Language and theme in `settings.toml`:** the page keeps them as browser preferences (STU-005); `PUT /api/settings`
  accepts them, but the page does not send them yet.
- **PR #13 review (`stu-pr12-14-review.md`, accepted; open follow-ups):**
  - **L-1:** in the assistant, `secretKept` hides `secretProblem`. When a retarget could neither remove the old
    keyring entry nor store the new password, the page only says the old one was kept. Show both.
  - **I-1:** no test pins the `_encodable` check of `parse_secret_ref` for `file:` (mutant M6).
  - **I-2:** test gaps behind mutants M8 to M11: assert the keyring warning record, an unreadable file during the
    recheck, the settings recheck, and `secret_removed: true` for an update that keeps the target.
  - **I-3:** keyring entries saved before `sslmode` joined the fingerprint are no longer found (fails safe: the
    password is asked again, and the old entry stays). Mention it in the release notes if an STU-008 build was ever
    handed out.
- **Concurrent edits (I-3 of `stu-008-review.md`):** a save re-reads the file just before `os.replace`, but takes no
  lock; an edit within the replace itself is lost. A lock file would close it, if hand edits during a save matter.
