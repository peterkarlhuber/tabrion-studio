"""STU-XW-1: the one source of the write-area flags (STU-ADR-0006 "Flags", TX-01, review N-1 of PR #109).

The flags come from ``write-areas.toml`` in the configuration folder the command line names, read once at start,
with the checks of Studio's own private files. Never from an environment variable (also not one that moves the
configuration folder), a ``.env`` file, a project file, the settings file, a URL, a header or anything the page
sends. Each area's state is logged at start.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import os
from pathlib import Path
import re
import sys

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
