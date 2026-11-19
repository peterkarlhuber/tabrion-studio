"""STU-XW-1 kill-switch proof (STU-ADR-0006 "Proof required from every kill-switch pull request", TX-01, TX-02).

1. **Default off:** every area is off with no flag source: the server's runtime, the CLI's, the app's and the
   ``/api/write/areas`` answer (the process start is in ``test_write_flags.py``).
2. **Effective:** every route of the write foundation (``WRITE_ROUTES``, every method) answers the W-9 refusal
   ``405 studio.method_not_allowed`` with every flag off, and nothing changes (the profile file, the database file,
   the keyring, the session secrets, the unlocks). With every flag off the write package is not even imported.
3. **One area's flag never opens another's** routes: the admin kind stays 405 with only the Data Editor on, the write
   kind with only the Database Manager on.
4. Every refusal is a catalog problem that echoes nothing of the request (canary).
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from tabrion_studio.api.write import MAX_PLAN_BODY, WRITE_ROUTES
from tabrion_studio.app import Runtime
from tabrion_studio.config.write_areas import ALL_OFF, Area, WriteAreas
from tabrion_studio.server import ServeOptions
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, DATABASE_MANAGER, WriteEnv, digest

pytestmark = pytest.mark.security

#: Every (method, path) of the write foundation with a body that would do something if the route were not refused.
CALLS: list[tuple[str, str, dict[str, object]]] = []
for _path, _methods in WRITE_ROUTES:
    for _method in _methods:
        CALLS.append(
            (
                _method,
                re.sub(r"\{[^}]+\}", "write:parts-write", _path),
                {
                    "profile": {"name": "parts-write", "kind": "write", "read_profile": "parts"},
                    "id": "write:parts-write",
                    "digest": None,
                    "confirm_no_prod_credentials": True,
                    "connection_id": "c_x",
                    "write_profile_id": "write:parts-write",
                    "typed_name": "parts-write",
                    "secret": CANARY_SECRET,
                    "store": "session",
                    "value": CANARY_VALUE,
                    "confirmation_hash": "0" * 64,
                    "typed": "localhost",
                    # STU-CR-2, STU-CR-3: the Schema Designer's database creation (G-S3).
                    "path": "/tmp/kill-switch-canary.db",  # noqa: S108 - never created: the route refuses first
                    "name": "kill_switch_canary",
                    # STU-CR-4: the Schema Designer's draft (G-S3).
                    "schema": "main",
                    "revision": 0,
                    "edit": {"op": "drop_table", "table": "parts"},
                    # STU-CR-5: the confirmation and the run of a previewed plan (G-S3).
                    "plan_hash": "sha256:" + "0" * 64,
                    "tables": ["parts"],
                    "steps": [],
                    "locking": False,
                    "backup": "skip",
                    "dry_run": False,
                    # STU-XSD-2: the schema comparison and its plan preview (G-S3).
                    "expected": {"source": "connection", "connection_id": "c_x"},
                    "actual": {"source": "connection", "connection_id": "c_x"},
                    "plan": True,
                    # STU-XSD-3: the revision output (G-S3).
                    "message": "add qty",
                    # STU-XDM-2: the Database Manager's backup and restore (G-S4).
                    "backup_id": "20261115T000000Z-00000000.dump",
                    "target": "new",
                    "restore_profile_id": "write:parts-write",
                    # STU-XDM-4: PostgreSQL roles (G-S4).
                    "action": "drop",
                    "role": "kill_switch_role",
                    "state": "0" * 64,
                    # STU-XDM-5: profile export and import (G-S4).
                    "profiles": ["user:parts"],
                    "overwrite": True,
                },
            )
        )


def test_the_route_table_covers_profiles_secrets_unlocks_and_the_audit_log() -> None:
    methods = {f"{method} {path}" for method, path, _ in CALLS}
    assert len(methods) == 45
    for expected in (
        "POST /api/write/profiles",
        "POST /api/write/unlocks",
        "DELETE /api/write/unlocks",
        "GET /api/write/audit",  # STU-XW-2: read only, and still 405 with every flag off
        "POST /api/write/plans/execute",  # STU-XW-3: the plan framework
        "POST /api/write/plans/confirm",
        "DELETE /api/write/plans",
        "POST /api/write/secret-confirmations",  # STU-XW-4: the I-109 j confirmation (D-15)
        "POST /api/write/secret-confirmations/confirm",
        "DELETE /api/write/secret-confirmations",
        "POST /api/write/data-editor/table",  # STU-026: the Data Editor's table (its changesets are plans)
        "POST /api/write/database-manager/create/sqlite",  # STU-CR-2: a new SQLite file (admin area, G-S4)
        "POST /api/write/database-manager/create/postgresql",  # STU-CR-3: a new PostgreSQL database (admin area)
        "POST /api/write/schema-designer/draft",  # STU-CR-4: the server-side draft and its preview (G-S3)
        "DELETE /api/write/schema-designer/draft",
        "POST /api/write/schema-designer/draft/view",
        "POST /api/write/schema-designer/draft/edits",
        "POST /api/write/schema-designer/draft/preview",
        "POST /api/write/schema-designer/plan/confirm",  # STU-CR-5: confirm and run a previewed plan (G-S3)
        "POST /api/write/schema-designer/plan/run",
        "POST /api/write/schema-designer/compare",  # STU-XSD-2: the read-only comparison (G-S3)
        "POST /api/write/schema-designer/revision",  # STU-XSD-3: the Alembic revision output (G-S3)
        "POST /api/write/schema-designer/profile-export",  # STU-CR-6 part 1: the profile export (G-S3)
        "GET /api/write/database-manager/backups",  # STU-XDM-2: backup and restore (G-S4)
        "POST /api/write/database-manager/backup",
        "POST /api/write/database-manager/restore/preview",
        "POST /api/write/database-manager/restore",
        "POST /api/write/database-manager/delete/preview",  # STU-XDM-3: delete a database (G-S4)
        "POST /api/write/database-manager/delete",
        "POST /api/write/database-manager/roles",  # STU-XDM-4: PostgreSQL roles (G-S4)
        "POST /api/write/database-manager/roles/preview",
        "POST /api/write/database-manager/roles/apply",
        "POST /api/write/database-manager/profiles/export/preview",  # STU-XDM-5: profile export and import (G-S4)
        "POST /api/write/database-manager/profiles/export",
        "POST /api/write/database-manager/profiles/import/preview",
        "POST /api/write/database-manager/profiles/import",
        "POST /api/write/database-manager/runtime-role/preview",  # STU-CR-6 part 2: both flags (G-S3, G-S4)
        "POST /api/write/database-manager/runtime-role/apply",
    ):
        assert expected in methods
    assert "PUT /api/write/profiles/write:parts-write/secret" in methods


def test_every_flag_is_off_by_default() -> None:
    """Proof item 1: the runtime, the serve options and the area table default to off; nothing else builds one."""
    assert ALL_OFF.enabled == frozenset()
    assert ALL_OFF.to_dict() == {"data_editor": False, "schema_designer": False, "database_manager": False}
    assert WriteAreas().to_dict() == ALL_OFF.to_dict()
    fields = Runtime.__dataclass_fields__
    assert fields["write_areas"].default is ALL_OFF
    assert ServeOptions.__dataclass_fields__["write_areas_dir"].default is None
    assert {area.value for area in Area} == {"data_editor", "schema_designer", "database_manager"}


@pytest.fixture
def env(tmp_path: Path, static_dir: Path) -> Iterator[WriteEnv]:
    environment = WriteEnv(tmp_path, static_dir)
    try:
        yield environment
    finally:
        environment.close()


def _state(env: WriteEnv) -> dict[str, object]:
    profiles = env.config_dir / "profiles.toml"
    return {
        "profiles": profiles.read_bytes() if profiles.exists() else None,
        "database": digest(env.db),
        "side_files": sorted(p.name for p in env.db.parent.glob("parts.db-*")),
        "keyring": dict(env.fake.values),
        "session_secrets": dict(env.config.secrets.session._values),
        # STU-XW-2: the audit log is neither created nor read with every flag off.
        "audit": sorted(p.name for p in env.audit_dir.iterdir()) if env.audit_dir.exists() else None,
    }


def test_the_areas_route_says_every_area_is_off(env: WriteEnv) -> None:
    response = env.send("GET", "/api/write/areas")
    assert response.status_code == 200
    assert response.json() == {"areas": {"data_editor": False, "schema_designer": False, "database_manager": False}}
    assert env.unlocks is None


@pytest.mark.parametrize(("method", "path", "body"), CALLS, ids=[f"{m} {p}" for m, p, _ in CALLS])
def test_flag_off_every_write_route_is_405_and_changes_nothing(
    env: WriteEnv, method: str, path: str, body: dict[str, object]
) -> None:
    """Proof item 2, per endpoint: W-9 answer, nothing changed (profile file, database bytes, secrets)."""
    env.create_read()
    # A write profile written by the user into their own file: still unreachable with the flag off.
    (env.config_dir / "profiles.toml").write_text(
        (env.config_dir / "profiles.toml").read_text(encoding="utf-8")
        + '\n[[write_profile]]\nname = "parts-write"\nkind = "write"\nread_profile = "parts"\n',
        encoding="utf-8",
    )
    connection_id = env.open()
    body = {**body, "connection_id": connection_id}
    before = _state(env)
    response = env.send(method, path, body)
    assert (response.status_code, response.json()["code"]) == (405, "studio.method_not_allowed")
    text = response.text
    assert CANARY_SECRET not in text and CANARY_VALUE not in text and "parts-write" not in text
    assert _state(env) == before
    assert not env.audit_dir.exists()
    assert env.unlocks is None


@pytest.mark.parametrize(
    "query", ["?data_editor=true", "?areas=data_editor", "?write=1&mode=write", "?database_manager=on"]
)
def test_a_query_parameter_or_a_header_never_turns_a_flag_on(env: WriteEnv, query: str) -> None:
    """Proof item 3: a query parameter, a header or a body never turns an area on."""
    headers = {
        **env.headers,
        "X-Tabrion-Write-Areas": "data_editor,schema_designer,database_manager",
        "X-Tabrion-Data-Editor": "true",
    }
    areas = env.studio.client.get(f"/api/write/areas{query}", headers=headers)
    assert areas.json()["areas"] == ALL_OFF.to_dict()
    refused = env.studio.client.post(
        f"/api/write/unlocks{query}", json={"areas": {"data_editor": True}}, headers=headers
    )
    assert (refused.status_code, refused.json()["code"]) == (405, "studio.method_not_allowed")
    assert env.send("GET", "/api/write/areas").json()["areas"] == ALL_OFF.to_dict()


@pytest.mark.parametrize("size", [1024, MAX_PLAN_BODY - 1024], ids=["small", "near_the_cap"])
def test_flag_off_a_changeset_with_a_replaced_file_is_405_and_changes_nothing(env: WriteEnv, size: int) -> None:
    """G-S2 6A: the plan route takes a larger body (a Data Editor bytes value replaced by a file), and with every flag
    off it is still the refusing handler: 405, nothing read, written or recorded, and no write code loaded. A body
    above the cap is refused before the route (413)."""
    env.create_read()
    connection_id = env.open()
    filler = base64.b64encode(CANARY_VALUE.encode("utf-8") * (size // len(CANARY_VALUE) // 2)).decode("ascii")
    changes = [
        {"op": "update", "key": {"id": 1}, "old": {"id": 1, "data": None}, "new": {"data": {"base64": filler}}},
        # A change the database would refuse for a constraint: refused before anything is looked at.
        {"op": "insert", "values": {"id": 1, "data": None}},
    ]
    body = {
        "connection_id": connection_id,
        "executor": "data_editor.changeset",
        "request": {"schema": "main", "table": "parts", "columns": ["id", "data"], "changes": changes},
    }
    assert len(json.dumps(body)) < MAX_PLAN_BODY
    before = _state(env)
    response = env.send("POST", "/api/write/plans", body)
    assert (response.status_code, response.json()["code"]) == (405, "studio.method_not_allowed")
    assert filler[:64] not in response.text and CANARY_VALUE not in response.text
    too_large = {**body, "padding": "x" * MAX_PLAN_BODY}
    over = env.send("POST", "/api/write/plans", too_large)
    assert (over.status_code, over.json()["code"]) == (413, "access.body_too_large")
    assert _state(env) == before
    assert not env.audit_dir.exists()
    assert env.unlocks is None


@pytest.mark.parametrize(
    "settings",
    [
        {"data_editor": True},
        {"write_areas": {"data_editor": True}},
        {"areas": {"data_editor": True, "schema_designer": True, "database_manager": True}},
        {"writes_enabled": True},
        {"database_manager": True},
    ],
)
def test_the_settings_api_refuses_a_flag_key_as_unknown(env: WriteEnv, settings: dict[str, object]) -> None:
    """Proof item 3, the settings save: a flag key is ``config.unknown_key`` like any unknown key; nothing written."""
    current = env.send("GET", "/api/settings").json()["file"]["digest"]
    response = env.send("PUT", "/api/settings", {"settings": settings, "digest": current})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "studio.settings_invalid"
    assert {issue["code"] for issue in response.json()["issues"]} == {"config.unknown_key"}
    assert not (env.config_dir / "settings.toml").exists()
    assert env.send("GET", "/api/write/areas").json()["areas"] == ALL_OFF.to_dict()


def test_with_every_flag_off_no_write_code_is_imported(tmp_path: Path) -> None:
    """TX-02: the refusing handlers neither import, construct nor call write code (a fresh interpreter)."""
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tests.support.write import WriteEnv\n"
        "import tests.support.write as w\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}))\n"
        "env.create_read()\n"
        "calls = [('POST', '/api/write/unlocks'), ('POST', '/api/write/profiles'), ('GET', '/api/write/profiles'),\n"
        "         ('GET', '/api/write/audit')]\n"
        "for m, p in calls:\n"
        "    assert env.send(m, p, {'digest': None}).status_code == 405\n"
        "env.close()\n"
        "assert not env.audit_dir.exists()\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write')))\n"
    )
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=root, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    # Neither the write package nor its audit log (tabrion_studio.write.audit, STU-XW-2) is loaded.
    assert result.stdout.strip().splitlines()[-1] == "[]"


def test_with_a_flag_on_the_write_code_is_loaded(tmp_path: Path) -> None:
    """The counterpart: the same script with the Data Editor flag on loads the write package (the test above is
    not vacuous)."""
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tests.support.write import WriteEnv, DATA_EDITOR\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), DATA_EDITOR)\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write')))\n"
    )
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=root, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout.strip().splitlines()[-1].replace("'", '"'))
    assert "tabrion_studio.write.unlock" in loaded and "tabrion_studio.write.connection" in loaded
    assert "tabrion_studio.write.audit" in loaded


@pytest.mark.parametrize(
    ("areas", "kind"),
    [(DATA_EDITOR, "admin"), (DATABASE_MANAGER, "write"), (WriteAreas(frozenset({Area.SCHEMA_DESIGNER})), "admin")],
)
def test_one_areas_flag_never_opens_another_areas_profiles(
    tmp_path: Path, static_dir: Path, areas: WriteAreas, kind: str
) -> None:
    """TX-01, TX-15: a write profile needs the Data Editor or Schema Designer flag, an admin profile the Database
    Manager flag; the other kind stays 405 and its profile is neither created, listed, unlocked nor given a secret."""
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        created = env.create_write(name="other-kind", kind=kind, user=None)
        assert (created.status_code, created.json()["code"]) == (405, "studio.method_not_allowed")
        env.write_file(
            (env.config_dir / "profiles.toml").read_text(encoding="utf-8")
            + f'\n[[write_profile]]\nname = "other-kind"\nkind = "{kind}"\nread_profile = "parts"\n'
        )
        assert env.send("GET", "/api/write/profiles").json()["write_profiles"] == []
        connection_id = env.open()
        unlocked = env.unlock(connection_id, "write:other-kind", "other-kind")
        assert (unlocked.status_code, unlocked.json()["code"]) == (405, "studio.method_not_allowed")
        secret = env.send(
            "PUT", "/api/write/profiles/write:other-kind/secret", {"secret": CANARY_SECRET, "store": "session"}
        )
        assert (secret.status_code, secret.json()["code"]) == (405, "studio.method_not_allowed")
        assert CANARY_SECRET not in secret.text
        assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    finally:
        env.close()
