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
from typing import Any
import uuid

import pytest
import sqlalchemy

from tabrion_studio import cli, diagnose_cli
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.instance import project_key
from tabrion_studio.logs import forget_secrets
from tabrion_studio.services.connections import ConnectionRegistry

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
