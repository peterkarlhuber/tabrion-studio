"""STU-XDM-4: PostgreSQL role management in the Database Manager, without a server (GS4-01, GS4-02, GS4-13, GS4-17,
GS4-18; STU-ADR-0008 sections 6 and 10; TX-40..TX-44). The rules that need a server are in
``test_dbm_roles_postgres.py``.

1. **Kill switch (GS4-01, GS4-02).** Off by default: with no flag, or with only the other areas' flags, the three
   routes answer 405 and the module is not imported (a fresh interpreter).
2. **Who (E-X6).** No unlock: ``studio.write_locked``. A SQLite connection has no roles (``role.engine``). A field
   the route does not know (a password in the preview) is refused before anything runs.
3. **The request.** Banned attributes (SUPERUSER, BYPASSRLS, CREATEROLE, REPLICATION, ADMIN or GRANT option), TRIGGER,
   a member equal to the role, names with characters that cannot be seen, fields that do not belong to the action,
   an over-long password: refused with fixed problems, never echoing a value.
4. **Names (P90-05).** ``display_name`` escapes every invisible character as ``<U+XXXX>``.
5. **Tabrion's refusals** become ``studio.dbm_role_unsafe`` with fixed ``role.<reason>`` issues; a refused caller
   (``postgres.caller_unsafe``) is ``studio.admin_over_privileged``.

Python 3.11 refuses every SQLite write unlock (no defensive mode), so the SQLite admin-unlock test skips there.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
from tabrion.errors import RoleUnsafe

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.audit import clean_fields
from tabrion_studio.write.connection import WriteError, defensive_available
from tabrion_studio.write.database_manager import roles
from tests.support.write import CANARY_SECRET, DATABASE_MANAGER, WriteEnv

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
LIST = "/api/write/database-manager/roles"
PREVIEW = "/api/write/database-manager/roles/preview"
APPLY = "/api/write/database-manager/roles/apply"
OTHER_AREAS = WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}))


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_SECRET not in response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in response.json().get("issues", [])] == issues


def _request(**doc: Any) -> roles.Request:
    return roles.Request({"action": "create", "role": "app", **doc}, with_password=True)


def _invalid(code: str, issues: list[tuple[str, str]], **doc: Any) -> None:
    with pytest.raises(WriteError) as caught:
        _request(**doc)
    assert caught.value.code == code
    assert caught.value.issues == issues


# --- 1. the kill switch (GS4-01, GS4-02) --------------------------------------------------------------------------


@pytest.mark.parametrize("areas", [None, OTHER_AREAS], ids=["default", "other-areas"])
@pytest.mark.parametrize("path", [LIST, PREVIEW, APPLY])
def test_the_roles_are_off_by_default_and_with_another_areas_flag(
    tmp_path: Path, static_dir: Path, areas: WriteAreas | None, path: str
) -> None:
    env = WriteEnv(tmp_path, static_dir) if areas is None else WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        connection_id = env.open()
        body = {"connection_id": connection_id, "action": "drop", "role": "app", "typed": "app", "state": "0" * 64}
        _refused(env.send("POST", path, body if path != LIST else {"connection_id": connection_id}), 405,
                 "studio.method_not_allowed")  # fmt: skip
        assert env.send("GET", "/api/write/areas").json()["areas"]["database_manager"] is False
    finally:
        env.close()


def test_with_the_flag_off_the_roles_module_is_not_imported(tmp_path: Path) -> None:
    """GS4-01: the other areas on, the Database Manager off: the role routes answer 405 and the module is not loaded."""
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        "areas = WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}))\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), areas)\n"
        "env.create_read()\n"
        "c = env.open()\n"
        "for p in ['/api/write/database-manager/roles', '/api/write/database-manager/roles/preview',\n"
        "          '/api/write/database-manager/roles/apply']:\n"
        "    assert env.send('POST', p, {'connection_id': c, 'action': 'drop', 'role': 'x'}).status_code == 405\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith(('tabrion_studio.write', 'tabrion.postgres'))))\n"
    )
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout.strip().splitlines()[-1].replace("'", '"'))
    assert "tabrion_studio.write.unlock" in loaded  # the write package is loaded: the check is not vacuous
    assert "tabrion_studio.write.database_manager.roles" not in loaded
    assert "tabrion_studio.write.database_manager" not in loaded


# --- 2. who (E-X6) ----------------------------------------------------------------------------------------------------


def test_the_roles_need_an_admin_unlock_and_known_fields(tmp_path: Path, static_dir: Path) -> None:
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    try:
        env.create_read()
        connection_id = env.open()
        _refused(env.send("POST", LIST, {"connection_id": connection_id}), 405, "studio.write_locked")
        body = {"connection_id": connection_id, "action": "create", "role": "app", "password": CANARY_SECRET}
        response = env.send("POST", PREVIEW, body)  # the preview never takes a password
        assert response.status_code == 400 and CANARY_SECRET not in response.text
        response = env.send("POST", APPLY, {**body, "superuser": True})
        assert response.status_code == 400 and CANARY_SECRET not in response.text
    finally:
        env.close()


def test_a_sqlite_connection_has_no_roles(tmp_path: Path, static_dir: Path) -> None:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    try:
        env.create_read()
        connection_id = env.open()
        assert env.create_write(name="parts-admin", kind="admin").status_code == 201
        assert env.unlock(connection_id, "write:parts-admin", "parts-admin").status_code == 200
        for path in (LIST, PREVIEW, APPLY):
            body = {"connection_id": connection_id}
            if path != LIST:
                body |= {"action": "drop", "role": "app"}
            _refused(env.send("POST", path, body), 405, "studio.dbm_role_not_allowed", [("role.target", "role.engine")])
    finally:
        env.close()


# --- 3. the request ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(roles.BANNED_OPTIONS))
def test_a_banned_attribute_is_never_accepted(key: str) -> None:
    _invalid("studio.dbm_role_attribute_banned", [("role.options", "role.attribute_banned")], options={key: True})
    _invalid("studio.dbm_role_attribute_banned", [("role.options", "role.attribute_banned")], options={key: False})


def test_only_the_options_tabrion_takes_are_accepted() -> None:
    request = _request(options={"login": True, "inherit": False, "create_db": True, "connection_limit": 3,
                                "valid_until": "2030-01-01T00:00:00+00:00"})  # fmt: skip
    assert request.options.login is True and request.options.inherit is False and request.options.create_db is True
    assert request.options.connection_limit == 3 and request.options.valid_until.year == 2030
    bad = ("role.options", "role.options_invalid")
    _invalid("studio.dbm_role_invalid", [bad], options={"login": "yes"})
    _invalid("studio.dbm_role_invalid", [bad], options={"password_encryption": "md5"})
    _invalid("studio.dbm_role_invalid", [bad], options=["login"])
    _invalid("studio.dbm_role_invalid", [("role.options", "role.connection_limit")], options={"connection_limit": -2})
    _invalid("studio.dbm_role_invalid", [("role.options", "role.valid_until")], options={"valid_until": "2030-01-01"})
    _invalid("studio.dbm_role_invalid", [("role.password", "role.password_invalid")], password="x" * 1025)
    _invalid("studio.dbm_role_invalid", [("role.password", "role.password_invalid")], password=7)


def test_the_request_fits_its_action() -> None:
    _invalid("studio.dbm_role_invalid", [("role.action", "role.action_invalid")], action="rename")
    _invalid(
        "studio.dbm_role_invalid", [("role.member", "role.member_is_role")], action="grant_membership", member="app"
    )
    _invalid("studio.dbm_role_invalid", [("role.options", "role.nothing_to_change")], action="alter")
    _invalid("studio.dbm_role_invalid", [("role.member", "role.not_for_action")], action="drop", member="other")
    _invalid("studio.dbm_role_invalid", [("role.password", "role.not_for_action")], action="drop", password="pw")
    _invalid("studio.dbm_role_invalid", [("role.on", "role.on_invalid")], action="grant_privileges", on="column")
    grant = {"action": "grant_privileges", "on": "table", "objects": ["app.parts"]}
    _invalid("studio.dbm_role_unsafe", [("role.privileges", "role.trigger")], **grant, privileges=["TRIGGER"])
    _invalid("studio.dbm_role_invalid", [("role.privileges", "role.privileges_invalid")], **grant, privileges=["ALL"])
    _invalid("studio.dbm_role_invalid", [("role.privileges", "role.privileges_invalid")], **grant,
             privileges=["SELECT", "SELECT"])  # fmt: skip
    _invalid("studio.dbm_role_invalid", [("role.objects", "role.objects_invalid")], action="grant_privileges",
             on="table", privileges=["SELECT"], objects=[])  # fmt: skip
    revoke = roles.Request(
        {"action": "revoke_privileges", "role": "app", "on": "table", "privileges": ["TRIGGER"], "objects": ["a.b"]},
        with_password=False,
    )
    assert revoke.privileges == ("TRIGGER",)  # revoking TRIGGER removes a K-2 finding
    membership = roles.Request({"action": "revoke_membership", "role": "grp", "member": "app"}, with_password=False)
    assert membership.confirm == "app"  # the role whose rights change is typed


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("", "role.name_invalid"),
        (" app", "role.name_invalid"),
        ("a" * 64, "role.name_invalid"),
        ("ä" * 32, "role.name_invalid"),  # 64 bytes
        ("app\u202e", "role.name_hidden"),
        ("app\u200b", "role.name_hidden"),
        ("ap\u00a0p", "role.name_hidden"),
        ("app\u2028", "role.name_hidden"),
        (7, "role.name_invalid"),
    ],
)
def test_a_name_that_cannot_be_typed_is_refused(name: object, reason: str) -> None:
    _invalid("studio.dbm_role_name_invalid", [("role.role", reason)], role=name)
    _invalid("studio.dbm_role_name_invalid", [("role.member", reason)], action="grant_membership", member=name)


def test_the_described_request_never_holds_the_password() -> None:
    request = _request(password=CANARY_SECRET, options={"login": True})
    assert CANARY_SECRET not in json.dumps(request.described())
    assert request.described()["options"]["login"] is True
    audited = request.audited(password_set=True)
    assert CANARY_SECRET not in json.dumps(audited)
    assert audited == {"objects": ["app"], "password_set": True, "options_set": ["login"]}
    clean_fields(audited)  # every field is one the audit log takes (FIELDS)
    grant = roles.Request(
        {"action": "grant_privileges", "role": "app", "on": "table", "privileges": ["SELECT"], "objects": ["a.b"]},
        with_password=False,
    )
    clean_fields(grant.audited(password_set=False))


def test_alter_names_every_option() -> None:
    """Tabrion's ``alter_role`` sets every field, so a field left out would fall back to its default unseen."""
    _invalid("studio.dbm_role_invalid", [("role.options", "role.options_incomplete")], action="alter", options={})
    _invalid(
        "studio.dbm_role_invalid",
        [("role.options", "role.options_incomplete")],
        action="alter",
        options={"login": True},
    )
    full = {"login": True, "inherit": True, "create_db": False, "connection_limit": None, "valid_until": None}
    assert _request(action="alter", options=full).options_set == tuple(sorted(full))


# --- 4. names (P90-05) ------------------------------------------------------------------------------------------------


def test_every_invisible_character_is_shown_as_a_code_point() -> None:
    assert roles.display_name("app") == "app"
    assert roles.display_name("a b") == "a b"
    assert roles.display_name("adm\u202enimda") == "adm<U+202E>nimda"
    assert roles.display_name("x\u200by\u2028z\u00a0\x07") == "x<U+200B>y<U+2028>z<U+00A0><U+0007>"


# --- 5. tabrion's refusals --------------------------------------------------------------------------------------------


def test_tabrions_reasons_become_fixed_issues() -> None:
    found = roles.refusal(RoleUnsafe("postgres.role_unsafe", details={"reasons": "reachable_superuser,owns_objects,x"}))
    assert found.code == "studio.dbm_role_unsafe"
    assert found.issues == [
        ("role.target", "role.reachable_superuser"),
        ("role.target", "role.owns_objects"),
        ("role.target", "role.refused"),
    ]
    caller = roles.refusal(RoleUnsafe("postgres.caller_unsafe", details={"reasons": "superuser"}))
    assert caller.code == "studio.admin_over_privileged" and caller.issues is None


def test_every_reason_and_issue_is_a_valid_problem_issue() -> None:
    from tabrion_studio.problems import problem

    for reason in sorted(roles.REASONS):
        problem("studio.dbm_role_unsafe", issues=[("role.target", f"role.{reason}")])  # raises when not valid
    for issue in [("role.options", "role.attribute_banned"), ("role.objects", "role.grant_not_allowed")]:
        problem("studio.dbm_role_invalid", issues=[issue])


def test_differences_name_the_fields_that_changed() -> None:
    inside = {"role": "app", "member_of": ["grp"], "dangerous": [], "safe": True}
    assert roles._differences(inside, dict(inside)) == []
    assert roles._differences(inside, {**inside, "member_of": ["grp", "other"]}) == ["member_of"]
    assert roles._differences(None, None) == []
    assert roles._differences(inside, None) == ["role"]
