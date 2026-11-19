"""STU-XW-1: the one source of the write-area flags (STU-ADR-0006 "Flags", TX-01, review N-1 of PR #109).

The flags come from ``write-areas.toml`` in the configuration folder the command line names, read once at start,
with the checks of Studio's own private files. Never from an environment variable (also not one that moves the
configuration folder), a ``.env`` file, a project file, the settings file, a URL, a header or anything the page
sends. Each area's state is logged at start.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import (
    ALL_OFF,
    WRITE_AREAS_FILE,
    Area,
    WriteAreas,
    flag_dir,
    load_write_areas,
)
from tests.support.session import request, sign_in
from tests.support.studio import NO_KEYRING, start_studio, studio_command

pytestmark = pytest.mark.security
POSIX = sys.platform != "win32"
ALL_ON = "\n".join(
    ["version = 1", "[areas]", "data_editor = true", "schema_designer = true", "database_manager = true"]
)


def _flag_file(folder: Path, text: str = ALL_ON, mode: int = 0o600) -> Path:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    file = folder / WRITE_AREAS_FILE
    file.write_text(text + "\n", encoding="utf-8")
    file.chmod(mode)
    return file


def test_no_file_is_every_area_off(tmp_path: Path) -> None:
    assert load_write_areas(tmp_path / "missing") == ALL_OFF
    assert load_write_areas(tmp_path) == ALL_OFF


def test_the_file_turns_on_exactly_the_areas_it_names(tmp_path: Path) -> None:
    _flag_file(tmp_path / "c", "version = 1\n[areas]\ndata_editor = true\nschema_designer = false")
    assert load_write_areas(tmp_path / "c") == WriteAreas(frozenset({Area.DATA_EDITOR}))
    _flag_file(tmp_path / "all")
    assert load_write_areas(tmp_path / "all").enabled == frozenset(Area)


@pytest.mark.parametrize(
    "text",
    [
        "[areas]\ndata_editor = true",  # no version
        "version = 2\n[areas]\ndata_editor = true",
        'version = "1"\n[areas]\ndata_editor = true',
        "version = 1\n[areas]\ndata_editor = 'true'",
        "version = 1\n[areas]\ndata_editor = 1",
        "version = 1\n[areas]\ndata_editor = 'on'",
        "version = 1\nareas = true",
        "version = 1\n[areas\ndata_editor = true",  # malformed TOML
        "version = 1\ndata_editor = true",  # not in [areas]
        "version = 1\n[areas]\nDATA_EDITOR = true\nwrites_enabled = true",
        "\xff\xfe",
    ],
)
def test_a_malformed_file_or_value_is_off(tmp_path: Path, text: str) -> None:
    _flag_file(tmp_path, text)
    assert load_write_areas(tmp_path) == ALL_OFF


def test_loopback_tls_is_accept_by_default(tmp_path: Path) -> None:
    """G-S2 decision 2A: the owner accepted loopback TCP without TLS for local use; the default keeps that."""
    assert ALL_OFF.loopback_tls == "accept" and not ALL_OFF.loopback_tls_required
    _flag_file(tmp_path / "c")
    assert load_write_areas(tmp_path / "c").loopback_tls == "accept"


@pytest.mark.parametrize(("value", "expected"), [("'accept'", "accept"), ("'require'", "require")])
def test_loopback_tls_takes_its_two_values(tmp_path: Path, value: str, expected: str) -> None:
    _flag_file(tmp_path, f"version = 1\nloopback_tls = {value}\n[areas]\ndata_editor = true")
    areas = load_write_areas(tmp_path)
    assert areas.loopback_tls == expected and areas.enabled == frozenset({Area.DATA_EDITOR})


@pytest.mark.parametrize("value", ["'Require'", "'off'", "''", "true", "false", "1", "['require']"])
def test_an_unknown_loopback_tls_is_require(tmp_path: Path, value: str, caplog: pytest.LogCaptureFixture) -> None:
    """Fail closed: a value Studio does not know is the stricter rule, and the value is never logged."""
    _flag_file(tmp_path, f"version = 1\nloopback_tls = {value}\n[areas]\ndata_editor = true")
    with caplog.at_level(logging.WARNING, logger="tabrion_studio.write_areas"):
        areas = load_write_areas(tmp_path)
    assert areas.loopback_tls == "require" and areas.loopback_tls_required
    assert areas.enabled == frozenset({Area.DATA_EDITOR})
    assert [(getattr(r, "event", None), r.getMessage()) for r in caplog.records] == [
        ("studio.write_areas_invalid", "write areas file has an unknown loopback_tls; require is used")
    ]


def test_loopback_tls_turns_no_area_on(tmp_path: Path) -> None:
    _flag_file(tmp_path, "version = 1\nloopback_tls = 'require'")
    areas = load_write_areas(tmp_path)
    assert areas.enabled == frozenset() and areas.loopback_tls == "require"


@pytest.mark.skipif(not POSIX, reason="POSIX file modes")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o606])
def test_a_file_others_can_read_or_write_is_off(tmp_path: Path, mode: int) -> None:
    _flag_file(tmp_path, mode=mode)
    assert load_write_areas(tmp_path) == ALL_OFF


@pytest.mark.skipif(not POSIX, reason="POSIX file modes")
def test_a_folder_others_may_write_is_off(tmp_path: Path) -> None:
    folder = tmp_path / "c"
    _flag_file(folder)
    folder.chmod(0o777)
    try:
        assert load_write_areas(folder) == ALL_OFF
    finally:
        folder.chmod(0o700)


def test_a_link_is_off(tmp_path: Path) -> None:
    real = _flag_file(tmp_path / "real")
    folder = tmp_path / "c"
    folder.mkdir(mode=0o700)
    try:
        (folder / WRITE_AREAS_FILE).symlink_to(real)
    except OSError:
        pytest.skip("no symlinks here")
    assert load_write_areas(folder) == ALL_OFF
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path / "real", target_is_directory=True)
    assert load_write_areas(linked) == ALL_OFF


def test_the_folder_comes_from_the_command_line_never_from_an_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-1: ``TABRION_STUDIO_CONFIG_DIR`` and ``TABRION_STUDIO_STATE_DIR`` move Studio's other folders, never this
    file; with neither option the platform folder is used."""
    monkeypatch.setenv("TABRION_STUDIO_CONFIG_DIR", str(tmp_path / "env-config"))
    monkeypatch.setenv("TABRION_STUDIO_STATE_DIR", str(tmp_path / "env-state"))
    default = tmp_path / "platform"
    assert flag_dir(None, None, default) == default
    assert flag_dir(tmp_path / "cli", None, default) == tmp_path / "cli"
    assert flag_dir(None, tmp_path / "state", default) == tmp_path / "state" / "config"


@pytest.mark.skipif(not POSIX, reason="POSIX folders; Windows uses the LocalAppData known folder")
def test_the_platform_folder_follows_no_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G2-04 of the G-S2 gate review: XDG_CONFIG_HOME (which platformdirs follows) and HOME can be set by a
    per-folder tool when the user enters a project; neither moves the folder of the flag file."""
    import pwd

    from tabrion_studio.paths import APP_NAME, platform_config_dir

    before = platform_config_dir()
    assert before is not None
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert before.parent.parent == home or before.parent.parent.parent == home  # ~/.config or ~/Library/...
    assert before.name == APP_NAME
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "project" / ".cfg"))
    monkeypatch.setenv("HOME", str(tmp_path / "project"))
    assert platform_config_dir() == before
    assert str(tmp_path) not in str(platform_config_dir())


@pytest.mark.skipif(not POSIX, reason="the POSIX account database")
def test_a_user_without_an_account_entry_has_no_platform_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-1 of the PR #125 review: a container user without a passwd entry (``--user 1234``) gets no flag folder
    instead of an error: every area off."""
    import pwd

    from tabrion_studio.paths import platform_config_dir

    def no_entry(uid: int) -> Any:
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    monkeypatch.setattr(pwd, "getpwuid", no_entry)
    assert platform_config_dir() is None
    assert flag_dir(None, None, None) is None
    assert flag_dir(Path("cli"), None, None) == Path("cli")


#: Runs Studio's command with ``pwd.getpwuid`` failing as it does for a user without an account entry.
_NO_ACCOUNT_ENTRY = (
    "import pwd, runpy, sys\n"
    "def no_entry(uid):\n"
    "    raise KeyError('getpwuid(): uid not found')\n"
    "pwd.getpwuid = no_entry\n"
    "sys.argv = ['tabrion-studio', *sys.argv[1:]]\n"
    "runpy.run_module('tabrion_studio', run_name='__main__', alter_sys=True)\n"
)


@pytest.mark.process
@pytest.mark.skipif(not POSIX, reason="the POSIX account database")
def test_studio_starts_read_only_for_a_user_without_an_account_entry(
    project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """L-1 of the PR #125 review with a real process: Studio starts (it did not), every area is off and the start
    log says why."""
    from tests.support import studio as studio_support

    monkeypatch.setattr(
        studio_support, "studio_command", lambda *args: [sys.executable, "-c", _NO_ACCOUNT_ENTRY, *args]
    )
    monkeypatch.setenv("TABRION_STUDIO_STATE_DIR", str(state_dir))  # no --state-dir: the platform folder decides
    with studio_support.start_studio(project, state_dir, state_option=False) as studio:
        info, _ = studio.wait_ready()
        assert _areas(info) == ALL_OFF.to_dict()
        lines = _logged_states(state_dir)
        notices = [
            line
            for log in (state_dir / "logs").glob("*.log*")
            for line in log.read_text(encoding="utf-8").splitlines()
            if "studio.write_areas_no_folder" in line
        ]
    assert len(lines) == 3 and all("is off" in line for line in lines), lines
    assert len(notices) == 1


def test_no_environment_variable_sets_a_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TABRION_STUDIO_WRITE_AREAS",
        "TABRION_STUDIO_DATA_EDITOR",
        "TABRION_STUDIO_SCHEMA_DESIGNER",
        "TABRION_STUDIO_DATABASE_MANAGER",
        "TABRION_STUDIO_WRITES_ENABLED",
        "DATA_EDITOR",
    ):
        monkeypatch.setenv(name, "true")
    assert load_write_areas(tmp_path) == ALL_OFF
    source = (Path(__file__).resolve().parents[2] / "src" / "tabrion_studio" / "config" / "write_areas.py").read_text(
        encoding="utf-8"
    )
    assert re.search(r"os\.environ|getenv|dotenv", source) is None
    # Studio never reads .env files (N-1): no dotenv loader anywhere in the package.
    package = Path(__file__).resolve().parents[2] / "src" / "tabrion_studio"
    for path in package.rglob("*.py"):
        assert re.search(r"\bdotenv\b|['\"]\.env['\"]", path.read_text(encoding="utf-8")) is None, path


# --- a real tabrion-studio process ----------------------------------------------------------------------------------


def _areas(info: object) -> dict[str, bool]:
    session = sign_in(info)  # type: ignore[arg-type]
    status, _, body = request(session.port, "GET", "/api/write/areas", session.headers(), host=session.host)
    assert status == 200
    areas: dict[str, bool] = json.loads(body)["areas"]
    origin = f"http://{session.host}:{session.port}"
    refused = request(
        session.port,
        "POST",
        "/api/write/unlocks",
        {**session.headers(), "Origin": origin},
        {"connection_id": "c_x", "write_profile_id": "write:w", "typed_name": "w"},
        host=session.host,
    )
    if not any(areas.values()):
        assert refused[0] == 405 and json.loads(refused[2])["code"] == "studio.method_not_allowed"
    return areas


def _logged_states(state_dir: Path) -> list[str]:
    lines: list[str] = []
    for log in (state_dir / "logs").glob("*.log*"):
        lines += [line for line in log.read_text(encoding="utf-8").splitlines() if "studio.write_area_state" in line]
    return lines


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("TABRION_STUDIO_CONFIG_DIR", "TABRION_STUDIO_STATE_DIR", "TABRION_STUDIO_LOG_DIR"):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.mark.process
def test_a_server_started_without_a_flag_source_has_every_area_off(
    project: Path, state_dir: Path, clean_env: None
) -> None:
    """Proof item 1 with a real process; the start log names each area as off."""
    with start_studio(project, state_dir) as studio:
        info, _ = studio.wait_ready()
        assert _areas(info) == ALL_OFF.to_dict()
        lines = _logged_states(state_dir)
    assert len(lines) == 3 and all("is off" in line for line in lines), lines


@pytest.mark.process
def test_no_outside_source_turns_a_flag_on(
    tmp_path: Path, project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """Proof item 3 with a real process: variables, a ``.env`` file, a project file, a flag file in the project and
    a configuration folder named by a variable never turn an area on."""
    elsewhere = tmp_path / "env-config"
    _flag_file(elsewhere)  # a private, valid file: used only if a variable could move the flag source
    _flag_file(project)  # write-areas.toml in the project folder
    _flag_file(project / ".tabrion")
    (project / ".env").write_text(
        "TABRION_STUDIO_WRITE_AREAS=data_editor,schema_designer,database_manager\nTABRION_STUDIO_DATA_EDITOR=1\n",
        encoding="utf-8",
    )
    (project / ".tabrion" / "studio.toml").write_text(
        'version = 1\n[areas]\ndata_editor = true\n[[write_profile]]\nname = "w"\nkind = "write"\nread_profile = "p"\n',
        encoding="utf-8",
    )
    env = {
        **os.environ,
        **NO_KEYRING,
        "TABRION_STUDIO_CONFIG_DIR": str(elsewhere),
        "TABRION_STUDIO_WRITE_AREAS": "data_editor,schema_designer,database_manager",
        "TABRION_STUDIO_DATA_EDITOR": "1",
        "TABRION_STUDIO_SCHEMA_DESIGNER": "true",
        "TABRION_STUDIO_DATABASE_MANAGER": "on",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with start_studio(project, state_dir) as studio:
        info, _ = studio.wait_ready()
        assert _areas(info) == ALL_OFF.to_dict()
        lines = _logged_states(state_dir)
    assert len(lines) == 3 and all("is off" in line for line in lines), lines


@pytest.mark.process
def test_the_owners_file_in_the_command_line_folder_turns_on_only_its_area(
    tmp_path: Path, project: Path, state_dir: Path, clean_env: None
) -> None:
    """The source works (so the tests above are not vacuous) and an area's flag turns on only that area."""
    config = tmp_path / "config"
    _flag_file(config, "version = 1\n[areas]\ndata_editor = true")
    with start_studio(project, state_dir, "--config-dir", str(config)) as studio:
        info, _ = studio.wait_ready()
        assert _areas(info) == {"data_editor": True, "schema_designer": False, "database_manager": False}
        lines = _logged_states(state_dir)
    assert sum("is on" in line for line in lines) == 1 and any("data_editor (G-S2) is on" in line for line in lines)


def test_the_command_has_no_flag_option() -> None:
    """A flag is set only in the owner's file; the command line names its folder, it takes no flag."""
    from tabrion_studio.cli import build_parser

    options = {option for action in build_parser()._actions for option in action.option_strings}
    assert not {option for option in options if "write" in option or "area" in option or "editor" in option}
    assert studio_command("--help")


# --- L-1 of PR #111: write and admin profiles follow the rule of the flag file ------------------------------------


def _profiles_file(folder: Path, database: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    file = folder / "profiles.toml"
    file.write_text(
        "version = 1\n\n"
        f'[[profile]]\nname = "parts"\nengine = "sqlite"\npath = {json.dumps(str(database))}\nenvironment = "dev"\n\n'
        '[[write_profile]]\nname = "parts-write"\nkind = "write"\nread_profile = "parts"\n',
        encoding="utf-8",
    )
    file.chmod(0o600)


def _write_profiles(info: object) -> tuple[dict[str, object], list[str], int, str]:
    """The write profile listing, the read profile ids, and the answer to creating a write profile."""
    session = sign_in(info)  # type: ignore[arg-type]
    headers = session.headers()
    status, _, body = request(session.port, "GET", "/api/write/profiles", headers, host=session.host)
    assert status == 200, body
    listing: dict[str, object] = json.loads(body)
    status, _, body = request(session.port, "GET", "/api/profiles", headers, host=session.host)
    reads = [item["id"] for item in json.loads(body)["profiles"]]
    created = request(
        session.port,
        "POST",
        "/api/write/profiles",
        {**headers, "Origin": f"http://{session.host}:{session.port}"},
        {
            "profile": {"name": "second-write", "kind": "write", "read_profile": "parts"},
            "digest": listing["user_file"]["digest"],  # type: ignore[index]
            "confirm_no_prod_credentials": True,
            "overwrite": True,  # the file was written by hand
        },
        host=session.host,
    )
    code = json.loads(created[2]).get("code", "") if created[2] else ""
    return listing, reads, created[0], code


def _logged(state_dir: Path, event: str) -> bool:
    return any(event in log.read_text(encoding="utf-8") for log in (state_dir / "logs").glob("*.log*"))


@pytest.mark.process
def test_write_profiles_from_a_folder_a_variable_chose_are_not_used(
    tmp_path: Path, project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """L-1: the flag is on (the command line's folder), but ``TABRION_STUDIO_CONFIG_DIR`` points the profiles at a
    folder in the project, as direnv could: its read profiles are used, its write profiles are not, none can be
    created there, and the start logs ``studio.write_profiles_ignored``."""
    database = tmp_path / "parts.db"
    database.write_bytes(b"")
    _flag_file(state_dir / "config", "version = 1\n[areas]\ndata_editor = true")
    moved = project / ".direnv-config"
    _profiles_file(moved, database)
    monkeypatch.setenv("TABRION_STUDIO_CONFIG_DIR", str(moved))
    with start_studio(project, state_dir) as studio:
        info, _ = studio.wait_ready()
        assert _areas(info)["data_editor"] is True
        listing, reads, status, code = _write_profiles(info)
    assert listing["write_profiles"] == []
    assert listing["write_profiles_allowed"] is False and listing["write_profiles_ignored"] is True
    assert "user:parts" in reads
    assert (status, code) == (409, "studio.write_profiles_ignored")
    assert "second-write" not in (moved / "profiles.toml").read_text(encoding="utf-8")
    assert _logged(state_dir, "studio.write_profiles_ignored")


@pytest.mark.process
def test_write_profiles_from_the_command_lines_folder_are_used(
    tmp_path: Path, project: Path, state_dir: Path, clean_env: None
) -> None:
    """The control: the same file in the folder the command line names (here ``<--state-dir>/config``) is used."""
    database = tmp_path / "parts.db"
    database.write_bytes(b"")
    _flag_file(state_dir / "config", "version = 1\n[areas]\ndata_editor = true")
    _profiles_file(state_dir / "config", database)
    with start_studio(project, state_dir) as studio:
        info, _ = studio.wait_ready()
        listing, _, status, _ = _write_profiles(info)
    assert [item["name"] for item in listing["write_profiles"]] == ["parts-write"]  # type: ignore[attr-defined]
    assert listing["write_profiles_allowed"] is True and listing["write_profiles_ignored"] is False
    assert status == 201
    assert not _logged(state_dir, "studio.write_profiles_ignored")
    # N-1 of PR #116: the audit log is in the flag file's folder (here <--state-dir>/config), not the state folder.
    assert b"profile.create" in (state_dir / "config" / "audit" / "audit.jsonl").read_bytes()
    assert not (state_dir / "audit").exists()
    assert not _logged(state_dir, "studio.write_state_ignored")


def test_write_profiles_allowed_follows_the_flag_files_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule the server applies, as the command line computes both folders (``cli.py``): only the flag file's
    folder supplies write profiles while an area is on; a folder that ``TABRION_STUDIO_CONFIG_DIR`` or
    ``TABRION_STUDIO_STATE_DIR`` chose does not. With every area off nothing uses them."""
    from tabrion_studio.config.write_areas import write_profiles_allowed
    from tabrion_studio.paths import default_config_dir

    on = WriteAreas(frozenset({Area.DATA_EDITOR}))
    platform = tmp_path / "platform"
    for name in ("TABRION_STUDIO_CONFIG_DIR", "TABRION_STUDIO_STATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    cli = tmp_path / "cli"
    assert write_profiles_allowed(on, cli, flag_dir(cli, None, platform))
    state = tmp_path / "state"
    assert write_profiles_allowed(on, state / "config", flag_dir(None, state, platform))
    monkeypatch.setenv("TABRION_STUDIO_CONFIG_DIR", str(tmp_path / "env-config"))
    assert not write_profiles_allowed(on, default_config_dir(), flag_dir(None, None, platform))
    assert not write_profiles_allowed(on, default_config_dir(state), flag_dir(None, state, platform))
    monkeypatch.delenv("TABRION_STUDIO_CONFIG_DIR")
    env_state = tmp_path / "env-state"
    monkeypatch.setenv("TABRION_STUDIO_STATE_DIR", str(env_state))
    # cli.py: a state folder from the variable gives <state>/config; the flag file stays in the platform folder.
    assert not write_profiles_allowed(on, default_config_dir(env_state), flag_dir(None, None, platform))
    assert not write_profiles_allowed(on, tmp_path / "anything", None)
    assert write_profiles_allowed(ALL_OFF, tmp_path / "anything", None)
    # The same folder through a link is the same folder.
    platform.mkdir()
    link = tmp_path / "link"
    link.symlink_to(platform, target_is_directory=True)
    assert write_profiles_allowed(on, link, platform)


# --- N-1 of PR #116: the audit log and the secret confirmations follow the rule of the flag file -------------------


def test_the_audit_log_and_confirmations_live_in_the_flag_files_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule the server applies: always the flag file's folder (the command line's or the platform's), never the
    state folder; a state folder that ``TABRION_STUDIO_STATE_DIR`` chose is logged while an area is on."""
    from tabrion_studio.config.write_areas import write_state_folder

    on = WriteAreas(frozenset({Area.DATA_EDITOR}))
    flags = tmp_path / "flags"
    moved = tmp_path / "project" / ".direnv-state"
    records: list[str] = []

    class Spy(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(str(getattr(record, "event", "")))

    spy = Spy()
    logging.getLogger("tabrion_studio.write_areas").addHandler(spy)
    try:
        assert write_state_folder(on, flags, moved, state_dir_from_env=True) == flags
        assert records == ["studio.write_state_ignored"]
        assert write_state_folder(on, flags, tmp_path / "cli-state", state_dir_from_env=False) == flags
        assert write_state_folder(ALL_OFF, flags, moved, state_dir_from_env=True) == flags
        assert write_state_folder(on, None, moved, state_dir_from_env=True) is None
        assert records == ["studio.write_state_ignored"]  # only the first call: an area on and a moved folder
    finally:
        logging.getLogger("tabrion_studio.write_areas").removeHandler(spy)


def _pg_profiles_file(folder: Path) -> None:
    """A PostgreSQL read profile (localhost) and its write profile with an ``env:`` secret reference."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    file = folder / "profiles.toml"
    file.write_text(
        "version = 1\n\n"
        '[[profile]]\nname = "pg"\nengine = "postgresql"\nhost = "localhost"\nport = 5432\ndatabase = "parts"\n'
        'user = "app_reader"\nsslmode = "prefer"\nenvironment = "dev"\n\n'
        '[[write_profile]]\nname = "pg-write"\nkind = "write"\nread_profile = "pg"\nuser = "app_writer"\n'
        'password = "env:STUDIO_N1_SECRET"\n',
        encoding="utf-8",
    )
    file.chmod(0o600)


def _every_file(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.mark.process
def test_a_state_folder_a_variable_chose_keeps_no_audit_log_or_confirmation(
    tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """N-1 of PR #116: the flag is on (``--config-dir``), and ``TABRION_STUDIO_STATE_DIR`` points the state folder at
    a folder in the project, as direnv could. A confirmation planted there for the exact pair is not read, and the
    confirmation and the audit records Studio writes go to the flag file's folder; nothing in the moved folder's
    ``audit/`` or ``write-secret-confirmations.json`` is read or changed. The start logs ``studio.write_state_ignored``.
    """
    from tabrion_studio.config.files import parse_toml
    from tabrion_studio.config.schema import validate_profiles
    from tabrion_studio.write.secret_confirm import CONFIRMATION_FILE, secret_pair

    config = tmp_path / "config"
    _flag_file(config, "version = 1\n[areas]\ndata_editor = true")
    _pg_profiles_file(config)
    parsed = validate_profiles(parse_toml((config / "profiles.toml").read_bytes()), owner="user")
    pair = secret_pair(parsed.write_profiles[0], parsed.profiles[0])
    assert pair is not None
    moved = project / ".direnv-state"
    moved.mkdir(mode=0o700)
    planted = moved / CONFIRMATION_FILE
    planted.write_text(json.dumps({"version": 1, "confirmed": {"pg-write": pair.digest}}), encoding="utf-8")
    planted.chmod(0o600)
    (moved / "audit").mkdir(mode=0o700)
    (moved / "audit" / "audit.jsonl").write_bytes(b"planted, not a record\n")
    (moved / "audit" / "audit.jsonl").chmod(0o600)
    before = _every_file(moved)
    monkeypatch.setenv("TABRION_STUDIO_STATE_DIR", str(moved))
    with start_studio(project, moved, "--config-dir", str(config), state_option=False) as studio:
        info, _ = studio.wait_ready()
        session = sign_in(info)
        headers = {**session.headers(), "Origin": f"http://{session.host}:{session.port}"}

        def call(method: str, path: str, body: object = None) -> tuple[int, dict[str, Any]]:
            status, _, raw = request(session.port, method, path, headers, body, host=session.host)
            doc: dict[str, Any] = json.loads(raw) if raw else {}
            return status, doc

        status, listing = call("GET", "/api/write/profiles")
        assert status == 200
        (item,) = listing["write_profiles"]
        assert item["secret"]["confirmed"] is False  # the planted confirmation is not read
        status, audit = call("GET", "/api/write/audit")
        assert status == 200 and audit["verification"]["ok"] is True, audit
        status, preview = call("POST", "/api/write/secret-confirmations", {"write_profile_id": "write:pg-write"})
        assert status == 200, preview
        status, confirmed = call(
            "POST",
            "/api/write/secret-confirmations/confirm",
            {"confirmation_hash": preview["confirmation_hash"], "typed": "localhost"},
        )
        assert status == 200 and confirmed["confirmed"] is True, confirmed
        status, listing = call("GET", "/api/write/profiles")
        assert listing["write_profiles"][0]["secret"]["confirmed"] is True
    stored = json.loads((config / CONFIRMATION_FILE).read_bytes())
    assert stored["confirmed"] == {"pg-write": pair.digest}
    assert b"secret.confirm" in (config / "audit" / "audit.jsonl").read_bytes()
    after = _every_file(moved)
    for name in (CONFIRMATION_FILE, "audit/audit.jsonl"):
        assert after[name] == before[name], name
    assert not [name for name in after if name.startswith("audit/") and name != "audit/audit.jsonl"]
    assert _logged(moved, "studio.write_state_ignored")
