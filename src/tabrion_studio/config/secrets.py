"""Secret management: keyring, ``env:``, ``file:`` and session secrets (03 section 4.4, STU-007).

A profile names its password by reference only (``config.schema.SecretRef``); the value is resolved in the backend
when a connection is made (STU-009) and never reaches the frontend, a log or an error:

* ``keyring:``: the OS keyring (macOS Keychain, Windows Credential Manager, Secret Service or KWallet), service
  :data:`SERVICE`, one entry per profile and connection target (:func:`keyring_key`). **There is no plain-text
  fallback:** only the ``keyring`` package's own OS backends count as a keyring; a backend that stores secrets in a
  file (the ``keyrings.alt`` package) or that does nothing (``fail``, ``null``) counts as "no keyring" (R-S4), and
  a backend of any other package only when the user names it in ``TABRION_STUDIO_KEYRING_BACKEND`` (C-04). Without
  a keyring, a ``keyring:`` profile can only use a secret entered for this session.
* ``env:NAME``: the variable of the Studio process.
* ``file:PATH``: read by :func:`read_secret_file`, which enforces the binding requirement C-3 of
  ``stu-006-review.md`` at read time: no ``expanduser``, no URL decoding and no Unicode normalization (the value is
  the path as written), containment re-checked with
  :func:`tabrion_studio.config.locations.contained` (project profiles: inside the project or the configuration
  folder), no link anywhere on the path (``O_NOFOLLOW``, component by component), a regular file with one link, owned
  by the user and private (no group or other bit; POSIX), at most :data:`MAX_SECRET_BYTES`.
* **session**: a secret entered in the page for this process only (:class:`SessionSecrets`), kept in memory.

Every resolved or entered value is registered with :func:`tabrion_studio.logs.register_secret` as a lasting value
(never evicted, C-01), so it is redacted from every log record. Every refusal is a :class:`SecretError` with a
problem code and nothing else: no path, no variable value, no keyring message.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import errno
import logging
import os
from pathlib import Path, PurePath
import stat
import sys
import threading
from typing import Any, Literal

from tabrion_studio.config.locations import (
    contained,
    lexical_target,
    lexically_within,
    network_or_device,
    safe_realpath,
)
from tabrion_studio.config.schema import Profile, SecretRef, target_fingerprint
from tabrion_studio.logs import register_secret

SERVICE = "tabrion-studio"
MAX_SECRET_BYTES = 4096
#: The longest secret the page may enter (characters).
MAX_SECRET_LENGTH = 1024

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_REPARSE = 0x400
_POSIX = sys.platform != "win32"
#: Keyring backends that are not a keyring: they fail, do nothing, or keep secrets in a file (no plain-text fallback).
#: Refused always, also when named in :data:`KEYRING_BACKEND_ENV`.
_REFUSED_MODULES = ("keyring.backends.fail", "keyring.backends.null", "keyrings.alt")
#: The OS keyrings of the ``keyring`` package itself (security assessment C-04): macOS Keychain, Windows Credential
#: Manager, Secret Service (also through libsecret) and KWallet. A backend of any other module is used only when the
#: user names its class in :data:`KEYRING_BACKEND_ENV`: a package or a configuration that brings its own backend
#: cannot make Studio store passwords somewhere that is not an OS keyring without the user's say.
_OS_KEYRING_MODULES = frozenset(
    {
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
        "keyring.backends.libsecret",
        "keyring.backends.kwallet",
    }
)
#: ``module.Class`` of one more keyring backend the user accepts (for example a password manager's own backend).
KEYRING_BACKEND_ENV = "TABRION_STUDIO_KEYRING_BACKEND"

logger = logging.getLogger("tabrion_studio.secrets")

Owner = Literal["user", "project"]


class SecretError(Exception):
    """A secret that cannot be resolved or stored; ``code`` is a problem code (never a value or a path)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- file: ------------------------------------------------------------------------------------------------------


def _plain(info: os.stat_result) -> bool:
    return not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & _REPARSE)


def _open_posix(real: Path) -> int:
    """Open ``real`` one component at a time with ``O_NOFOLLOW``: a link anywhere (also one swapped in) fails."""
    parts = real.parts
    fd = os.open(parts[0], os.O_RDONLY | _DIRECTORY | _CLOEXEC)
    try:
        for name in parts[1:-1]:
            next_fd = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        # O_NONBLOCK: a FIFO cannot block the server; the flag does not matter for a regular file.
        return os.open(parts[-1], os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC, dir_fd=fd)
    finally:
        os.close(fd)


def _open_windows(real: Path) -> int:
    """Windows has no ``O_NOFOLLOW``: every component must be plain (no link, junction or reparse point)."""
    current = Path(real.anchor)
    for name in real.parts[1:]:
        current = current / name
        if not _plain(os.lstat(current)):
            raise SecretError("studio.secret_file_unsafe")
    return os.open(real, os.O_RDONLY | _BINARY)


def _check_opened(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SecretError("studio.secret_file_unsafe")  # a FIFO, a device, a folder or a hard link
    if _POSIX:
        if info.st_uid != os.getuid():
            raise SecretError("studio.secret_file_unsafe")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise SecretError("studio.secret_file_not_private")
    if info.st_size > MAX_SECRET_BYTES:
        raise SecretError("studio.secret_file_unreadable")


def read_secret_file(text: str, base: Path, roots: list[Path] | None) -> str:
    """The secret in the file that ``file:<text>`` names, relative to the folder ``base`` (C-3, at read time).

    ``roots`` are the folders the file must lie in (for a project profile: the project and the configuration folder);
    None means no containment rule (the user's own ``profiles.toml``). ``~`` is never expanded: ``file:~/x`` is the
    file ``<base>/~/x``.
    """
    if not text or network_or_device(text):
        raise SecretError("studio.secret_file_outside")
    target = lexical_target(text, PurePath(os.path.abspath(base)))
    if target is None:
        raise SecretError("studio.secret_file_outside")
    if roots is not None:
        lexical = [Path(os.path.abspath(root)) for root in roots]
        real_roots = [Path(os.path.realpath(root)) for root in roots]  # the user's own folders, never the file's
        if not contained(text, base, lexical, real_roots):
            raise SecretError("studio.secret_file_outside")
    try:
        if not _plain(os.lstat(target)):
            raise SecretError("studio.secret_file_unsafe")  # the file itself is a link (03 section 4.4: no symlinks)
    except FileNotFoundError:
        raise SecretError("studio.secret_file_unreadable") from None
    except OSError:
        raise SecretError("studio.secret_file_unreadable") from None
    # The real path has no link left (links in the folders above were checked to stay inside the roots); opening it
    # component by component with O_NOFOLLOW refuses a link swapped in after the check.
    real = safe_realpath(Path(target))
    if real is None:
        raise SecretError("studio.secret_file_outside")
    if roots is not None and not lexically_within(PurePath(real), [PurePath(root) for root in lexical + real_roots]):
        raise SecretError("studio.secret_file_outside")  # contained() again, on the exact path that is opened
    try:
        fd = _open_posix(real) if _POSIX and os.open in os.supports_dir_fd else _open_windows(real)
    except FileNotFoundError:
        raise SecretError("studio.secret_file_unreadable") from None
    except OSError as exc:
        unsafe = exc.errno in (errno.ELOOP, errno.ENOTDIR)
        raise SecretError("studio.secret_file_unsafe" if unsafe else "studio.secret_file_unreadable") from None
    try:
        _check_opened(fd)
        if _POSIX:
            os.set_blocking(fd, True)
        chunks = []
        size = 0
        while size <= MAX_SECRET_BYTES:
            chunk = os.read(fd, MAX_SECRET_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        raise SecretError("studio.secret_file_unreadable") from None
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > MAX_SECRET_BYTES:
        raise SecretError("studio.secret_file_unreadable")
    try:
        value = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretError("studio.secret_file_unreadable") from None
    # One leading byte order mark (Notepad writes one; I-3 of stu-007-review.md) and exactly one trailing line end,
    # "\r\n" (a file written on Windows) or "\n", are removed; nothing else.
    value = value.removeprefix("\ufeff")
    value = value[:-2] if value.endswith("\r\n") else value.removesuffix("\n")
    if not value or "\x00" in value:
        raise SecretError("studio.secret_file_unreadable")
    register_secret(value, lasting=True)
    return value


# --- keyring: ---------------------------------------------------------------------------------------------------


def keyring_key(owner: Owner, profile: Profile, project_key: str) -> str:
    """The keyring and session key of a profile: ``user:<name>:<target>`` or ``project:<project key>:<name>:<target>``.

    ``<target>`` is :func:`~tabrion_studio.config.schema.target_fingerprint` (16 hex digits), so a stored secret is
    used only for the engine, host, port, database, user and path it was entered for (C-1 of stu-007-review.md): a
    profile that keeps its name but points elsewhere has no stored secret and the page asks again. The project key
    and the fingerprint have a fixed length, so a name that contains ``:`` cannot make two keys equal.
    """
    target = target_fingerprint(profile.to_dict())
    if owner == "user":
        return f"user:{profile.name}:{target}"
    return f"project:{project_key}:{profile.name}:{target}"


def backend_name(backend: Any) -> str:
    """``module.Class`` of a keyring backend (no secret: what the log and the opt-in name)."""
    cls = type(backend)
    return f"{cls.__module__}.{cls.__qualname__}"


def _allowed(backend: Any, accepted: str | None = None) -> bool:
    module = type(backend).__module__
    if any(module == refused or module.startswith(refused + ".") for refused in _REFUSED_MODULES):
        return False
    # The exact module of the class, not isinstance: a subclass in another package is another backend (C-04).
    if module not in _OS_KEYRING_MODULES and backend_name(backend) != accepted:
        return False
    try:
        return float(backend.priority) > 0
    except Exception:  # a backend whose priority check fails is not usable here
        return False


def select_backend(environ: Mapping[str, str] | None = None) -> Any | None:
    """The best usable keyring backend, or None.

    Usable: one of the ``keyring`` package's OS keyrings (:data:`_OS_KEYRING_MODULES`), or the one backend whose
    ``module.Class`` the user names in :data:`KEYRING_BACKEND_ENV`; never ``fail``, ``null`` or the file backends of
    ``keyrings.alt``.
    """
    import keyring
    from keyring.backends.chainer import ChainerBackend

    accepted = (os.environ if environ is None else environ).get(KEYRING_BACKEND_ENV) or None
    try:
        current = keyring.get_keyring()
    except Exception:
        return None
    candidates = list(current.backends) if isinstance(current, ChainerBackend) else [current]
    usable = [backend for backend in candidates if _allowed(backend, accepted)]
    if not usable:
        return None
    return max(usable, key=lambda backend: float(backend.priority))


class KeyringStore:
    """The OS keyring through one selected backend (never the chainer, which could reach a refused backend)."""

    def __init__(self, select: Callable[[], Any | None] | None = None) -> None:
        self._select = select
        self._backend: Any | None = None
        self._checked = False
        self._lock = threading.Lock()

    def backend(self) -> Any | None:
        with self._lock:
            if not self._checked:
                self._backend = select_backend() if self._select is None else self._select()
                self._checked = True
                if self._backend is None:
                    logger.warning(
                        "no usable keyring backend: keyring: profiles need a secret entered for this session "
                        "(env: and file: references still work; there is no plain-text fallback)",
                        extra={"event": "studio.keyring_unavailable"},
                    )
                else:
                    # C-04: say which backend keeps the passwords.
                    logger.info(
                        "keyring backend: %s",
                        backend_name(self._backend),
                        extra={"event": "studio.keyring_backend"},
                    )
            return self._backend

    @property
    def available(self) -> bool:
        return self.backend() is not None

    def get(self, key: str) -> str | None:
        backend = self.backend()
        if backend is None:
            raise SecretError("studio.keyring_unavailable")
        try:
            value = backend.get_password(SERVICE, key)
        except Exception:
            logger.warning("the keyring could not be read", extra={"event": "studio.keyring_failed"})
            raise SecretError("studio.keyring_failed") from None
        if value is None:
            return None
        if not isinstance(value, str):
            raise SecretError("studio.keyring_failed")
        register_secret(value, lasting=True)
        return value

    def set(self, key: str, value: str) -> None:
        backend = self.backend()
        if backend is None:
            raise SecretError("studio.keyring_unavailable")
        try:
            backend.set_password(SERVICE, key, value)
        except Exception:
            logger.warning("the keyring could not store a secret", extra={"event": "studio.keyring_failed"})
            raise SecretError("studio.keyring_failed") from None

    def delete(self, key: str) -> None:
        backend = self.backend()
        if backend is None:
            return
        from keyring.errors import PasswordDeleteError

        try:
            backend.delete_password(SERVICE, key)
        except PasswordDeleteError:
            return  # there is none: nothing to delete
        except Exception:
            # L-3 of stu-008-review.md: the entry stays, so say so (without the backend's message).
            logger.warning("the keyring could not remove a secret", extra={"event": "studio.keyring_failed"})
            raise SecretError("studio.keyring_failed") from None


class SessionSecrets:
    """Secrets entered in the page for this process only; in memory, never written anywhere."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: str) -> None:
        register_secret(value, lasting=True)
        with self._lock:
            self._values[key] = value

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._values.get(key)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._values

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


def valid_entered_secret(value: object) -> str:
    """A secret entered in the page: a non-empty string of at most :data:`MAX_SECRET_LENGTH`, no NUL."""
    if not isinstance(value, str) or not value or len(value) > MAX_SECRET_LENGTH or "\x00" in value:
        raise SecretError("studio.secret_invalid")
    return value


@dataclass(frozen=True)
class SecretTarget:
    """Where one profile's secret comes from: its reference, its owner and where relative ``file:`` paths start."""

    ref: SecretRef | None
    owner: Owner
    key: str  # the keyring and session key
    base: Path  # the folder a relative file: path is relative to
    roots: list[Path] | None  # the folders a file: path must lie in (None: no rule, the user's own profile)


class Secrets:
    """Resolves a profile's secret at connect time (STU-009) and stores entered ones (``PUT .../secret``)."""

    def __init__(
        self,
        keyring_store: KeyringStore | None = None,
        session: SessionSecrets | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.keyring = keyring_store if keyring_store is not None else KeyringStore()
        self.session = session if session is not None else SessionSecrets()
        self._environ = environ

    @property
    def environ(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def resolve(self, target: SecretTarget) -> str:
        """The secret of ``target``; :class:`SecretError` if there is none (the page may then ask for one)."""
        ref = target.ref
        if ref is not None and ref.source == "env":
            value = self.environ.get(ref.target)
            if not value:
                raise SecretError("studio.secret_env_missing")
            register_secret(value, lasting=True)
            return value
        if ref is not None and ref.source == "file":
            return read_secret_file(ref.target, target.base, target.roots)
        # keyring:, or no password reference: the keyring entry, else a secret entered for this session.
        if ref is not None and self.keyring.available:
            value = self.keyring.get(target.key)
            if value is not None:
                return value
        session = self.session.get(target.key)
        if session is not None:
            return session
        raise SecretError("studio.secret_missing")

    def has_secret(self, target: SecretTarget) -> bool | None:
        """Whether a secret is there, without reading the keyring or a file (None: known only when connecting).

        Reading the keyring can show a system dialog (macOS), so listing profiles never does.
        """
        ref = target.ref
        if self.session.has(target.key):
            return True
        if ref is not None and ref.source == "env":
            return bool(self.environ.get(ref.target))
        return None

    def store(self, target: SecretTarget, value: object, where: object) -> None:
        """Store an entered secret in the keyring (``where = "keyring"``) or for this session (``"session"``)."""
        secret = valid_entered_secret(value)
        if target.ref is not None and target.ref.source != "keyring":
            raise SecretError("studio.secret_not_storable")  # env: and file: profiles name their own source
        if where == "keyring":
            if target.ref is None:
                raise SecretError("studio.secret_not_storable")  # the profile has no keyring: reference
            register_secret(secret, lasting=True)
            self.keyring.set(target.key, secret)
            self.session.delete(target.key)
        elif where == "session":
            self.session.set(target.key, secret)
        else:
            raise SecretError("studio.secret_invalid")

    def forget(self, target: SecretTarget) -> None:
        """Remove the session secret and, for a ``keyring:`` profile, the keyring entry."""
        self.session.delete(target.key)
        if target.ref is not None and target.ref.source == "keyring":
            self.keyring.delete(target.key)
