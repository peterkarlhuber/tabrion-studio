"""The Database Manager of Studio 1.3 (STU-ADR-0008, threat model TX-32..TX-46, gate G-S4), behind the
``database_manager`` flag of ``write-areas.toml``, which is off by default.

Nothing in this package is imported while that flag is off: the routes of :mod:`tabrion_studio.api.write` refuse
with ``405 studio.method_not_allowed`` without importing it (every flag off), and :mod:`tabrion_studio.write.routes`
imports it only after it checked the flag (another area's flag on). The read paths never import it (import-linter
contract ``read-paths-never-write``).

Built so far: backup and restore (STU-XDM-2; :mod:`.backups`) with the verified tool runner
(:mod:`tabrion_studio.write.pgtools`, outside this package so the Schema Designer can use it later) and the backup
folder (:mod:`tabrion_studio.write.backup_store`), and deleting a database after a verified backup (STU-XDM-3;
:mod:`.delete`). Roles (STU-XDM-4) and profile pairs (STU-XDM-5) are open; G-S4 is not granted.
"""
