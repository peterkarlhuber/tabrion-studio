"""STU-004: the problem catalog and the problem answers of the app (03 sections 4.5 and 6)."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
from tabrion.errors import Kind, classify

from tabrion_studio.problems import (
    CATALOG,
    DB_CATALOG,
    DB_KINDS,
    PROBLEM_MEDIA_TYPE,
    ProblemKind,
    code_for_status,
    db_problem,
    is_problem_code,
    new_log_id,
    problem,
    well_formed_code,
)
from tests.support.access import ORIGIN, make_studio

SRC = Path(__file__).resolve().parents[2] / "src" / "tabrion_studio"


@pytest.mark.parametrize("code", sorted(CATALOG))
def test_catalog_entries_are_fixed_plain_texts(code: str) -> None:
    entry = CATALOG[code]
    assert well_formed_code(code)
    assert 400 <= entry.status < 600
    assert code.startswith("access.") or code.startswith("studio.")
    for text in (entry.title, entry.detail, entry.hint or "x"):
        assert text.isascii() and text.strip() == text and text
        # No format placeholder and nothing that looks like a path or a trace.
        assert not re.search(r"[{}%]|\.py\b|Traceback|[A-Za-z]:\\\\|/home/|/Users/", text), text


def test_every_code_the_code_base_sends_is_in_the_catalog() -> None:
    """A refusal or route that names an unknown code would raise; this finds it before a user does."""
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'return "(access\.[a-z_]+)"', text))
        found |= set(re.findall(r'_?problem(?:_response)?\("((?:access|studio)\.[a-z_]+)"', text))
    found |= {f"access.launch_{result}" for result in ("used", "expired", "invalid")}
    assert found
    assert found <= set(CATALOG), found - set(CATALOG)


def test_problem_document_shape() -> None:
    doc = problem("access.session_required").to_dict()
    assert doc == {
        "code": "access.session_required",
        "kind": "access",
        "status": 401,
        "title": "Not signed in",
        "detail": "This request needs a Studio session.",
        "hint": "Run tabrion-studio again; it opens Studio with a new sign-in link.",
    }
    log_id = new_log_id()
    assert re.fullmatch(r"[0-9a-f]{12}", log_id)
    assert problem("studio.internal_error", log_id=log_id).to_dict()["log_id"] == log_id
    assert problem("studio.internal_error", sqlstate="23505", position=0).to_dict()["sqlstate"] == "23505"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": "studio.whatever"},
        {"code": "access.session_required\nX"},
        {"code": "studio.internal_error", "log_id": "/home/user/x"},
        {"code": "studio.internal_error", "log_id": "ABCDEF123456"},
        {"code": "studio.internal_error", "sqlstate": "23505 DETAIL: Key (email)=(x)"},
        {"code": "studio.internal_error", "sqlstate": "2350"},
        {"code": "studio.internal_error", "position": -1},
        {"code": "studio.internal_error", "position": True},
        {"code": "studio.internal_error", "position": "3"},
    ],
)
def test_problem_refuses_free_text(kwargs: dict[str, object]) -> None:
    """Nothing but catalog codes and well-formed IDs can enter an answer."""
    code = kwargs.pop("code")
    with pytest.raises(ValueError):
        problem(code, **kwargs)  # type: ignore[arg-type]


def test_codes_and_kinds() -> None:
    assert well_formed_code("db.unique") and well_formed_code("db.read_only")
    assert not well_formed_code("db.nonsense") and not well_formed_code("other.x") and not well_formed_code("studio.")
    assert {kind.value for kind in ProblemKind}.isdisjoint(DB_KINDS - {"unknown"})
    assert {kind.value for kind in Kind} == DB_KINDS  # tabrion.errors.Kind itself, no mirror (TS-001, STU-009)
    assert len(DB_KINDS) == 20  # doc 04 section 1.1
    assert is_problem_code("db.read_only") and not is_problem_code("db.nonsense")
    assert is_problem_code("studio.not_found") and not is_problem_code("x") and not is_problem_code(None)
    assert [code_for_status(s) for s in (404, 405, 400, 413, 500, 503)] == [
        "studio.not_found",
        "studio.method_not_allowed",
        "studio.bad_request",
        "studio.bad_request",
        "studio.internal_error",
        "studio.internal_error",
    ]


def test_refusals_are_problem_documents(static_dir: Path) -> None:
    client = make_studio(static_dir).client
    response = client.get("/api/heartbeat")
    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json() == problem("access.session_required").to_dict()
    foreign = client.get("/", headers={"Host": "evil.example"})
    assert foreign.json() == problem("access.host_not_allowed").to_dict()
    assert "evil.example" not in foreign.text


def test_framework_errors_are_problem_documents(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    client = studio.client
    missing = client.get("/missing.js")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert missing.json()["code"] == "studio.not_found"
    assert missing.headers["cache-control"] == "no-store"
    # A wrong method on an API route: 405 with Allow, as a problem.
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    wrong = client.delete("/api/heartbeat", headers={**headers, "Content-Type": "application/json"})
    assert wrong.status_code == 405
    assert wrong.json()["code"] == "studio.method_not_allowed"
    assert "GET" in wrong.headers["allow"]
    assert wrong.headers["content-type"] == PROBLEM_MEDIA_TYPE


def test_issues_carry_fixed_key_names_and_config_codes_only() -> None:
    """STU-008: the issues of a refused form are key paths of fixed names and config.* codes, never a value."""
    doc = problem("studio.profile_invalid", issues=[("profile.host", "config.required")]).to_dict()
    assert doc["issues"] == [{"pointer": "profile.host", "code": "config.required"}]
    assert "issues" not in problem("studio.profile_invalid").to_dict()
    for bad in (
        [("profile.Host name", "config.required")],
        [("profile.host", "my password")],
        [("other.host", "config.required")],
        [("profile.host", "config.required")] * 33,
    ):
        with pytest.raises(ValueError):
            problem("studio.profile_invalid", issues=bad)


def test_every_database_kind_has_fixed_texts() -> None:
    assert set(DB_CATALOG) == DB_KINDS
    for entry in DB_CATALOG.values():
        assert 400 <= entry.status < 600
        for text in (entry.title, entry.detail, entry.hint or "x"):
            assert text.isascii() and text.strip() == text and not re.search(r"[{}%]", text)


def test_a_database_problem_takes_only_kind_and_sqlstate_from_the_error() -> None:
    """STU-009: db.<kind> from tabrion.errors.classify; the server's message, DETAIL and values never reach it."""
    import sqlite3

    canary = "tabrion-canary-db-problem"
    error = sqlite3.IntegrityError(f"UNIQUE constraint failed: users.email ({canary})")
    error.sqlite_errorname = "SQLITE_CONSTRAINT_UNIQUE"
    doc = db_problem(error).to_dict()
    assert doc["code"] == "db.unique" and doc["kind"] == "unique" and doc["status"] == 409
    assert canary not in json.dumps(doc) and "users" not in json.dumps(doc)
    assert db_problem(classify(error)).to_dict() == doc
    assert db_problem(RuntimeError(canary)).to_dict()["code"] == "db.unknown"
    with pytest.raises(ValueError):
        db_problem(error, log_id="not-hex")
