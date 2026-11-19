"""Unit tests of the profile import's pieces (STU-XDM-5): reading a file under the folder rules
(:func:`tabrion_studio.write.folders.read_user_file`), the reference and field mapping, and the PROD target match of
:mod:`tabrion_studio.write.database_manager.profiles`. The routes: ``tests/security/test_dbm_profiles.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import sys

import pytest
from tabrion.profilefile import ConnectionProfile, SecretRef

from tabrion_studio.config.schema import Profile
from tabrion_studio.write.database_manager import profiles
from tabrion_studio.write.folders import FolderRefused, read_user_file
from tabrion_studio.write.profile_export import EXPORTED_FIELDS, exported_profile

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX folder rules (Windows is refused)")


@pytest.fixture
def folder(tmp_path: Path) -> Iterator[tuple[Path, int]]:
    path = tmp_path / "f"
    path.mkdir(mode=0o700)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield path, descriptor
    finally:
        os.close(descriptor)


def _write(path: Path, data: bytes, mode: int = 0o600) -> Path:
    path.write_bytes(data)
    path.chmod(mode)
    return path


def test_a_private_regular_file_is_read(folder: tuple[Path, int]) -> None:
    path, descriptor = folder
    _write(path / "a.toml", b"version = 1\n")
    assert read_user_file(descriptor, "a.toml", 100) == b"version = 1\n"


def test_the_limit_is_exact(folder: tuple[Path, int]) -> None:
    path, descriptor = folder
    _write(path / "a.toml", b"x" * 10)
    assert read_user_file(descriptor, "a.toml", 10) == b"x" * 10
    with pytest.raises(FolderRefused) as refused:
        read_user_file(descriptor, "a.toml", 9)
    assert refused.value.reason == "too_large"


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_a_file_others_may_write_is_refused(folder: tuple[Path, int], mode: int) -> None:
    path, descriptor = folder
    _write(path / "a.toml", b"x", mode)
    with pytest.raises(FolderRefused) as refused:
        read_user_file(descriptor, "a.toml", 100)
    assert refused.value.reason == "unsafe"


def test_links_folders_pipes_and_missing_names_are_refused(folder: tuple[Path, int]) -> None:
    path, descriptor = folder
    real = _write(path / "real.toml", b"x")
    (path / "soft.toml").symlink_to(real)
    os.link(real, path / "hard.toml")
    (path / "dir.toml").mkdir()
    os.mkfifo(path / "pipe.toml", 0o600)
    for name in ("soft.toml", "hard.toml", "real.toml", "dir.toml", "pipe.toml"):
        with pytest.raises(FolderRefused) as refused:
            read_user_file(descriptor, name, 100)
        assert refused.value.reason == "unsafe", name  # real.toml has a second name (hard.toml)
    with pytest.raises(FolderRefused) as refused:
        read_user_file(descriptor, "none.toml", 100)
    assert refused.value.reason == "missing"
    assert "toml" not in str(refused.value)  # a refusal holds no name or path


def _source(**values: object) -> ConnectionProfile:
    base: dict[str, object] = {"name": "p", "engine": "postgresql", "host": "localhost", "database": "d"}
    return ConnectionProfile(**{**base, "environment": "dev", **values})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (None, None),
        (SecretRef("env", "PGPASS_X"), "keyring:"),  # review M-1 of #155: not kept
        (SecretRef("keyring"), "keyring:"),
        (SecretRef("keyring", "svc/user"), "keyring:"),
        (SecretRef("file", "/home/u/secret"), "keyring:"),
        (SecretRef("prompt"), "keyring:"),
    ],
)
def test_no_reference_of_the_file_is_kept(ref: SecretRef | None, expected: str | None) -> None:
    assert profiles._reference(_source(password=ref)) == expected


def test_a_sqlite_profile_never_gets_a_password() -> None:
    source = ConnectionProfile(
        name="s", engine="sqlite", path="/a.db", environment="dev", password=SecretRef("env", "X")
    )
    assert profiles._reference(source) is None


def test_the_import_uses_the_exports_allowlist_only() -> None:
    source = _source(
        port=5433,
        user="u",
        password=SecretRef("env", "X"),
        statement_timeout_ms=10,
        read_only_role=True,
        application_name="app",
        description="d",
        allow=("public.t",),
        schema_fingerprint="v1:sha256:" + "0" * 64,
        mode="read_only",
    )
    raw = profiles._raw(source, "p", "/folder")
    assert set(raw) <= {*EXPORTED_FIELDS, "password"}
    assert raw["password"] == "keyring:" and raw["read_only_role"] is True


def test_a_relative_sqlite_path_is_relative_to_the_files_folder() -> None:
    source = ConnectionProfile(name="s", engine="sqlite", path="data/../x.db", environment="dev")
    assert profiles._raw(source, "s", "/home/u/exchange")["path"] == "/home/u/exchange/x.db"
    absolute = ConnectionProfile(name="s", engine="sqlite", path="/srv/x.db", environment="dev")
    assert profiles._raw(absolute, "s", "/home/u/exchange")["path"] == "/srv/x.db"


def test_an_export_reads_back_as_the_same_read_profile(tmp_path: Path) -> None:
    """The round trip: a read profile exported and imported again is the same profile (its reference aside)."""
    original = Profile(
        name="orders",
        engine="postgresql",
        environment="test",
        host="db.example.org",
        port=5433,
        database="orders",
        user="reader",
        sslmode="verify-full",
        read_only_role=True,
        statement_timeout_ms=5000,
    )
    back, refusals = profiles._studio_profile(exported_profile(original), "orders", str(tmp_path))
    assert refusals == [] and back == original


@pytest.mark.parametrize(
    ("one", "other", "same"),
    [
        (("localhost", 5432, "d"), ("127.0.0.1", None, "d"), True),
        (("/var/run/postgresql", None, "d"), ("localhost", 5432, "d"), True),
        (("localhost", 5432, "d"), ("localhost", 5433, "d"), False),
        (("localhost", 5432, "d"), ("localhost", 5432, "e"), False),
        (("192.0.2.10", 5432, "d"), ("192.0.2.11", 5432, "d"), False),
    ],
)
def test_the_prod_target_match(
    one: tuple[str, int | None, str], other: tuple[str, int | None, str], same: bool
) -> None:
    first = Profile(name="a", engine="postgresql", environment="dev", host=one[0], port=one[1], database=one[2])
    second = Profile(name="b", engine="postgresql", environment="prod", host=other[0], port=other[1], database=other[2])
    assert profiles._same_target(first, second) is same
