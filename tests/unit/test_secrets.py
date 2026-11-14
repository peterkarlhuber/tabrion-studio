"""STU-007: secret resolution, the ``file:`` reader (C-3 of stu-006-review.md) and the keyring without fallback."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import logging
import os
from pathlib import Path
import signal
import sys
from typing import Any
import uuid

import keyring
from keyring.backends import chainer, fail, null
import pytest

from tabrion_studio.config import secrets as studio_secrets
from tabrion_studio.config.locations import safe_realpath
from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.config.secrets import (
    KEYRING_BACKEND_ENV,
    MAX_SECRET_BYTES,
    SERVICE,
    KeyringStore,
    SecretError,
    Secrets,
    SecretTarget,
    SessionSecrets,
    backend_name,
    keyring_key,
    read_secret_file,
    select_backend,
)
from tabrion_studio.logs import forget_secrets, scrub
from tests.support.fake_keyring import FakeKeyring

POSIX = sys.platform != "win32"
posix_only = pytest.mark.skipif(not POSIX, reason="POSIX file modes, owners, FIFOs and links")
CANARY = f"tabrion-canary-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def _forget() -> Iterator[None]:
    yield
    forget_secrets()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    (folder / "secrets").mkdir(parents=True)
    return folder


def _secret(path: Path, content: str = CANARY + "\n", mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))  # binary: no newline translation on Windows
    path.chmod(mode)
    return path


def _code(call: Any) -> str:
    with pytest.raises(SecretError) as caught:
        call()
    return caught.value.code


# --- file: (C-3) ------------------------------------------------------------------------------------------------


def test_a_private_file_inside_the_project_is_read_and_registered(project: Path) -> None:
    _secret(project / "secrets" / "db")
    assert read_secret_file("secrets/db", project, [project]) == CANARY  # one trailing newline stripped
    assert CANARY not in scrub(f"value {CANARY}")  # registered for log redaction


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"a b\r\n", "a b"),
        (b"a b\n", "a b"),
        (b"x\n\n", "x\n"),
        (b"x\r\n\r\n", "x\r\n"),
        (b"x\r", "x\r"),
        (b"x", "x"),
        # I-3 of stu-007-review.md: one leading BOM (Notepad) is removed, a second one is part of the secret.
        (b"\xef\xbb\xbfpw\r\n", "pw"),
        (b"\xef\xbb\xbf\xef\xbb\xbfpw", "\ufeffpw"),
    ],
)
def test_exactly_one_trailing_line_end_is_stripped(project: Path, content: bytes, expected: str) -> None:
    """The contract: one trailing "\\r\\n" or "\\n" is removed, nothing else (the file is written in binary)."""
    path = project / "secrets" / "db"
    path.write_bytes(content)
    path.chmod(0o600)
    assert read_secret_file("secrets/db", project, [project]) == expected


def test_tilde_is_never_expanded(project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """C-3: ``file:~/x`` is ``<project>/~/x``, never the home folder."""
    home = tmp_path / "home"
    _secret(home / "x", "home-secret")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert _code(lambda: read_secret_file("~/x", project, [project])) == "studio.secret_file_unreadable"
    _secret(project / "~" / "x", "project-secret")
    assert read_secret_file("~/x", project, [project]) == "project-secret"


@pytest.mark.parametrize("text", ["../outside", "/etc/passwd", "//attacker.example/s/x", "C:x", "/net/host/x", ""])
def test_files_outside_the_roots_are_refused(project: Path, text: str) -> None:
    _secret(project.parent / "outside")
    assert _code(lambda: read_secret_file(text, project, [project])) == "studio.secret_file_outside"


def test_containment_is_checked_at_read_time(project: Path, tmp_path: Path) -> None:
    """A file listed at load time as inside, whose folder is a link out of the project by the time it is read."""
    _secret(tmp_path / "elsewhere" / "db")
    os.rmdir(project / "secrets")
    if not POSIX:
        pytest.skip("symlinks")
    (project / "secrets").symlink_to(tmp_path / "elsewhere")
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_outside"


@posix_only
def test_a_link_to_a_secret_is_refused(project: Path) -> None:
    real = _secret(project / "secrets" / "real")
    (project / "secrets" / "db").symlink_to(real)
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_unsafe"
    assert _code(lambda: read_secret_file("secrets/db", project, None)) == "studio.secret_file_unsafe"


@posix_only
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o666])
def test_a_file_readable_by_others_is_refused(project: Path, mode: int) -> None:
    _secret(project / "secrets" / "db", mode=mode)
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_not_private"


@posix_only
def test_a_read_only_file_of_the_owner_is_accepted(project: Path) -> None:
    _secret(project / "secrets" / "db", mode=0o400)
    assert read_secret_file("secrets/db", project, [project]) == CANARY


@posix_only
def test_a_hard_link_is_refused(project: Path) -> None:
    real = _secret(project / "secrets" / "real")
    os.link(real, project / "secrets" / "db")
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_unsafe"


@posix_only
def test_a_fifo_is_refused_without_blocking(project: Path) -> None:
    """I-2: without O_NONBLOCK the open would block; the alarm makes that a failure instead of a hanging run."""
    os.mkfifo(project / "secrets" / "db", 0o600)

    def blocked(signum: int, frame: object) -> None:
        raise AssertionError("opening the FIFO blocked")

    previous = signal.signal(signal.SIGALRM, blocked)
    signal.alarm(5)
    try:
        assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_unsafe"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@posix_only
@pytest.mark.parametrize("swap", ["file", "folder"])
def test_a_link_swapped_in_after_the_checks_is_not_followed(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str
) -> None:
    """I-2: the swap happens deterministically between the checks and the open; O_NOFOLLOW refuses it."""
    _secret(project / "secrets" / "db", "decoy")
    outside = tmp_path / "outside"
    _secret(outside / "db", CANARY)
    real_safe_realpath = safe_realpath

    def swapping(path: Path) -> Path | None:
        result = real_safe_realpath(path)
        if swap == "file":
            (project / "secrets" / "db").unlink()
            (project / "secrets" / "db").symlink_to(outside / "db")
        else:
            (project / "secrets" / "db").unlink()
            (project / "secrets").rmdir()
            (project / "secrets").symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(studio_secrets, "safe_realpath", swapping)
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_unsafe"


def test_env_session_and_short_values_are_redacted(tmp_path: Path) -> None:
    """I-1 and I-2: every resolved or entered value is registered, a short one too (4 characters and more)."""
    secrets, _ = _secrets({"PGPASSWORD": "env-" + CANARY})
    env = SecretTarget(SecretRef("env", "PGPASSWORD"), "user", "user:e", tmp_path, None)
    assert secrets.resolve(env) == "env-" + CANARY
    session = SecretTarget(None, "user", "user:s", tmp_path, None)
    secrets.store(session, "ses-" + CANARY, "session")
    short = SecretTarget(None, "user", "user:t", tmp_path, None)
    secrets.store(short, "Zq7#", "session")
    line = scrub(f"env-{CANARY} ses-{CANARY} pw=Zq7# x")
    assert CANARY not in line and "Zq7#" not in line
    secrets.store(SecretTarget(None, "user", "user:u", tmp_path, None), "abc", "session")
    assert scrub("abc") == "abc"  # below 4 characters nothing is registered: the log stays readable


@posix_only
def test_a_folder_is_refused(project: Path) -> None:
    (project / "secrets" / "db").mkdir()
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_unsafe"


@pytest.mark.skipif(not POSIX or os.geteuid() != 0, reason="needs root to plant a file of another user")
def test_a_file_of_another_user_is_refused(project: Path) -> None:
    path = _secret(project / "secrets" / "db")
    os.chown(path, 65534, 65534)
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_unsafe"


@pytest.mark.parametrize(
    "content",
    [b"", b"\n", b"x" * (MAX_SECRET_BYTES + 1), b"\xff\xfe", b"a\x00b"],
    ids=["empty", "nl", "big", "enc", "nul"],
)
def test_unusable_contents_are_refused(project: Path, content: bytes) -> None:
    path = project / "secrets" / "db"
    path.write_bytes(content)
    path.chmod(0o600)
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_unreadable"


def test_a_missing_file_is_refused(project: Path) -> None:
    assert _code(lambda: read_secret_file("secrets/none", project, [project])) == "studio.secret_file_unreadable"


@posix_only
def test_open_refuses_a_link_swapped_in_after_the_check(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open walks the real path with O_NOFOLLOW, so a folder replaced by a link after the check fails."""
    from tabrion_studio.config.locations import safe_realpath as original

    _secret(project / "secrets" / "db")
    _secret(tmp_path / "evil" / "db", "evil")

    def swap(path: Path) -> Path | None:
        real = original(path)
        (project / "secrets" / "db").unlink()
        os.rmdir(project / "secrets")
        (project / "secrets").symlink_to(tmp_path / "evil")
        return real

    monkeypatch.setattr("tabrion_studio.config.secrets.safe_realpath", swap)
    assert _code(lambda: read_secret_file("secrets/db", project, [project])) == "studio.secret_file_unsafe"


def test_no_home_expansion_is_ever_called(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object) -> str:
        raise AssertionError("expanduser must not be called (C-3)")

    monkeypatch.setattr(os.path, "expanduser", forbidden)
    monkeypatch.setattr(Path, "expanduser", forbidden)
    _secret(project / "~" / "db")
    assert read_secret_file("~/db", project, [project]) == CANARY


# --- keyring without plain-text fallback ------------------------------------------------------------------------


class _PlainTextKeyring(FakeKeyring):
    """Looks like ``keyrings.alt.file.PlaintextKeyring``: a backend that writes secrets to a file."""


_PlainTextKeyring.__module__ = "keyrings.alt.file"


_NOT_A_KEYRING = [fail.Keyring(), null.Keyring(), _PlainTextKeyring()]  # type: ignore[no-untyped-call]


@pytest.mark.parametrize("backend", _NOT_A_KEYRING, ids=["fail", "null", "alt"])
def test_backends_that_are_not_a_keyring_are_refused(monkeypatch: pytest.MonkeyPatch, backend: object) -> None:
    """R-S4: no keyring means no keyring, never a plain-text file."""
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    assert select_backend() is None


class _OsKeyring(FakeKeyring):
    """Looks like one of the keyring package's own OS backends."""


_OsKeyring.__module__ = "keyring.backends.SecretService"


class _VendorKeyring(FakeKeyring):
    """A backend of another package (it might write a plain file; Studio cannot tell)."""

    priority = 50


_VendorKeyring.__module__ = "vendor_keyring.backend"


def test_the_chainer_is_never_used_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    good, plain = _OsKeyring(), _PlainTextKeyring()
    plain.priority = 50  # even a higher priority does not make a file backend usable
    chain = chainer.ChainerBackend()  # type: ignore[no-untyped-call]
    monkeypatch.setattr(chainer.ChainerBackend, "backends", property(lambda _self: [plain, good]))
    monkeypatch.setattr(keyring, "get_keyring", lambda: chain)
    assert select_backend() is good


def test_a_backend_of_another_package_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Security assessment C-04: only the keyring package's OS backends count, whatever the other's priority."""
    os_keyring, vendor = _OsKeyring(), _VendorKeyring()
    chain = chainer.ChainerBackend()  # type: ignore[no-untyped-call]
    monkeypatch.setattr(chainer.ChainerBackend, "backends", property(lambda _self: [vendor, os_keyring]))
    monkeypatch.setattr(keyring, "get_keyring", lambda: chain)
    assert select_backend({}) is os_keyring
    monkeypatch.setattr(keyring, "get_keyring", lambda: vendor)
    assert select_backend({}) is None


def test_a_subclass_of_an_os_backend_in_another_package_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class Imitation(_OsKeyring):
        pass

    Imitation.__module__ = "vendor_keyring.secretservice"
    monkeypatch.setattr(keyring, "get_keyring", Imitation)
    assert select_backend({}) is None


def test_the_user_can_accept_one_named_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    vendor = _VendorKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: vendor)
    assert select_backend({KEYRING_BACKEND_ENV: "vendor_keyring.backend._VendorKeyring"}) is vendor
    assert select_backend({KEYRING_BACKEND_ENV: "vendor_keyring.backend.Other"}) is None
    assert select_backend({KEYRING_BACKEND_ENV: "vendor_keyring"}) is None  # the exact class, not a prefix
    monkeypatch.setenv(KEYRING_BACKEND_ENV, backend_name(vendor))
    assert select_backend() is vendor


@pytest.mark.parametrize("backend", _NOT_A_KEYRING, ids=["fail", "null", "alt"])
def test_a_refused_backend_stays_refused_when_named(monkeypatch: pytest.MonkeyPatch, backend: object) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    assert select_backend({KEYRING_BACKEND_ENV: backend_name(backend)}) is None


def test_the_selected_backend_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="tabrion_studio.secrets")
    store = KeyringStore(lambda: _OsKeyring())
    assert store.available
    records = [r for r in caplog.records if getattr(r, "event", None) == "studio.keyring_backend"]
    assert [r.getMessage() for r in records] == [f"keyring backend: {backend_name(_OsKeyring())}"]


def test_keyring_store(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeKeyring()
    store = KeyringStore(lambda: fake)
    assert store.available and store.get("user:p") is None
    store.set("user:p", CANARY)
    assert fake.values == {(SERVICE, "user:p"): CANARY}
    forget_secrets()
    assert store.get("user:p") == CANARY
    assert CANARY not in scrub(CANARY)  # a value read from the keyring is registered too
    store.delete("user:p")
    store.delete("user:p")  # nothing to delete: no error
    fake.fail = True
    assert _code(lambda: store.get("user:p")) == "studio.keyring_failed"
    assert _code(lambda: store.set("user:p", "x")) == "studio.keyring_failed"


def test_without_a_keyring_nothing_is_stored_anywhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative test of 09 (STU-007): no keyring, no fallback file, in any folder Studio uses."""
    monkeypatch.chdir(tmp_path)
    secrets = Secrets(KeyringStore(lambda: None))
    target = SecretTarget(SecretRef("keyring", ""), "user", "user:p", tmp_path, None)
    assert not secrets.keyring.available
    assert _code(lambda: secrets.store(target, CANARY, "keyring")) == "studio.keyring_unavailable"
    assert _code(lambda: secrets.resolve(target)) == "studio.secret_missing"
    for path in tmp_path.rglob("*"):
        assert not path.is_file() or CANARY not in path.read_text(errors="replace")


# --- resolution ---------------------------------------------------------------------------------------------------


def _secrets(environ: dict[str, str] | None = None) -> tuple[Secrets, FakeKeyring]:
    fake = FakeKeyring()
    return Secrets(KeyringStore(lambda: fake), SessionSecrets(), environ or {}), fake


def test_keyring_then_session(tmp_path: Path) -> None:
    secrets, fake = _secrets()
    target = SecretTarget(SecretRef("keyring", ""), "user", keyring_key("user", _pg("p"), ""), tmp_path, None)
    assert _code(lambda: secrets.resolve(target)) == "studio.secret_missing"
    secrets.store(target, "typed", "session")
    assert secrets.resolve(target) == "typed" and secrets.has_secret(target) is True
    secrets.store(target, CANARY, "keyring")
    assert secrets.resolve(target) == CANARY  # the keyring wins, the session copy is dropped
    assert not secrets.session.has(target.key)
    secrets.forget(target)
    assert fake.values == {} and _code(lambda: secrets.resolve(target)) == "studio.secret_missing"


def test_env_and_file_references_use_their_own_source(tmp_path: Path) -> None:
    secrets, _ = _secrets({"PGPASSWORD": CANARY, "EMPTY": ""})
    env = SecretTarget(SecretRef("env", "PGPASSWORD"), "project", "project:k:p", tmp_path, [tmp_path])
    assert secrets.resolve(env) == CANARY and secrets.has_secret(env) is True
    missing = SecretTarget(SecretRef("env", "EMPTY"), "project", "project:k:q", tmp_path, [tmp_path])
    assert _code(lambda: secrets.resolve(missing)) == "studio.secret_env_missing"
    assert secrets.has_secret(missing) is False
    for where in ("keyring", "session"):
        assert _code(lambda w=where: secrets.store(env, "x", w)) == "studio.secret_not_storable"
    _secret(tmp_path / "pw", "from-file")
    file = SecretTarget(SecretRef("file", "pw"), "project", "project:k:r", tmp_path, [tmp_path])
    assert secrets.resolve(file) == "from-file"
    assert secrets.has_secret(file) is None  # not known without reading the file


def test_listing_never_reads_the_keyring(tmp_path: Path) -> None:
    """Reading the keyring can show a system dialog (macOS): has_secret never does it."""
    secrets, fake = _secrets()
    target = SecretTarget(SecretRef("keyring", ""), "user", "user:p", tmp_path, None)
    fake.values[(SERVICE, "user:p")] = "x"
    assert secrets.has_secret(target) is None and fake.reads == 0


@pytest.mark.parametrize("value", [None, "", 5, ["x"], "x" * 1025, "a\x00b"])
def test_entered_secrets_are_validated_without_echo(tmp_path: Path, value: object) -> None:
    secrets, fake = _secrets()
    target = SecretTarget(SecretRef("keyring", ""), "user", "user:p", tmp_path, None)
    assert _code(lambda: secrets.store(target, value, "keyring")) == "studio.secret_invalid"
    assert fake.values == {}


def test_a_profile_without_a_reference_can_only_use_a_session_secret(tmp_path: Path) -> None:
    secrets, fake = _secrets()
    target = SecretTarget(None, "user", "user:p", tmp_path, None)
    assert _code(lambda: secrets.store(target, "x", "keyring")) == "studio.secret_not_storable"
    assert _code(lambda: secrets.store(target, "x", "disk")) == "studio.secret_invalid"
    secrets.store(target, "typed", "session")
    assert secrets.resolve(target) == "typed" and fake.reads == 0


def _pg(name: str, **changes: object) -> Profile:
    fields: dict[str, Any] = {"host": "db.internal", "database": "d", "user": "u", "port": None, **changes}
    return Profile(name=name, engine="postgresql", environment="prod", password=SecretRef("keyring", ""), **fields)


def test_keyring_keys_are_unique_per_project_folder() -> None:
    profile = _pg("p")
    assert keyring_key("user", profile, "k1").startswith("user:p:")
    assert keyring_key("project", profile, "k1") != keyring_key("project", profile, "k2")
    # The fingerprint has a fixed length at the end: a ":" in the name cannot make two keys equal.
    assert keyring_key("user", _pg("a:b"), "") != keyring_key("user", _pg("a"), "")


@pytest.mark.parametrize(
    "change",
    [
        {"host": "evil.example"},
        {"port": 6432},
        {"database": "other"},
        {"user": "postgres"},
        {"sslmode": "disable"},  # L-1 of stu-008-review.md: a TLS downgrade is a new target
        {"sslmode": "prefer"},
    ],
)
def test_a_stored_secret_does_not_follow_a_retargeted_profile(tmp_path: Path, change: dict[str, Any]) -> None:
    """C-1 (M-1 of stu-007-review.md): same name, another target: the stored secret is not used."""
    secrets, _ = _secrets()

    def target(profile: Profile) -> SecretTarget:
        return SecretTarget(profile.password, "project", keyring_key("project", profile, "k1"), tmp_path, [tmp_path])

    before, after = _pg("prod"), _pg("prod", **change)
    secrets.store(target(before), CANARY, "keyring")
    secrets.store(target(before), CANARY, "session")
    assert secrets.resolve(target(before)) == CANARY
    assert _code(lambda: secrets.resolve(target(after))) == "studio.secret_missing"
    assert secrets.has_secret(target(after)) is None


def test_the_default_port_is_the_same_target() -> None:
    assert keyring_key("user", _pg("p"), "") == keyring_key("user", _pg("p", port=5432), "")
    sqlite = Profile(name="p", engine="sqlite", environment="dev", path="a.db")
    assert keyring_key("user", sqlite, "") != keyring_key("user", replace(sqlite, path="b.db"), "")


@pytest.mark.parametrize("text", ["%5C%5Cattacker.example%5Cs", "\u2215\u2215attacker.example", "\uff3c\uff3cattacker"])
def test_values_are_never_url_decoded_or_normalized(project: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Extended C-3: a URL-encoded or full-width separator is a literal name inside the project, never a UNC path."""
    import unicodedata
    import urllib.parse

    def forbidden(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("file: values are never URL-decoded or normalized (C-3)")

    _secret(project / text, "literal")
    with monkeypatch.context() as patched:  # only around the read: pytest itself uses both functions
        patched.setattr(urllib.parse, "unquote", forbidden)
        patched.setattr(unicodedata, "normalize", forbidden)
        value = read_secret_file(text, project, [project])
    assert value == "literal"
