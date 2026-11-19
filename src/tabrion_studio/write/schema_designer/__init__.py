"""The Schema Designer of Studio 1.2 (STU-ADR-0007, threat model TX-18..TX-31, gate G-S3), behind the
``schema_designer`` flag of ``write-areas.toml``, which is off by default.

Nothing in this package is imported while that flag is off: the routes of :mod:`tabrion_studio.api.write` refuse
with ``405 studio.method_not_allowed`` without importing it (every flag off), and
:mod:`tabrion_studio.write.routes` imports it only inside a handler, after it checked the flag (another area's flag
on). The read paths never import it (import-linter contract ``read-paths-never-write``).

Built so far: the database creation wizards (STU-CR-2, STU-CR-3; :mod:`.create`), the table designer with its
server-side draft and plan preview (STU-CR-4; :mod:`.draft`, :mod:`.designer`) and the confirmed direct run of a plan
on DEV or TEST with its backup (STU-CR-5; :mod:`.execute`), and the read-only schema comparison with its plan
preview that is never kept or run (STU-XSD-2; :mod:`.compare`), the graphical mode (STU-XSD-1) and the revision
output (STU-XSD-3). G-S3 is effective since 2026-11-19 for local DEV, TEST and STAGE only; the flag stays off by
default and nothing turns it on, and H-2 and G-R stay open.
"""
