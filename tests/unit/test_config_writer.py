"""STU-008: Studio writes its own ``profiles.toml`` and ``settings.toml`` (strict TOML, atomic, 0600, digest-bound)."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tomllib

import pytest

from tabrion_studio.config.files import parse_toml
from tabrion_studio.config.schema import Profile, SecretRef, Settings, validate_profiles
from tabrion_studio.config.user import PROFILES_FILE, SETTINGS_FILE, UserConfig
from tabrion_studio.config.writer import (
    SaveError,
    checked_profiles,
    checked_settings,
    digest,
    dump_settings,
    toml_string,
)

POSIX = sys.platform != "win32"
posix_only = pytest.mark.skipif(not POSIX, reason="POSIX modes and links")

HOSTILE = [
    'a"b',
    "a\\b",
    'x"\nname = "injected"\n[[profile]]\nname = "y',
    "tab\there",
    "\u2028line\u2029",
    "\x7f\x85\x9f",
    "emoji \U0001f600 and é",
    "]]\n[limits]\nmax_rows = 1",
    "'''",
    '"""',
]


@pytest.mark.parametrize("text", HOSTILE)
def test_strings_round_trip_and_never_inject_keys(text: str) -> None:
    doc = tomllib.loads(f"key = {toml_string(text)}\n")
    assert doc == {"key": text}
    assert "\n" not in toml_string(text)


def _pg(name: str, **changes: object) -> Profile:
    fields: dict[str, object] = {
        "host": "db.internal",
        "database": "d",
        "user": "u",
        "password": SecretRef("keyring", ""),
        "sslmode": "verify-full",
        **changes,
    }
    return Profile(name=name, engine="postgresql", environment="prod", **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", [*HOSTILE[:2], "plain", "a.b c/d:e"])
def test_profiles_round_trip(name: str) -> None:
    profiles = [
        _pg(name, port=6432, read_only_role=True, statement_timeout_ms=15000),
        Profile(name="file", engine="sqlite", environment="dev", path="data/parts.db"),
        _pg("env", password=SecretRef("env", "PGPASSWORD")),
        _pg("secret-file", password=SecretRef("file", "secrets/db")),
        _pg("nopw", password=None),
    ]
    data = checked_profiles(profiles)
    assert data.startswith(b"# Written by Tabrion Studio.")
    parsed = validate_profiles(parse_toml(data))
    assert parsed.issues == [] and parsed.profiles == profiles


def test_a_control_character_in_a_name_is_refused_on_read_back() -> None:
    """The writer escapes it; the schema refuses it; so Studio never writes a file it would not read."""
    with pytest.raises(ValueError):
        checked_profiles([_pg("a\nb")])


def test_settings_keep_only_what_differs_from_the_defaults() -> None:
    assert dump_settings(Settings()).splitlines()[1:] == [b"version = 1"]
    settings = Settings(language="de", log_sql=True, max_rows=500)
    data = checked_settings(settings)
    assert b"[limits]" in data and b"statement_timeout_ms" not in data
    assert b"density" not in data  # compact is the default
    appearance = checked_settings(Settings(language="en", theme="dark", density="normal"))
    assert appearance.splitlines()[1:] == [b"version = 1", b'language = "en"', b'theme = "dark"', b'density = "normal"']


def _user(tmp_path: Path) -> UserConfig:
    folder = tmp_path / "config"
    return UserConfig(folder)


def test_save_creates_the_folder_and_writes_0600(tmp_path: Path) -> None:
    user = _user(tmp_path)
    new = user.save_profiles(lambda ps: [*ps, _pg("a")], None)
    file = user.config_dir / PROFILES_FILE
    assert new == digest(file.read_bytes())
    if POSIX:
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
        assert stat.S_IMODE(user.config_dir.stat().st_mode) == 0o700
    current = user.profiles()
    assert [p.name for p in current.profiles] == ["a"] and current.digest == new and current.canonical
    assert not list(user.config_dir.glob(".*.tmp"))  # the temporary file is gone


def _code(call: object) -> str:
    with pytest.raises(SaveError) as caught:
        call()  # type: ignore[operator]
    return caught.value.code


def test_a_stale_digest_is_refused(tmp_path: Path) -> None:
    user = _user(tmp_path)
    first = user.save_profiles(lambda ps: [*ps, _pg("a")], None)
    assert _code(lambda: user.save_profiles(lambda ps: ps, None)) == "studio.config_stale"
    assert _code(lambda: user.save_profiles(lambda ps: ps, "0" * 64)) == "studio.config_stale"
    user.save_profiles(lambda ps: [*ps, _pg("b")], first)


def _hand_write(user: UserConfig, name: str, text: str) -> str | None:
    user.config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    file = user.config_dir / name
    file.write_bytes(text.encode("utf-8"))
    file.chmod(0o600)
    return digest(file.read_bytes())


HAND_WRITTEN = """version = 1
# my production database
[[profile]]
name = "a"
engine = "sqlite"
path = "a.db"
environment = "dev"
"""


def test_a_hand_written_file_is_rewritten_only_when_confirmed(tmp_path: Path) -> None:
    user = _user(tmp_path)
    current = _hand_write(user, PROFILES_FILE, HAND_WRITTEN)
    assert not user.profiles().canonical
    assert _code(lambda: user.save_profiles(lambda ps: ps, current)) == "studio.config_hand_written"
    assert (user.config_dir / PROFILES_FILE).read_text(encoding="utf-8") == HAND_WRITTEN
    user.save_profiles(lambda ps: ps, current, overwrite=True)
    assert "# my production" not in (user.config_dir / PROFILES_FILE).read_text(encoding="utf-8")
    assert user.profiles().canonical


@pytest.mark.parametrize(
    "text",
    [
        HAND_WRITTEN + "colour = 1\n",  # an unknown key: a warning, but it would be dropped
        HAND_WRITTEN + "[[profile]]\nname = 'b'\n",  # an invalid profile would be dropped
        "version = 1\n[[profile\n",  # a syntax error
    ],
)
def test_a_file_with_issues_is_never_rewritten(tmp_path: Path, text: str) -> None:
    user = _user(tmp_path)
    current = _hand_write(user, PROFILES_FILE, text)
    code = _code(lambda: user.save_profiles(lambda ps: ps, current, overwrite=True))
    assert code == "studio.config_invalid"
    assert (user.config_dir / PROFILES_FILE).read_text(encoding="utf-8") == text


def test_settings_are_saved_digest_bound(tmp_path: Path) -> None:
    user = _user(tmp_path)
    first = user.save_settings(Settings(max_rows=5), None)
    assert user.settings() == (Settings(max_rows=5), [])
    assert _code(lambda: user.save_settings(Settings(), None)) == "studio.config_stale"
    _hand_write(user, SETTINGS_FILE, "version = 1\nlog_sql = 3\n")
    assert _code(lambda: user.save_settings(Settings(), first)) == "studio.config_stale"
    current = digest((user.config_dir / SETTINGS_FILE).read_bytes())
    assert _code(lambda: user.save_settings(Settings(), current, overwrite=True)) == "studio.config_invalid"


@posix_only
def test_a_link_at_the_target_is_never_written_through(tmp_path: Path) -> None:
    user = _user(tmp_path)
    user.config_dir.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    (user.config_dir / SETTINGS_FILE).symlink_to(victim)
    # The link is not a usable settings file (an issue), so saving is refused and the victim is untouched.
    _, issues, current, _ = user.settings_file()
    assert [issue.code for issue in issues] == ["config.unsafe_file"]
    assert _code(lambda: user.save_settings(Settings(), current, overwrite=True)) == "studio.config_invalid"
    assert victim.read_text(encoding="utf-8") == "untouched"


@posix_only
def test_a_temporary_file_link_is_not_followed(tmp_path: Path) -> None:
    user = _user(tmp_path)
    user.config_dir.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    (user.config_dir / f".{PROFILES_FILE}.{os.getpid()}.tmp").symlink_to(victim)
    user.save_profiles(lambda ps: [_pg("a")], None)
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert [p.name for p in user.profiles().profiles] == ["a"]


@posix_only
@pytest.mark.parametrize("problem", ["link", "shared", "file"])
def test_an_unsafe_folder_is_refused(tmp_path: Path, problem: str) -> None:
    folder = tmp_path / "config"
    if problem == "link":
        (tmp_path / "real").mkdir(mode=0o700)
        folder.symlink_to(tmp_path / "real", target_is_directory=True)
    elif problem == "shared":
        folder.mkdir()
        folder.chmod(0o777)
    else:
        folder.write_text("", encoding="utf-8")
    user = UserConfig(folder)
    assert _code(lambda: user.save_profiles(lambda ps: [_pg("a")], None)) == "studio.config_dir_unsafe"
    assert not (tmp_path / "real" / PROFILES_FILE).exists()


def test_a_file_larger_than_studio_reads_is_not_written(tmp_path: Path) -> None:
    long = "x" * 1000
    profiles = [_pg(f"p{i}", database=long, user=long) for i in range(150)]
    user = _user(tmp_path)
    assert _code(lambda: user.save_profiles(lambda ps: profiles, None)) == "studio.config_too_large"
    assert not (user.config_dir / PROFILES_FILE).exists()
