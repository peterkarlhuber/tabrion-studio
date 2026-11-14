"""The shared write foundation of Studio 1.1 (STU-XW-1, STU-ADR-0006): write and admin connections and unlocking.

Nothing in this package is imported while every write area's flag is off (``tabrion_studio.config.write_areas``):
the routes of :mod:`tabrion_studio.api.write` are then refusing handlers that answer ``405`` without importing it
(TX-02). The read paths (the connection registry, queries, the viewer, the explorer, exports, diagnostics, vector
data, migrations and their worker, the history) never import it (import-linter contract ``read-paths-never-write``,
TX-03, TX-10).
"""
