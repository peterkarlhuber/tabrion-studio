"""STU-CR-6 part 2: the runtime-role wizard, without a server (STU-ADR-0007 section 12; TX-30; GS3-22). The rules
that need a server are in ``test_dbm_runtime_role_postgres.py``.

1. **Both flags (ADR 0007 section 12).** With no flag, with only the Database Manager or only the Schema Designer (or
   the other areas), the two routes answer 405 and the wizard's module is not imported (a fresh interpreter).
2. **Who.** Both flags on but no admin unlock: ``studio.write_locked``; a SQLite connection: ``role.engine``. A field
   the route does not know (a password in the preview) is refused before anything runs.
3. **The request.** Names with characters that cannot be seen, an over-long name, a ``create`` that is not a boolean,
   too many protected tables, a password for an existing role: fixed problems, never echoing a value.
4. **Audit fields.** The start record takes names and flags only.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.audit import clean_fields
from tabrion_studio.write.connection import WriteError, defensive_available
from tabrion_studio.write.database_manager import runtime_role
from tests.support.write import CANARY_SECRET, WriteEnv

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = "/api/write/database-manager/runtime-role/preview"
APPLY = "/api/write/database-manager/runtime-role/apply"
BOTH = WriteAreas(frozenset({Area.SCHEMA_DESIGNER, Area.DATABASE_MANAGER}))
NOT_BOTH = {
    "default": None,
    "database-manager-only": WriteAreas(frozenset({Area.DATABASE_MANAGER})),
    "schema-designer-only": WriteAreas(frozenset({Area.SCHEMA_DESIGNER})),
    "data-editor-and-schema-designer": WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER})),
    "data-editor-and-database-manager": WriteAreas(frozenset({Area.DATA_EDITOR, Area.DATABASE_MANAGER})),
}


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_SECRET not in response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in response.json().get("issues", [])] == issues


def _body(connection_id: str, **doc: Any) -> dict[str, Any]:
    return {"connection_id": connection_id, "role": "app_rt", "schema": "public", "create": True, **doc}


# --- 1. both flags (ADR 0007 section 12) ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(NOT_BOTH))
@pytest.mark.parametrize("path", [PREVIEW, APPLY])
def test_the_wizard_needs_both_flags(tmp_path: Path, static_dir: Path, name: str, path: str) -> None:
    areas = NOT_BOTH[name]
    env = WriteEnv(tmp_path, static_dir) if areas is None else WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        connection_id = env.open()
        body = _body(connection_id, typed="app_rt", state="0" * 64, password=CANARY_SECRET)
        _refused(env.send("POST", path, body), 405, "studio.method_not_allowed")
    finally:
        env.close()


@pytest.mark.parametrize("flags", [("DATABASE_MANAGER",), ("SCHEMA_DESIGNER",)])
def test_with_one_flag_the_wizard_module_is_not_imported(tmp_path: Path, flags: tuple[str, ...]) -> None:
    """Either flag alone: both routes answer 405 and ``runtime_role`` is never loaded."""
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        f"areas = WriteAreas(frozenset({{{', '.join(f'Area.{f}' for f in flags)}}}))\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), areas)\n"
        "env.create_read()\n"
        "c = env.open()\n"
        "for p in ['/api/write/database-manager/runtime-role/preview',\n"
        "          '/api/write/database-manager/runtime-role/apply']:\n"
        "    body = {'connection_id': c, 'role': 'x', 'schema': 'public', 'create': True}\n"
        "    assert env.send('POST', p, body).status_code == 405\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write')))\n"
    )
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout.strip().splitlines()[-1].replace("'", '"'))
    assert "tabrion_studio.write.unlock" in loaded  # the write package is loaded: the check is not vacuous
    assert "tabrion_studio.write.database_manager.runtime_role" not in loaded


# --- 2. who -----------------------------------------------------------------------------------------------------------


def test_the_wizard_needs_an_admin_unlock_and_known_fields(tmp_path: Path, static_dir: Path) -> None:
    env = WriteEnv(tmp_path, static_dir, BOTH)
    try:
        env.create_read()
        connection_id = env.open()
        _refused(env.send("POST", PREVIEW, _body(connection_id)), 405, "studio.write_locked")
        response = env.send("POST", PREVIEW, _body(connection_id, password=CANARY_SECRET))
        assert response.status_code == 400 and CANARY_SECRET not in response.text  # the preview takes no password
        response = env.send("POST", APPLY, _body(connection_id, superuser=True, password=CANARY_SECRET))
        assert response.status_code == 400 and CANARY_SECRET not in response.text
    finally:
        env.close()


def test_a_sqlite_connection_has_no_runtime_role(tmp_path: Path, static_dir: Path) -> None:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    env = WriteEnv(tmp_path, static_dir, BOTH)
    try:
        env.create_read()
        connection_id = env.open()
        assert env.create_write(name="parts-admin", kind="admin").status_code == 201
        assert env.unlock(connection_id, "write:parts-admin", "parts-admin").status_code == 200
        _refused(
            env.send("POST", PREVIEW, _body(connection_id)),
            405,
            "studio.dbm_role_not_allowed",
            [("role.target", "role.engine")],
        )
    finally:
        env.close()


# --- 3. the request ---------------------------------------------------------------------------------------------------


def _invalid(code: str, issues: list[tuple[str, str]], **doc: Any) -> None:
    with pytest.raises(WriteError) as caught:
        runtime_role.Request(
            {"role": "app_rt", "schema": "public", "create": True, **doc},
            with_password=True,
        )
    assert caught.value.code == code
    assert caught.value.issues == issues


@pytest.mark.parametrize(
    ("doc", "code", "issues"),
    [
        ({"role": "app‮rt"}, "studio.dbm_role_name_invalid", [("role.role", "role.name_hidden")]),
        ({"role": "a" * 64}, "studio.dbm_role_name_invalid", [("role.role", "role.name_invalid")]),
        ({"role": ""}, "studio.dbm_role_name_invalid", [("role.role", "role.name_invalid")]),
        ({"schema": "pub​lic"}, "studio.dbm_role_name_invalid", [("role.schema", "role.name_hidden")]),
        ({"schema": None}, "studio.dbm_role_name_invalid", [("role.schema", "role.name_invalid")]),
        ({"create": "yes"}, "studio.dbm_role_invalid", [("role.create", "role.create_invalid")]),
        ({"create": None}, "studio.dbm_role_invalid", [("role.create", "role.create_invalid")]),
        (
            {"protected_tables": [f"t{i}" for i in range(runtime_role.MAX_PROTECTED + 1)]},
            "studio.dbm_role_invalid",
            [("role.protected_tables", "role.objects_invalid")],
        ),
        (
            {"protected_tables": "alembic_version"},
            "studio.dbm_role_invalid",
            [("role.protected_tables", "role.objects_invalid")],
        ),
        (
            {"protected_tables": ["alembic⁦version"]},
            "studio.dbm_role_name_invalid",
            [("role.protected_tables", "role.name_hidden")],
        ),
        (
            {"create": False, "password": CANARY_SECRET},
            "studio.dbm_role_invalid",
            [("role.password", "role.not_for_action")],
        ),
        ({"password": "x" * 1025}, "studio.dbm_role_invalid", [("role.password", "role.password_invalid")]),
    ],
)
def test_the_request_is_checked_before_the_server(
    doc: dict[str, Any], code: str, issues: list[tuple[str, str]]
) -> None:
    _invalid(code, issues, **doc)


def test_the_request_drops_duplicate_protected_tables_and_never_describes_the_password() -> None:
    request = runtime_role.Request(
        {
            "role": "app_rt",
            "schema": "app",
            "create": True,
            "protected_tables": ["alembic_version", "alembic_version", "flyway_schema_history"],
            "password": CANARY_SECRET,
        },
        with_password=True,
    )
    assert request.protected == ("alembic_version", "flyway_schema_history")
    described = request.described()
    assert described == {
        "role": "app_rt",
        "schema": "app",
        "protected_tables": ["alembic_version", "flyway_schema_history"],
        "create": True,
    }
    audited = request.audited(password_set=True)
    assert CANARY_SECRET not in json.dumps([described, audited])


# --- 4. audit fields --------------------------------------------------------------------------------------------------


def test_the_audit_fields_take_names_and_flags_only() -> None:
    request = runtime_role.Request(
        {"role": "appé", "schema": "app", "create": False, "protected_tables": ["alembic_version"]},
        with_password=False,
    )
    start = clean_fields({"action": runtime_role.ACTION, **request.audited(password_set=False)})
    assert start == {
        "action": "dbm.role.runtime",
        "objects": ["appé"],
        "schema": "app",
        "protected": ["alembic_version"],
        "created": False,
        "password_set": False,
    }
    assert clean_fields({"connect": True, "undone": False}) == {"connect": True, "undone": False}
