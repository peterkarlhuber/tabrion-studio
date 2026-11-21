"""Private files in the per-user state directory (03 section 4.3, threat T-04), on :mod:`nodestro_session.private`
(NS-003).

Directories are created with mode 0700 and files with mode 0600; symlinks are refused on every platform that
supports ``O_NOFOLLOW``.

An existing state directory is checked, never repaired behind the user's back (review F-10): one that another user
owns, or that the group or others may write to, is refused, because another user may already have placed files in
it. Read and search bits for group and others are removed, since nothing secret has been written yet. A parent that
another user controls (owned by another user than root, or writable by others without the sticky bit) is refused as
well: that user could swap the directory.

On Windows the modes are not enforced by the OS. There the state directory and each of its parents must not be a
symlink, junction or other reparse point, and a directory outside the profile is accepted only if its access control
list is private to the user (review F-07, ``nodestro_session._winsec``).

The checks live in the shared library; this module keeps Studio's folder names and environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from nodestro_session.private import (
    UnsafePathError,
    check_private_dir,
    ensure_private_dir,
    is_within,
    profile_roots,
    read_private_file,
    remove_file,
    write_private_file,
)
from nodestro_session.private import (
    # tests/unit/test_paths.py checks the reparse-point test through this module (an explicit re-export).
    _is_reparse_point as _is_reparse_point,
)
import platformdirs

__all__ = [
    "APP_NAME",
    "CONFIG_DIR_ENV",
    "LOG_DIR_ENV",
    "STATE_DIR_ENV",
    "UnsafePathError",
    "check_private_dir",
    "default_config_dir",
    "default_log_dir",
    "default_state_dir",
    "ensure_private_dir",
    "is_within",
    "platform_config_dir",
    "profile_roots",
    "read_private_file",
    "remove_file",
    "write_private_file",
]

APP_NAME = "tabrion-studio"
STATE_DIR_ENV = "TABRION_STUDIO_STATE_DIR"
LOG_DIR_ENV = "TABRION_STUDIO_LOG_DIR"
CONFIG_DIR_ENV = "TABRION_STUDIO_CONFIG_DIR"


def default_state_dir() -> Path:
    """``$TABRION_STUDIO_STATE_DIR`` if set, else the platform's per-user state directory."""
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override)
    return Path(platformdirs.user_state_dir(APP_NAME, appauthor=False))


def default_log_dir(state_dir: Path | None = None) -> Path:
    """``$TABRION_STUDIO_LOG_DIR`` if set; else ``<state_dir>/logs`` for a state directory given by option or
    environment; else the platform's per-user log directory (03 section 4.3)."""
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override)
    if state_dir is not None:
        return state_dir / "logs"
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


def default_config_dir(state_dir: Path | None = None) -> Path:
    """``$TABRION_STUDIO_CONFIG_DIR`` if set; else ``<state_dir>/config`` for a state directory given by option or
    environment; else the platform's per-user configuration directory (03 section 4.3)."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)
    if state_dir is not None:
        return state_dir / "config"
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def platform_config_dir() -> Path | None:
    """The platform's per-user configuration folder, never moved by an environment variable (the write-area flags,
    STU-XW-1, review N-1 of PR #109; G2-04 of the G-S2 gate review).

    ``platformdirs`` follows ``XDG_CONFIG_HOME`` (Linux, and macOS), ``WIN_PD_OVERRIDE_*`` (Windows) and ``HOME``,
    which a per-folder tool (direnv, a ``.env`` loader) can set when the user enters a project folder. So this folder
    is built without them: below the home folder of the account database (``pwd``) on POSIX, ``~/.config/<app>``
    (``~/Library/Application Support/<app>`` on macOS); from the known folder ``LocalAppData``
    (``SHGetKnownFolderPath``) on Windows. Usually it is the same folder as :func:`default_config_dir`; when an
    environment variable moves that one, the two differ and write profiles stay unused while an area is on
    (``write_areas.write_profiles_allowed``).

    None when there is no such folder (review L-1 of PR #125): a process whose user has no entry in the account
    database (a container started with ``--user 1234``, OpenShift) or a known-folder lookup that fails. Studio then
    starts read-only with every write area off (fail closed) and logs ``studio.write_areas_no_folder``."""
    if sys.platform == "win32":
        from platformdirs.windows import get_win_folder_via_ctypes

        try:
            return Path(get_win_folder_via_ctypes("CSIDL_LOCAL_APPDATA")) / APP_NAME
        except (OSError, ValueError, KeyError):
            return None
    import pwd

    try:
        entry = pwd.getpwuid(os.getuid())
    except KeyError:  # no account entry for this user
        return None
    if not entry.pw_dir:
        return None
    home = Path(entry.pw_dir)
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    return home / ".config" / APP_NAME
