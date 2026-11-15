"""STU-006: schema tests of TOML version 1 (profile files and settings.toml, 03 section 4.3)."""

from __future__ import annotations

from collections.abc import Callable
import json
import tomllib
from typing import Any

import pytest

from tabrion_studio.config import files as files_module
from tabrion_studio.config.files import ConfigFileError, parse_toml
from tabrion_studio.config.schema import (
    MAX_PROFILES,
    SecretRef,
    Settings,
    is_host_list,
    is_loopback,
    parse_secret_ref,
    validate_profiles,
    validate_settings,
)

SPEC_EXAMPLE = """
version = 1
[[profile]]
name = "parts-dev"
engine = "sqlite"
path = "data/parts.db"
environment = "dev"

[[profile]]
name = "measurements-central"
engine = "postgresql"
host = "db.example.internal"
port = 5432
database = "measurements"
user = "studio_ro"
password = "keyring:"
sslmode = "verify-full"
environment = "prod"
read_only_role = true
statement_timeout_ms = 15000
"""


def _codes(doc: dict[str, Any]) -> set[tuple[str, str]]:
    return {(issue.pointer, issue.code) for issue in validate_profiles(doc).issues}


def _one(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"name": "p", "engine": "sqlite", "path": "a.db", "environment": "dev"}
    base.update(fields)
    return {"version": 1, "profile": [{k: v for k, v in base.items() if v is not None}]}


def test_the_example_of_the_spec_is_valid() -> None:
    result = validate_profiles(tomllib.loads(SPEC_EXAMPLE))
    assert result.ok and result.issues == []
    sqlite, pg = result.profiles
    assert sqlite.to_dict() == {
        "name": "parts-dev",
        "engine": "sqlite",
        "environment": "dev",
        "path": "data/parts.db",
        "password": None,
    }
    assert pg.port == 5432 and pg.sslmode == "verify-full" and pg.read_only_role is True
    assert pg.password == SecretRef("keyring", "")
    assert pg.statement_timeout_ms == 15000


def test_an_empty_file_of_version_1_has_no_profiles() -> None:
    result = validate_profiles({"version": 1})
    assert result.ok and result.profiles == []


@pytest.mark.parametrize(
    ("doc", "issue"),
    [
        ({}, ("version", "config.required")),
        ({"version": 2}, ("version", "config.unsupported_version")),
        ({"version": "1"}, ("version", "config.unsupported_version")),
        ({"version": True}, ("version", "config.unsupported_version")),
        ({"version": 1, "profile": {"name": "x"}}, ("profile", "config.type")),
        ({"version": 1, "profile": ["x"]}, ("profile[0]", "config.type")),
        (_one(name=None), ("profile[0].name", "config.required")),
        (_one(name=""), ("profile[0].name", "config.length")),
        (_one(name="x" * 201), ("profile[0].name", "config.length")),
        (_one(name="a\nb"), ("profile[0].name", "config.control_character")),
        (_one(name=7), ("profile[0].name", "config.type")),
        (_one(engine="mysql"), ("profile[0].engine", "config.choice")),
        (_one(engine=None), ("profile[0].engine", "config.required")),
        (_one(environment=None), ("profile[0].environment", "config.required")),
        (_one(environment="staging"), ("profile[0].environment", "config.choice")),
        (_one(path=None), ("profile[0].path", "config.required")),
        (_one(path="a\x00b"), ("profile[0].path", "config.control_character")),
        (_one(host="h"), ("profile[0].host", "config.not_for_engine")),
        (_one(statement_timeout_ms=0), ("profile[0].statement_timeout_ms", "config.range")),
        (_one(statement_timeout_ms=1.5), ("profile[0].statement_timeout_ms", "config.type")),
        (_one(statement_timeout_ms=True), ("profile[0].statement_timeout_ms", "config.type")),
        (_one(read_only_role="yes"), ("profile[0].read_only_role", "config.type")),
        (_one(engine="postgresql", path=None, database="d"), ("profile[0].host", "config.required")),
        (_one(engine="postgresql", path=None, host="h"), ("profile[0].database", "config.required")),
        (_one(engine="postgresql", host="h", database="d"), ("profile[0].path", "config.not_for_engine")),
        (_one(engine="postgresql", path=None, host="h", database="d", port=0), ("profile[0].port", "config.range")),
        (_one(engine="postgresql", path=None, host="h", database="d", port=70000), ("profile[0].port", "config.range")),
        (
            _one(engine="postgresql", path=None, host="a b", database="d"),
            ("profile[0].host", "config.control_character"),
        ),
        (
            _one(engine="postgresql", path=None, host="h", database="d", sslmode="on"),
            ("profile[0].sslmode", "config.choice"),
        ),
    ],
)
def test_invalid_files(doc: dict[str, Any], issue: tuple[str, str]) -> None:
    assert issue in _codes(doc)
    assert not validate_profiles(doc).ok


@pytest.mark.parametrize(
    "password", ["hunter2", "keyring:x", "env:", "env:1BAD", "env:A B", "file:", "file:a\nb", "plain:x", "", 42]
)
def test_a_plain_password_is_refused_and_never_echoed(password: object) -> None:
    result = validate_profiles(_one(password=password))
    assert ("profile[0].password", "config.plain_password") in {(i.pointer, i.code) for i in result.issues}
    assert result.profiles == []
    if isinstance(password, str) and len(password) > 4:
        assert password not in repr([i.to_dict() for i in result.issues])


@pytest.mark.parametrize(
    ("value", "ref"),
    [
        ("keyring:", SecretRef("keyring", "")),
        ("env:PGPASSWORD", SecretRef("env", "PGPASSWORD")),
        ("env:_x1", SecretRef("env", "_x1")),
        ("file:./secrets/db", SecretRef("file", "./secrets/db")),
    ],
)
def test_secret_references(value: str, ref: SecretRef) -> None:
    assert parse_secret_ref(value) == ref


def test_duplicate_names_and_too_many_profiles() -> None:
    doc = {"version": 1, "profile": [_one()["profile"][0], _one()["profile"][0]]}
    result = validate_profiles(doc)
    assert ("profile[1].name", "config.duplicate") in {(i.pointer, i.code) for i in result.issues}
    many = {"version": 1, "profile": [_one(name=f"p{i}")["profile"][0] for i in range(MAX_PROFILES + 1)]}
    assert ("profile", "config.too_many") in _codes(many)


def test_unknown_keys_are_warnings_with_escaped_names() -> None:
    doc = _one(extra=1)
    doc["top"] = 2
    doc["profile"][0]["evil\nkey"] = 3
    result = validate_profiles(doc)
    assert result.ok
    assert set(result.unknown_keys) == {"profile[0].extra", 'profile[0]."evil\\x0akey"', "top"}
    assert {i.severity for i in result.issues} == {"warning"}


def test_postgres_defaults_to_verify_full_for_remote_hosts() -> None:
    remote = validate_profiles(_one(engine="postgresql", path=None, host="db.internal", database="d")).profiles[0]
    local = validate_profiles(_one(engine="postgresql", path=None, host="localhost", database="d")).profiles[0]
    assert remote.sslmode == "verify-full" and local.sslmode == "prefer"


def test_settings_defaults_and_values() -> None:
    assert validate_settings({}) == (Settings(), [])
    settings, issues = validate_settings(
        {
            "version": 1,
            "language": "de",
            "theme": "dark",
            "density": "normal",
            "log_sql": True,
            "limits": {"max_rows": 500},
        }
    )
    assert issues == []
    assert settings.to_dict() == {
        "language": "de",
        "theme": "dark",
        "density": "normal",
        "log_sql": True,
        "telemetry": False,
        "limits": {"max_rows": 500, "statement_timeout_ms": 30000, "max_result_mb": 20},
        "history": {"enabled": True, "retention_days": 30},
    }


def test_invalid_settings_keep_their_defaults() -> None:
    settings, issues = validate_settings(
        {
            "language": "fr",
            "density": "tiny",
            "log_sql": "yes",
            "telemetry": True,
            "limits": {"max_rows": 0, "x": 1},
            "y": 2,
        }
    )
    assert settings == Settings()
    assert {(i.pointer, i.code) for i in issues} == {
        ("settings.language", "config.choice"),
        ("settings.density", "config.choice"),
        ("settings.log_sql", "config.type"),
        ("settings.telemetry", "config.telemetry_unsupported"),
        ("settings.limits.max_rows", "config.range"),
        ("settings.limits.x", "config.unknown_key"),
        ("settings.y", "config.unknown_key"),
    }
    assert validate_settings({"version": 9})[1][0].code == "config.unsupported_version"
    assert settings.to_dict()["telemetry"] is False


def test_syntax_errors_keep_only_the_line() -> None:
    with pytest.raises(ConfigFileError) as caught:
        parse_toml(b'version = 1\npassword = "hunter2\n')
    assert caught.value.code == "config.syntax" and caught.value.line == 2
    assert "hunter2" not in str(caught.value)
    with pytest.raises(ConfigFileError) as caught:
        parse_toml(b"\xff\xfe")
    assert caught.value.code == "config.encoding"


@pytest.mark.parametrize(
    "host",
    ["db.internal", "127.evil.example", "127.0.0.1.nip.io", "localhost.evil.example", "127.1", "0x7f.0.0.1", "::2"],
)
def test_names_that_only_look_like_loopback_are_remote(host: str) -> None:
    """Review T-4: only the literal localhost, a loopback IP address or a socket folder defaults to prefer."""
    assert not is_loopback(host)
    profile = validate_profiles(_one(engine="postgresql", path=None, host=host, database="d")).profiles[0]
    assert profile.sslmode == "verify-full"


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "127.0.0.1", "127.8.9.10", "::1", "[::1]", "/run/pg"])
def test_loopback_hosts(host: str) -> None:
    assert is_loopback(host)


#: libpq host lists (M-1 of the PR #117 review): a comma separates hosts that libpq tries in turn.
HOST_LISTS = [
    "/var/run/postgresql,db.attacker.example",
    "/var/run/postgresql,127.0.0.1",
    "localhost,db.attacker.example",
    "127.0.0.1,::1",
    "db.example.internal,",
    ",localhost",
]


@pytest.mark.parametrize("host", HOST_LISTS)
def test_a_host_list_is_never_loopback(host: str) -> None:
    """Defense in depth: even a list that starts with a socket folder or a loopback address is not loopback."""
    assert is_host_list(host)
    assert not is_loopback(host)


@pytest.mark.parametrize("host", ["/run/pg", "localhost", "db.example.internal", "::1", "[::1]"])
def test_a_single_host_is_not_a_list(host: str) -> None:
    assert not is_host_list(host)


@pytest.mark.parametrize("owner", ["project", "user"])
@pytest.mark.parametrize("host", HOST_LISTS)
def test_a_host_list_is_refused_in_every_profile_file(host: str, owner: str) -> None:
    """M-1 of the PR #117 review: refused with ``config.host_list`` in a project and in the user's file, so no
    profile with it exists and no sslmode default is derived from it; the value is not repeated."""
    doc = _one(engine="postgresql", path=None, host=host, database="d", password="keyring:")
    result = validate_profiles(doc, owner=owner)  # type: ignore[arg-type]
    assert [(issue.pointer, issue.code, issue.severity) for issue in result.issues] == [
        ("profile[0].host", "config.host_list", "error")
    ]
    assert result.profiles == []
    assert host not in json.dumps([issue.to_dict() for issue in result.issues])


@pytest.mark.parametrize(
    "fields",
    [
        {"path": "//attacker.example/share/x.db"},
        {"path": "\\\\attacker.example\\share\\x.db"},
        {"path": "\\\\?\\C:\\x.db"},
        {"path": "\\\\.\\pipe\\x"},
        {"path": "C:x.db"},
        {"path": "file://attacker.example/x.db"},
        {"path": "/net/attacker.example/x.db"},
        {"password": "file://attacker.example/s/x"},
        {"password": "file:\\\\attacker.example\\s\\x"},
        {"password": "file:C:secret"},
    ],
)
def test_network_and_device_paths_are_refused_by_the_schema(fields: dict[str, Any]) -> None:
    """Review T-2: refused as text with config.network_path, so nothing ever resolves them."""
    key = next(iter(fields))
    assert (f"profile[0].{key}", "config.network_path") in _codes(_one(**fields))


@pytest.mark.parametrize(
    "path", ["data/100%.db", "%2F%2Fattacker.example/share/x.db", "a.db?mode=rw", "a.db#x", "file:/a.db?mode=rwc"]
)
def test_url_characters_in_a_sqlite_path_are_refused(path: str) -> None:
    """TD-013: like tabrion 0.2's profilefile (``profilefile.value``, G-T2 B-1), a "?", "#" or "%" in a SQLite path
    is an error, so a Studio SQLite path is also valid for tabrion's reader and no URL reading can turn it into
    another path (the PostgreSQL ``database`` is not aligned, TD-013). Text only: the value is not repeated."""
    result = validate_profiles(_one(path=path))
    assert [(issue.pointer, issue.code, issue.severity) for issue in result.issues] == [
        ("profile[0].path", "config.url_character", "error")
    ]
    assert result.profiles == []
    assert path not in json.dumps([issue.to_dict() for issue in result.issues])


def test_url_characters_elsewhere_are_not_refused() -> None:
    """Only the SQLite path: a "#" or "%" in a name, and a plain path, stay valid."""
    assert _codes(_one(name="#1 at 100%")) == set()
    assert _codes(_one(path="data/parts-2026.db")) == set()


def test_deeply_nested_toml_is_too_deep_not_a_crash() -> None:
    """Review T-3: 1.8 KB of nested arrays used to raise RecursionError out of the parser."""
    with pytest.raises(ConfigFileError) as caught:
        parse_toml(b"x = " + b"[" * 5000 + b"]" * 5000)
    # A parser with its own nesting limit reports a syntax error instead; either way the file is invalid.
    assert caught.value.code in ("config.too_deep", "config.syntax")


def _path(segments: int, name: str = "a") -> str:
    return ".".join([name] * segments)


def test_too_deep_toml_never_reaches_the_recursive_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file nested deeper than ``MAX_NESTING`` is refused before tomllib runs, so the parser never recurses to the
    interpreter's limit, where a finalizer the garbage collector runs (an event loop's ``__del__``) failed with its
    own RecursionError (CI on Windows, Python 3.13). Dotted keys and table headers nest as deep as brackets do
    (review L-2 of #120: tomllib recurses once per segment, RecursionError from about 1000 on 3.13)."""

    def parser(text: str) -> dict[str, Any]:
        raise AssertionError("tomllib must not run")

    monkeypatch.setattr(tomllib, "loads", parser)
    nested = [
        "x = " + "[" * 900 + "]" * 900,
        "x = " + "{a = " * 65 + "1" + "}" * 65,
        f"{_path(1000)} = 1",
        f"[{_path(1000)}]",
        f"[[{_path(1000)}]]",
        "x = " + "{a.b = " * 33 + "1" + "}" * 33,
        f"[{_path(40, 't')}]\n{_path(30, 'k')} = 1",
    ]
    for text in nested:
        with pytest.raises(ConfigFileError) as caught:
            parse_toml(text.encode())
        assert caught.value.code == "config.too_deep", text[:40]


@pytest.mark.parametrize(
    ("text", "nesting"),
    [
        ("x = [[1], {a = [2]}]", 3),
        ('[[profile]]\nname = "a[[["\n', 2),
        ("x = '[[[['", 0),
        ('x = """a\\"""[[[["""', 0),
        ("x = '''[[[''''' \ny = [1]", 1),
        ("# [[[[\nx = [1]", 1),
        ('x = "\\\\"\ny = [[1]]', 2),
        ('x = """a"""""\ny = [1]', 1),
        ('x = "unclosed [[[[', 0),
        # The dots of a key, of the table header and of the keys that hold an open bracket or brace count.
        ("a.b.c = 1", 2),
        ("[a.b]\nc.d = 1", 3),
        ("[[a.b]]\nc = 1", 3),
        ("x.y = {a.b = {c = [1]}}", 5),
        # A dot in a quoted key, a value or a comment does not.
        ("\"a.b.c\".d = 1\n['x.y.z']", 1),
        ("x = 1.5\ny = [2.25, {a = 3.5}]", 2),
        ("x = [1,\n  2.5] # a.b.c\na.b = 1", 1),
    ],
)
def test_the_nesting_scan_skips_comments_strings_and_values(text: str, nesting: int) -> None:
    assert files_module._nesting(text) == nesting


@pytest.mark.parametrize(
    "file",
    [
        lambda depth: "x = " + "[" * depth + "]" * depth,
        lambda depth: _path(depth + 1) + " = 1",
        lambda depth: f"[{_path(depth)}]",
        lambda depth: f"[[{_path(depth - 1)}]]",
    ],
)
def test_the_deepest_allowed_nesting_parses(file: Callable[[int], str]) -> None:
    deepest = files_module.MAX_NESTING
    assert parse_toml(file(deepest).encode())
    with pytest.raises(ConfigFileError) as caught:
        parse_toml(file(deepest + 1).encode())
    assert caught.value.code == "config.too_deep"


def test_quoted_keys_with_dots_are_one_level_each() -> None:
    dotted = "." * 1000
    assert parse_toml(f'"{dotted}" = 1\n["{dotted}x"]\n\'{dotted}\'.b = 2'.encode()) == {
        dotted: 1,
        dotted + "x": {dotted: {"b": 2}},
    }


def test_the_nesting_scan_is_linear_on_hostile_text() -> None:
    hostile = (
        '"' * 256_000,
        '"""' + "x\\" * 100_000,
        "'''" * 80_000,
        '"\\' * 120_000,
        "[" * 256_000,
        "a." * 128_000,
        "x = [" + "1.5," * 60_000 + "]",
        "\n" * 256_000,
        "," * 256_000,
        "x = [" + "{}," * 80_000 + "]",
    )
    for text in hostile:
        files_module._nesting(text)  # a quadratic scan would take minutes here
