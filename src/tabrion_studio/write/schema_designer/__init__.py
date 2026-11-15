"""The Schema Designer of Studio 1.2 (STU-ADR-0007, threat model TX-18..TX-31, gate G-S3), behind the
``schema_designer`` flag of ``write-areas.toml``, which is off by default.

Nothing in this package is imported while that flag is off: the routes of :mod:`tabrion_studio.api.write` refuse
with ``405 studio.method_not_allowed`` without importing it (every flag off), and
:mod:`tabrion_studio.write.routes` imports it only inside a handler, after it checked the flag (another area's flag
on). The read paths never import it (import-linter contract ``read-paths-never-write``).

Built so far: the database creation wizards (STU-CR-2, STU-CR-3; :mod:`.create`). The designer, its plans and the
revision output (STU-CR-4.., STU-XSD-*) are open; G-S3 is not granted.
"""
