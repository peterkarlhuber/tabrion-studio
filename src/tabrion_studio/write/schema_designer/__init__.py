"""The Schema Designer of Studio 1.2 (STU-ADR-0007, threat model TX-18..TX-31, gate G-S3), behind the
``schema_designer`` flag of ``write-areas.toml``, which is off by default.

Nothing in this package is imported while that flag is off: the routes of :mod:`tabrion_studio.api.write` refuse
with ``405 studio.method_not_allowed`` without importing it (every flag off), and
:mod:`tabrion_studio.write.routes` imports it only inside a handler, after it checked the flag (another area's flag
on). The read paths never import it (import-linter contract ``read-paths-never-write``).

Built so far: the database creation wizards (STU-CR-2, STU-CR-3; :mod:`.create`), the table designer with its
server-side draft and plan preview (STU-CR-4; :mod:`.draft`, :mod:`.designer`) and the confirmed direct run of a plan
on DEV or TEST with its backup (STU-CR-5; :mod:`.execute`). The graphical mode, comparison and revision output
(STU-XSD-*) and the PostgreSQL backup before a destructive run are open (the verified runner
:mod:`tabrion_studio.write.pgtools` exists since STU-XDM-2 but is not used here yet); G-S3 is not granted.
"""
