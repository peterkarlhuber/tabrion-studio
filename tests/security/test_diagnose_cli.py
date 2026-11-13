"""``tabrion-studio diagnose``: the diagnosis report on the command line (03 section 1, STU-025, TD-036).

The command writes the report of :class:`.DiagnosisReports` (the route's allowlist) for one profile, without a
server. A PROD profile needs ``--confirm-prod`` (D-02), checked again by the registry on the profile it connects to;
a project profile needs the project's trust from Studio (D-01); refusals are the problem catalog's fixed words.
Canaries in the file's folder and name, the password and the environment never reach the output.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any
import uuid

import pytest
import sqlalchemy
from tabrion.check import Finding, Severity

from tabrion_studio import cli, diagnose_cli
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.instance import project_key
from tabrion_studio.logs import forget_secrets
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
CANARY = f"tabrion-canary-{uuid.uuid4().hex[:12]}"


class Env:
    """A project folder, a state and a configuration folder, and an SQLite file with rows that break a key."""

    def __init__(self, tmp_path: Path) -> None:
        self.project = tmp_path / "project"
        (self.project / ".tabrion").mkdir(parents=True)
        self.state = tmp_path / "state"
        self.state.mkdir(mode=0o700)
        self.config = tmp_path / "config"
        self.config.mkdir(mode=0o700)
        folder = tmp_path / CANARY
        folder.mkdir()
        self.db = folder / f"{CANARY}.db"
        connection = sqlite3.connect(self.db)
        connection.executescript(
            "CREATE TABLE p (id INTEGER PRIMARY KEY); "
            "CREATE TABLE c (id INTEGER PRIMARY KEY, p INTEGER REFERENCES p(id)); "
            "INSERT INTO c VALUES (1, 7), (2, 8);"
        )
        connection.commit()
        connection.close()
        self.db.chmod(0o600)

    def profiles(self, text: str) -> None:
        file = self.config / "profiles.toml"
        file.write_text("version = 1\n" + text, encoding="utf-8")
        file.chmod(0o600)

    def sqlite_profile(self, name: str = "shop", environment: str = "dev") -> str:
        return f'[[profile]]\nname = "{name}"\nengine = "sqlite"\npath = "{self.db.as_posix()}"\n' + (
            f'environment = "{environment}"\n'
        )

    def run(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = diagnose_cli.main(
            [str(self.project), "--state-dir", str(self.state), "--config-dir", str(self.config), *args], out, err
        )
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def env(tmp_path: Path) -> Any:
    try:
        yield Env(tmp_path)
    finally:
        forget_secrets()


def _no_secret(text: str, *names: str) -> None:
    for name in (CANARY, *names):
        assert name not in text


def test_the_markdown_report_goes_to_standard_output(env: Env) -> None:
    env.profiles(env.sqlite_profile())
    code, out, err = env.run("--profile", "user:shop")
    assert code == diagnose_cli.EXIT_OK, err
    assert out.startswith("# Tabrion Studio diagnosis report")
    assert "STU-DX-005" in out and "finds 2 rows whose foreign key points nowhere" in out
    assert "STU-BK-001" in out  # no backup next to the file
    _no_secret(out + err, str(env.db.parent))


def test_the_json_report_by_name_to_a_file_carries_studios_params(env: Env, tmp_path: Path) -> None:
    env.profiles(env.sqlite_profile())
    target = tmp_path / "report.json"
    code, out, err = env.run("--profile", "shop", "--format", "json", "--output", str(target))
    assert code == diagnose_cli.EXIT_OK, err
    assert out == "" and f"Report written to {target}" in err
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["report"] == "tabrion-studio-diagnosis" and doc["connection"]["environment"] == "dev"
    found = {item["code"]: item for item in doc["diagnosis"]["findings"]}
    # TD-032: Studio's facts behind its English text are in the JSON report too.
    assert found["STU-DX-005"]["params"] == {"rows": 2} and found["STU-DX-005"]["source"] == "studio"
    assert doc["backup"]["hints"][0]["params"] == {}
    _no_secret(target.read_text(encoding="utf-8") + err)


def test_the_cli_entry_point_dispatches_and_names_the_subcommand(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.profiles(env.sqlite_profile())
    out = io.StringIO()
    args = ["diagnose", str(env.project), "--state-dir", str(env.state), "--config-dir", str(env.config)]
    assert cli.main([*args, "--profile", "user:shop", "--format", "json"], out) == 0
    assert json.loads(out.getvalue())["connection"]["engine"] == "sqlite"
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0 and "tabrion-studio diagnose --help" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        cli.main(["diagnose", "--help"])
    assert exc.value.code == 0 and "--confirm-prod" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        cli.main(["diagnose", str(env.project), "--profile", "x", "--format", "html"])
    assert exc.value.code == 2
    capsys.readouterr()


def test_prod_needs_confirm_prod_and_opens_nothing_without_it(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.profiles(env.sqlite_profile(environment="prod"))
    opened: list[str] = []
    real_open = ConnectionRegistry.open

    def counting_open(self: ConnectionRegistry, profile_id: str, confirmed_environment: str | None = None) -> Any:
        opened.append(profile_id)
        return real_open(self, profile_id, confirmed_environment)

    monkeypatch.setattr(ConnectionRegistry, "open", counting_open)
    code, out, err = env.run("--profile", "user:shop")
    assert code == diagnose_cli.EXIT_USAGE and out == "" and opened == []
    assert "marked as PROD" in err and "--confirm-prod" in err
    code, out, err = env.run("--profile", "user:shop", "--confirm-prod", "--format", "json")
    assert code == diagnose_cli.EXIT_OK, err
    assert json.loads(out)["connection"]["environment"] == "prod" and opened == ["user:shop"]


def test_a_profile_that_becomes_prod_before_it_opens_is_refused(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-02 is the registry's check on the read it connects to (TD-038), not only the command's early question: the
    profile is DEV when the command looks and PROD when it connects."""
    env.profiles(env.sqlite_profile())
    real_open = ConnectionRegistry.open

    def flip_then_open(self: ConnectionRegistry, profile_id: str, confirmed_environment: str | None = None) -> Any:
        env.profiles(env.sqlite_profile(environment="prod"))
        return real_open(self, profile_id, confirmed_environment)

    monkeypatch.setattr(ConnectionRegistry, "open", flip_then_open)
    code, out, err = env.run("--profile", "user:shop")
    assert code == diagnose_cli.EXIT_USAGE and out == ""
    assert "Production database not confirmed" in err and "(studio.prod_confirmation_required)" in err


def test_project_profiles_need_the_trust_given_in_studio(env: Env) -> None:
    (env.project / ".tabrion" / "studio.toml").write_text(
        'version = 1\n[[profile]]\nname = "proj"\nengine = "sqlite"\npath = "data/p.db"\nenvironment = "dev"\n',
        encoding="utf-8",
    )
    data = env.project / "data"
    data.mkdir()
    (data / "p.db").write_bytes(env.db.read_bytes())
    (data / "p.db").chmod(0o600)
    code, out, err = env.run("--profile", "project:proj")
    assert code == diagnose_cli.EXIT_USAGE and out == ""
    assert "(studio.profile_not_found)" in err and "dialog D-01" in err
    project = ProjectConfig(env.project.resolve(), env.state, project_key(env.project.resolve()), env.config)
    project.trust(project.load().digest)
    code, out, err = env.run("--profile", "project:proj")
    assert code == diagnose_cli.EXIT_OK, err
    assert "STU-DX-005" in out


def test_an_ambiguous_or_unknown_name_is_a_usage_error(env: Env) -> None:
    env.profiles(env.sqlite_profile() + env.sqlite_profile("other"))
    (env.project / ".tabrion" / "studio.toml").write_text(
        'version = 1\n[[profile]]\nname = "shop"\nengine = "sqlite"\npath = "p.db"\nenvironment = "dev"\n',
        encoding="utf-8",
    )
    (env.project / "p.db").write_bytes(env.db.read_bytes())
    project = ProjectConfig(env.project.resolve(), env.state, project_key(env.project.resolve()), env.config)
    project.trust(project.load().digest)
    code, out, err = env.run("--profile", "shop")
    assert code == diagnose_cli.EXIT_USAGE and out == ""
    assert "user:shop" in err and "project:shop" in err
    code, out, err = env.run("--profile", "nope")
    assert code == diagnose_cli.EXIT_USAGE and "Profile not found" in err
    assert env.run("--profile", "other")[0] == diagnose_cli.EXIT_OK


def test_refusals_are_the_catalogs_words_never_a_value(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STUDIO_CLI_NOPE", raising=False)
    env.profiles(
        f'[[profile]]\nname = "pg"\nengine = "postgresql"\nhost = "localhost"\ndatabase = "{CANARY}"\n'
        f'user = "{CANARY}"\npassword = "env:STUDIO_CLI_NOPE"\nenvironment = "dev"\n'
        f'[[profile]]\nname = "gone"\nengine = "sqlite"\npath = "{(env.db.parent / "missing.db").as_posix()}"\n'
        'environment = "dev"\n'
    )
    code, out, err = env.run("--profile", "pg")
    assert code == diagnose_cli.EXIT_ERROR and out == ""
    assert "(studio.secret_env_missing)" in err
    code, out, err = env.run("--profile", "gone")
    assert code == diagnose_cli.EXIT_ERROR and "(studio.database_missing)" in err
    _no_secret(err, "STUDIO_CLI_NOPE")


def test_an_unreachable_server_is_the_db_problem(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_CLI_PASSWORD", CANARY)
    env.profiles(
        '[[profile]]\nname = "pg"\nengine = "postgresql"\nhost = "127.0.0.1"\nport = 1\ndatabase = "d"\n'
        'password = "env:STUDIO_CLI_PASSWORD"\nenvironment = "dev"\n'
    )
    code, out, err = env.run("--profile", "pg")
    assert code == diagnose_cli.EXIT_ERROR and out == ""
    assert "(db.connection)" in err
    _no_secret(err)


def test_a_missing_project_folder_is_a_usage_error(tmp_path: Path) -> None:
    err = io.StringIO()
    assert diagnose_cli.main([str(tmp_path / "missing"), "--profile", "x"], io.StringIO(), err) == 2
    assert "not a folder" in err.getvalue()


@pytest.mark.postgres
@postgres
def test_postgres_report_hides_the_password_host_port_user_and_database(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route's allowlist on the command line: the password (through ``env:``), host, port, user and database name
    are in no format of the report."""
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    assert url.password and url.host and url.username and url.database
    monkeypatch.setenv("STUDIO_CLI_PASSWORD", url.password)
    env.profiles(
        f'[[profile]]\nname = "pg"\nengine = "postgresql"\nhost = "{url.host}"\nport = {url.port or 5432}\n'
        f'database = "{url.database}"\nuser = "{url.username}"\npassword = "env:STUDIO_CLI_PASSWORD"\n'
        'sslmode = "prefer"\nenvironment = "dev"\nread_only_role = true\n'
    )
    for report_format in ("markdown", "json"):
        code, out, err = env.run("--profile", "user:pg", "--format", report_format)
        assert code == diagnose_cli.EXIT_OK, err
        text = out + err
        assert url.password not in text and f"{url.host}:" not in text and f"//{url.host}" not in text
        assert f":{url.port or 5432}" not in text
        for name in (url.username, url.database):
            assert f" {name} " not in text and f'"{name}"' not in text and f"`{name}`" not in text
        assert "STU-DX-004" in out or "STU-DX-010" in out or "No findings" in out


# Review of PR #85: M-1 (a safe --output), M-2 (bytes to standard output), L-1 to L-4.
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX links and modes")


def test_m1_output_is_a_new_private_file_and_an_existing_one_needs_force(env: Env, tmp_path: Path) -> None:
    env.profiles(env.sqlite_profile())
    target = tmp_path / "out" / "report.md"
    target.parent.mkdir()
    assert env.run("--profile", "shop", "--output", str(target))[0] == diagnose_cli.EXIT_OK
    assert target.read_bytes().startswith(b"# Tabrion Studio diagnosis report")
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    target.write_bytes(b"keep me")
    code, _, err = env.run("--profile", "shop", "--output", str(target))
    assert code == diagnose_cli.EXIT_USAGE and "exists; pass --force" in err
    assert target.read_bytes() == b"keep me"
    assert env.run("--profile", "shop", "--output", str(target), "--force")[0] == diagnose_cli.EXIT_OK
    assert target.read_bytes().startswith(b"# Tabrion Studio diagnosis report")
    assert sorted(path.name for path in target.parent.iterdir()) == ["report.md"]  # no temporary file left


def test_m1_output_never_replaces_the_profiles_database(env: Env) -> None:
    env.profiles(env.sqlite_profile())
    before = env.db.read_bytes()
    for target in (env.db, Path(f"{env.db}-wal"), Path(f"{env.db}-journal"), Path(f"{env.db}-shm")):
        for force in ((), ("--force",)):
            code, _, err = env.run("--profile", "shop", "--output", str(target), *force)
            assert code == diagnose_cli.EXIT_USAGE, (target, force)
            # Without --force the existing database file is refused as existing; with it, as the database.
            existing = not force and target == env.db
            assert ("exists; pass --force" if existing else "Studio never changes a database") in err, (target, err)
    assert env.db.read_bytes() == before
    assert not Path(f"{env.db}-wal").exists() and not Path(f"{env.db}-journal").exists()


@posix
def test_m1_output_refuses_links_to_any_file_and_hard_links_to_the_database(env: Env, tmp_path: Path) -> None:
    env.profiles(env.sqlite_profile())
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"other data")
    link = tmp_path / "link.md"
    link.symlink_to(victim)
    for force in ((), ("--force",)):
        code, _, err = env.run("--profile", "shop", "--output", str(link), *force)
        assert code == diagnose_cli.EXIT_USAGE and "link or not a regular file" in err
    assert victim.read_bytes() == b"other data" and link.is_symlink()
    to_db = tmp_path / "db-link.md"
    to_db.symlink_to(env.db)
    hard = tmp_path / "hard.md"
    os.link(env.db, hard)
    before = env.db.read_bytes()
    for target in (to_db, hard):
        code, _, err = env.run("--profile", "shop", "--output", str(target), "--force")
        assert code == diagnose_cli.EXIT_USAGE, target
        assert "link or not a regular file" in err or "never changes a database" in err
    assert env.db.read_bytes() == before
    folder = tmp_path / "a-folder"
    folder.mkdir()
    assert env.run("--profile", "shop", "--output", str(folder), "--force")[0] == diagnose_cli.EXIT_USAGE


def test_m1_a_missing_folder_is_an_error_without_a_traceback(env: Env, tmp_path: Path) -> None:
    env.profiles(env.sqlite_profile())
    code, _, err = env.run("--profile", "shop", "--output", str(tmp_path / "missing" / "r.md"))
    assert code == diagnose_cli.EXIT_ERROR and "cannot write" in err and "Traceback" not in err


def test_m2_standard_output_gets_the_utf8_bytes_on_an_ascii_stream(env: Env) -> None:
    env.profiles(env.sqlite_profile())
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="ascii", newline="\r\n")
    err = io.StringIO()
    args = [str(env.project), "--state-dir", str(env.state), "--config-dir", str(env.config), "--profile", "shop"]
    assert diagnose_cli.main(args, out, err) == diagnose_cli.EXIT_OK, err.getvalue()
    body = raw.getvalue()
    assert "·".encode() in body and b"\r\n" not in body  # UTF-8 and LF, as the report's bytes
    assert body.decode("utf-8").startswith("# Tabrion Studio diagnosis report")


def test_m2_a_failing_standard_output_is_a_fixed_error(env: Env) -> None:
    env.profiles(env.sqlite_profile())

    class Broken(io.BytesIO):
        def write(self, data: Any) -> int:
            raise BrokenPipeError(32, "Broken pipe")

    out = io.TextIOWrapper(Broken(), encoding="utf-8")
    err = io.StringIO()
    args = [str(env.project), "--state-dir", str(env.state), "--config-dir", str(env.config), "--profile", "shop"]
    assert diagnose_cli.main(args, out, err) == diagnose_cli.EXIT_ERROR
    assert "cannot write standard output: Broken pipe" in err.getvalue()


def test_l1_json_and_markdown_hold_no_raw_c1_or_bidi_character(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.profiles(env.sqlite_profile())
    hostile = "audit\x9b31m\u202egol\u2066x"
    monkeypatch.setattr(
        OpenConnection,
        "tabrion_findings",
        lambda self, **_: [Finding("TAB-SQ-004", Severity.WARNING, hostile, f"detail {hostile}", "remedy")],
    )
    for report_format in ("json", "markdown"):
        code, out, err = env.run("--profile", "shop", "--format", report_format)
        assert code == diagnose_cli.EXIT_OK, err
        for character in ("\x9b", "\u202e", "\u2066"):
            assert character not in out, (report_format, character)
        if report_format == "json":
            assert out.isascii()
            detail = next(item for item in json.loads(out)["diagnosis"]["findings"] if item["code"] == "TAB-SQ-004")
            assert detail["detail"] == f"detail {hostile}"  # the escapes decode to the text


def test_l2_an_id_shaped_value_matches_ids_only(env: Env) -> None:
    (env.project / ".tabrion" / "studio.toml").write_text(
        'version = 1\n[[profile]]\nname = "user:mine"\nengine = "sqlite"\npath = "p.db"\nenvironment = "dev"\n',
        encoding="utf-8",
    )
    (env.project / "p.db").write_bytes(env.db.read_bytes())
    project = ProjectConfig(env.project.resolve(), env.state, project_key(env.project.resolve()), env.config)
    project.trust(project.load().digest)
    code, out, err = env.run("--profile", "user:mine")
    assert code == diagnose_cli.EXIT_USAGE and out == "" and "(studio.profile_not_found)" in err
    assert env.run("--profile", "project:user:mine")[0] == diagnose_cli.EXIT_OK


def test_l3_the_user_files_issues_are_named(env: Env) -> None:
    env.profiles("[[profile]\nname = broken")
    code, _, err = env.run("--profile", "shop")
    assert code == diagnose_cli.EXIT_USAGE and "(studio.profile_not_found)" in err
    assert "profiles.toml: profiles.toml line" in err and "config." in err


@posix
def test_l4_a_state_directory_others_may_write_is_refused(env: Env) -> None:
    env.profiles(env.sqlite_profile())
    env.state.chmod(0o777)
    try:
        code, out, err = env.run("--profile", "shop")
    finally:
        env.state.chmod(0o700)
    assert code == diagnose_cli.EXIT_ERROR and out == "" and "is not private" in err
    assert stat.S_IMODE(env.state.stat().st_mode) == 0o700  # checked, never changed by the command


def test_m2_a_message_naming_a_profile_survives_an_ascii_error_stream(env: Env) -> None:
    env.profiles(env.sqlite_profile("prüf", environment="prod"))
    raw = io.BytesIO()
    err = io.TextIOWrapper(raw, encoding="ascii")
    args = [str(env.project), "--state-dir", str(env.state), "--config-dir", str(env.config), "--profile", "prüf"]
    assert diagnose_cli.main(args, io.StringIO(), err) == diagnose_cli.EXIT_USAGE
    err.flush()
    assert b"user:pr\\xfcf is marked as PROD" in raw.getvalue()


# Follow-up to the PR #85 review, D-1: the write stays in the folder that was opened and checked.
@posix
def test_d1_a_folder_link_swapped_after_the_check_cannot_redirect_the_write(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.profiles(env.sqlite_profile())
    assert diagnose_cli._IN_FOLDER
    real = tmp_path / "reports"
    real.mkdir()
    link = tmp_path / "folder-link"
    link.symlink_to(real)
    before = env.db.read_bytes()

    def swap(folder: int) -> None:
        link.unlink()
        link.symlink_to(env.db.parent)  # now the path names the database itself

    monkeypatch.setattr(diagnose_cli, "_folder_opened", swap)
    code, _, err = env.run("--profile", "shop", "--output", str(link / env.db.name), "--force")
    assert code == diagnose_cli.EXIT_OK, err
    assert env.db.read_bytes() == before
    assert (real / env.db.name).read_bytes().startswith(b"# Tabrion Studio diagnosis report")
    assert sorted(path.name for path in real.iterdir()) == [env.db.name]  # no temporary file left
    assert sorted(path.name for path in env.db.parent.iterdir()) == [env.db.name]


@posix
def test_d1_the_opened_folder_is_checked_again_and_a_swap_before_opening_is_refused(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.profiles(env.sqlite_profile())
    real = tmp_path / "reports"
    real.mkdir()
    link = tmp_path / "folder-link"
    link.symlink_to(real)
    before = env.db.read_bytes()
    checked = diagnose_cli._check_output

    def check_then_swap(target: Path, *, force: bool, protected: set[str]) -> None:
        checked(target, force=force, protected=protected)
        if protected:  # the check before the write: the folder link changes right after it
            link.unlink()
            link.symlink_to(env.db.parent)

    monkeypatch.setattr(diagnose_cli, "_check_output", check_then_swap)
    for name in (env.db.name, f"{env.db.name}-wal"):
        link.unlink()
        link.symlink_to(real)
        code, _, err = env.run("--profile", "shop", "--output", str(link / name), "--force")
        assert code == diagnose_cli.EXIT_USAGE and "never changes a database" in err, (name, err)
    assert env.db.read_bytes() == before
    assert sorted(path.name for path in env.db.parent.iterdir()) == [env.db.name]
    assert list(real.iterdir()) == []
