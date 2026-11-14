"""The findings of the privilege check of write and admin connections (TX-11, E-X6), without a server.

Review I-1 of PR #111, consistent with tabrion ADR 0015 D-1 and D-2: besides the four attributes, CREATEROLE before
PostgreSQL 16 is ``dangerous`` (``admin.createrole_pre16``), and membership in a predefined role, held directly or
through another role, is ``admin.broad:<role>`` (the server-file roles ``dangerous``). A role that is not predefined
is never named. ``test_write_postgres.py`` proves the same against a server.
"""

from __future__ import annotations

from typing import Any

import pytest

from tabrion_studio.write.connection import SERVER_FILE_ROLES, privilege_findings

pytestmark = pytest.mark.security


def _row(
    *,
    own: bool = False,
    superuser: bool = False,
    createrole: bool = False,
    predefined: str | None = None,
    member: bool = False,
) -> tuple[Any, ...]:
    """One row of the privilege query: own, the four attributes, createdb, owned databases, predefined, member."""
    return (own, superuser, False, createrole, False, False, 0, predefined, member)


def _codes(findings: list[dict[str, str]]) -> dict[str, tuple[str, str]]:
    return {f["code"]: (f["via"], f["severity"]) for f in findings}


def test_a_plain_role_has_no_finding() -> None:
    facts, findings = privilege_findings([_row(own=True)], 160004)
    assert findings == [] and facts["reachable_roles"] == 0 and facts["server_version"] == 160004


@pytest.mark.parametrize(
    ("version", "pre16"),
    [(150008, True), (140013, True), (160000, False), (170002, False), (None, True)],
)
def test_createrole_before_16_is_dangerous(version: int | None, pre16: bool) -> None:
    """D-1: before 16 CREATEROLE can grant membership in almost any role; an unknown version counts as before 16."""
    for rows, via in (
        ([_row(own=True, createrole=True)], "direct"),
        ([_row(own=True), _row(createrole=True)], "inherited"),
    ):
        codes = _codes(privilege_findings(rows, version)[1])
        assert codes["admin.createrole"] == (via, "warning")
        if pre16:
            assert codes["admin.createrole_pre16"] == (via, "dangerous")
        else:
            assert "admin.createrole_pre16" not in codes


@pytest.mark.parametrize(
    "role",
    sorted(
        {
            "pg_write_all_data",
            "pg_read_all_data",
            "pg_read_server_files",
            "pg_write_server_files",
            "pg_execute_server_program",
            "pg_monitor",
            "pg_signal_backend",
        }
    ),
)
def test_membership_in_a_predefined_role_is_reported_broad(role: str) -> None:
    """D-2: ``admin.broad:<role>``, direct when the session's role is the member, else inherited."""
    severity = "dangerous" if role in SERVER_FILE_ROLES else "warning"
    direct = _codes(privilege_findings([_row(own=True), _row(predefined=role, member=True)], 160000)[1])
    assert direct == {f"admin.broad:{role}": ("direct", severity)}
    inherited = _codes(privilege_findings([_row(own=True), _row(), _row(predefined=role)], 160000)[1])
    assert inherited == {f"admin.broad:{role}": ("inherited", severity)}
    # Reached both ways: direct wins.
    both = _codes(
        privilege_findings([_row(own=True), _row(predefined=role), _row(predefined=role, member=True)], 160000)[1]
    )
    assert both[f"admin.broad:{role}"][0] == "direct"


def test_the_server_file_roles_are_dangerous() -> None:
    assert {"pg_execute_server_program", "pg_read_server_files", "pg_write_server_files"} == SERVER_FILE_ROLES


@pytest.mark.parametrize("name", ["app_admin", "pg_", "PG_WRITE_ALL_DATA", "pg_x; drop", "pg_" + "a" * 64, 42])
def test_no_other_role_name_is_ever_reported(name: object) -> None:
    """Only a name as PostgreSQL spells its predefined roles; the session's own row never counts."""
    rows = [_row(own=True, predefined="pg_write_all_data"), _row(predefined=name, member=True)]  # type: ignore[arg-type]
    assert privilege_findings(rows, 160000)[1] == []


def test_attributes_and_predefined_roles_together() -> None:
    rows = [
        _row(own=True, superuser=True),
        _row(createrole=True),
        _row(predefined="pg_write_server_files"),
        _row(predefined="pg_read_all_data", member=True),
    ]
    codes = _codes(privilege_findings(rows, 150000)[1])
    assert codes == {
        "admin.superuser": ("direct", "warning"),
        "admin.createrole": ("inherited", "warning"),
        "admin.createrole_pre16": ("inherited", "dangerous"),
        "admin.broad:pg_read_all_data": ("direct", "warning"),
        "admin.broad:pg_write_server_files": ("inherited", "dangerous"),
    }
