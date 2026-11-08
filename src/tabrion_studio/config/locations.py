"""Paths from an untrusted project file, judged as text before any file-system access (T-08, review T-2).

A project file comes from a repository that may be hostile (AT4). On Windows, merely resolving a path such as
``//attacker.example/share/x.db`` (``Path.resolve()`` calls ``GetFinalPathNameByHandle``, which opens it) makes an
SMB connection to that host and sends the user's NTLM challenge-response; ``\\\\host@80\\...`` does the same over
WebDAV. On macOS, ``/net/<host>/...`` (the autofs ``-hosts`` map, on by default) beacons in the same way. So:

1. :func:`network_or_device` refuses, as text, UNC paths (``\\\\host``, ``//host``, also mixed separators), device
   paths (``\\\\?\\``, ``\\\\.\\``), drive-relative paths (``C:foo``, ``C:``), ``file:`` URLs with a host
   (``file://host/x``) and the automount folders ``/net/<host>`` and ``/Network/`` on every platform: the same
   project file can be opened on any of them. The schema reports these as ``config.network_path``.
2. :func:`lexical_target` and :func:`lexically_within` decide containment as pure path logic (``ntpath`` or
   ``posixpath`` by the flavour of the base path, never the file system).
3. Only a path that is lexically inside a root is resolved, and then by :func:`safe_realpath`, which walks it one
   component at a time with ``lstat``/``readlink`` and refuses a link whose *target text* is a network or device
   form before following it. ``Path.resolve()`` is never called on a value from the project file.
"""

from __future__ import annotations

from collections.abc import Iterable
import ntpath
import os
from pathlib import Path, PurePath, PureWindowsPath
import posixpath
import stat

_SEPARATORS = "\\/"
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
_MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT (a junction)
_MAX_LINKS = 40
#: Automount folders that reach other hosts by name (macOS ``/net/<host>``, ``/Network/Servers/<host>``).
_AUTOMOUNT = ("/net", "/network")


def network_or_device(text: str) -> bool:
    """Whether ``text`` names another host, a device or a drive-relative path (refused as text, review T-2)."""
    value = text
    if value[:5].lower() == "file:":  # a file: URL (or a file: reference): judge what follows the scheme
        value = value[5:]
    if len(value) >= 2 and value[0] in _SEPARATORS and value[1] in _SEPARATORS:
        return True  # \\host\share, //host/share, \\?\..., \\.\..., file://host/...
    drive = len(value) >= 2 and value[1] == ":" and value[0].isascii() and value[0].isalpha()
    if drive and (len(value) == 2 or value[2] not in _SEPARATORS):
        return True  # C:foo is relative to the current folder of drive C (not this project)
    if value[:1] in _SEPARATORS:
        folded = posixpath.normpath(value.replace("\\", "/")).lower()
        if any(folded == folder or folded.startswith(folder + "/") for folder in _AUTOMOUNT):
            return True
    return False


def lexical_target(text: str, base: PurePath) -> PurePath | None:
    """The path ``text`` names relative to the absolute folder ``base``, normalized as text; None if refused.

    Pure path logic: works the same for ``PureWindowsPath`` and ``PurePosixPath`` on any platform.
    """
    if network_or_device(text):
        return None
    module = ntpath if isinstance(base, PureWindowsPath) else posixpath
    # The base is a folder the user chose (it may itself be on a share); only the value from the file is judged.
    return type(base)(module.normpath(module.join(str(base), text)))


def lexically_within(target: PurePath, roots: Iterable[PurePath]) -> bool:
    """Whether ``target`` is one of ``roots`` or inside one, compared as text (case-folded for Windows paths)."""
    windows = isinstance(target, PureWindowsPath)
    module = ntpath if windows else posixpath
    name = module.normcase(str(target))
    sep = "\\" if windows else "/"
    for root in roots:
        if isinstance(root, PureWindowsPath) != windows:
            continue
        base = module.normcase(module.normpath(str(root))).rstrip(_SEPARATORS)
        if base and (name == base or name.startswith(base + sep)):
            return True
    return False


def _is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & _REPARSE) and getattr(info, "st_reparse_tag", 0) == _MOUNT_POINT


def _local_link_target(target: str) -> str:
    """A Windows link target without the ``\\\\?\\`` prefix that ``os.readlink`` adds to a local drive path."""
    if target.startswith("\\\\?\\") and len(target) >= 7 and target[5] == ":" and target[6] == "\\":
        return target[4:]
    return target


def safe_realpath(path: Path) -> Path | None:
    """The real path of the absolute ``path``, following links one at a time; None if a link leads to a refused form.

    Unlike ``Path.resolve()``, this never opens a path: it ``lstat``s each existing component and reads link targets
    as text, so a link to ``//host/share`` or ``/net/host`` is refused before anything touches that host. Components
    that do not exist yet are kept as they are (like ``resolve(strict=False)``).
    """
    if not path.is_absolute():
        return None
    current = Path(path.anchor)
    pending = list(path.parts[1:])
    links = 0
    while pending:
        name = pending.pop(0)
        if name in ("", "."):
            continue
        if name == "..":
            current = current.parent
            continue
        candidate = current / name
        try:
            info = os.lstat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            rest = PurePath(*pending) if pending else None
            tail = candidate if rest is None else candidate / rest
            return Path(os.path.normpath(tail))
        except OSError:
            return None
        if not _is_link(info):
            current = candidate
            continue
        links += 1
        if links > _MAX_LINKS:
            return None
        try:
            target = _local_link_target(os.readlink(candidate))
        except (OSError, ValueError):
            return None
        if not target or network_or_device(target):
            return None
        link = PurePath(target)
        if link.root:
            current = Path((link.drive or current.drive) + link.root)
            pending = list(link.parts[1:]) + pending
        elif link.drive:
            return None  # drive-relative (already refused as text; kept for safety)
        else:
            pending = list(link.parts) + pending
    return current


def contained(text: str, base: Path, lexical_roots: list[Path], real_roots: list[Path]) -> bool:
    """Whether ``text`` (as written in a profile file, relative to ``base``) stays inside the roots.

    First as text (refused forms, then lexical containment); only then on the file system, without following a link
    to a refused form. Every root list is trusted (the project and configuration folders the user chose).
    """
    target = lexical_target(text, PurePath(os.path.abspath(base)))
    if target is None or not lexically_within(target, [PurePath(root) for root in lexical_roots + real_roots]):
        return False
    real = safe_realpath(Path(target))
    if real is None:
        return False
    return lexically_within(PurePath(real), [PurePath(root) for root in lexical_roots + real_roots])
