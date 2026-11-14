# Architecture decisions

The Studio decisions STU-ADR-0001 to STU-ADR-0004 from the program's planning document 10, in English,
STU-ADR-0005 from planning document 15 (change request CR-DBX), and STU-ADR-0006, the successor of STU-ADR-0003
(CR-DBX work package STU-XW-5). The status
of each is the status in that document; a change needs a new ADR and the owner's approval.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-local-web-app.md) | Local web app with its own CLI; desktop packaging later | Needs E-S2 (Tauri only after 1.0) |
| [0002](0002-local-security-model.md) | Local security model | Proposed (E-QA2 decided) |
| [0003](0003-writes-behind-gate.md) | Writes behind a server-side flag until G-S2; Studio 1.0 without Data Editor | Superseded by 0006 (E-S1 decided; rules carried over) |
| [0004](0004-frontend-reuse.md) | Reuse instead of new builds in the frontend | Needs E-S4, E-S6 |
| [0005](0005-crdbx-write-and-admin-gates.md) | CR-DBX write and admin features locked per area until G-S2, G-S3, G-S4; amends 0003 | Accepted as planning (2026-11-13); development start approved conditionally (G-CX, 2026-11-14), starts once S-1..S-4 are proven |
| [0006](0006-per-area-write-kill-switches.md) | Per-area write and admin kill switches: one default-off flag and one gate per area, set only locally, W-9 error semantics, PROD write-locked, separate write and admin profiles; supersedes 0003 | Proposed (STU-XW-5; records owner decisions E-X7..E-X9, W-9, G-CX) |
