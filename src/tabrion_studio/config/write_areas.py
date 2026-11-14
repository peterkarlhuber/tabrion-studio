"""The per-area write kill switches (STU-ADR-0006, STU-XW-1, threat TX-01).

Studio 1.1 adds areas that write to or administer databases. Each area has one server-side flag, and every flag is
**off by default**:

=====================  ====  ========  ==========================================================================
Flag (key)             Gate  Version   What it enables
=====================  ====  ========  ==========================================================================
``data_editor``        G-S2  1.1       the shared write foundation (write profiles, unlocking) and the Data Editor
``schema_designer``    G-S3  1.2       the Schema Designer (it uses write profiles too)
``database_manager``   G-S4  1.3       the Database Manager and its admin profiles
=====================  ====  ========  ==========================================================================

**The one source** is the file ``write-areas.toml`` in the configuration folder that the ``tabrion-studio`` command
line names (``--config-dir``, or ``<--state-dir>/config``), else the platform's per-user configuration folder. It is
read once, when the server starts; a change needs a restart. The environment variables that move Studio's other
folders (``TABRION_STUDIO_CONFIG_DIR``, ``TABRION_STUDIO_STATE_DIR``) never move this file, and no variable sets a
flag, because per-folder tools (direnv, ``.env`` loaders) can set variables when the user enters a project folder
(review N-1 of PR #109). Studio never reads ``.env`` files. The file gets the checks of Studio's own private files:
a regular file, not a link or junction, owned by the user and private (0600; POSIX), in a folder that only the user
may write. Anything else (missing, unreadable, unsafe, malformed, a value that is not the boolean ``true``) is off.

::

    version = 1
    [areas]
    data_editor = false
    schema_designer = false
    database_manager = false

A flag is **never** taken from the frontend, the settings file or route, a project file, a URL, a header, a cookie,
the session, a profile, a database or browser storage: nothing but :func:`load_write_areas` builds a
:class:`WriteAreas` with an area on, and the server keeps the one it read at start for its whole life. Each area's
state is logged once at start (area and on or off, nothing else), so a flag left on is visible.

**Write and admin profiles follow the same rule (review L-1 of PR #111).** While any area is on, the
``[[write_profile]]`` tables of ``profiles.toml`` are used only when the user's configuration folder is the folder of
``write-areas.toml`` (:func:`write_profiles_allowed`): one the command line named, or the platform's folder. A folder
that ``TABRION_STUDIO_CONFIG_DIR`` or ``TABRION_STUDIO_STATE_DIR`` chose supplies read profiles as before, but no write
or admin profile, so a per-folder tool cannot point Studio at write profiles (and their ``dev`` labels) inside a
project. That start is logged as a warning (``studio.write_profiles_ignored``).

**So do the audit log and the secret confirmations (review N-1 of PR #116).** The audit log (``audit/``) and the
confirmed pairs of server and secret reference (``write-secret-confirmations.json``) live in the folder of
``write-areas.toml`` (:func:`write_state_folder`), never in Studio's state folder, which ``TABRION_STUDIO_STATE_DIR``
can move. A state folder that the variable chose is not read or written for them; while an area is on, such a start
is logged as a warning (``studio.write_state_ignored``, no path).
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
import logging
from pathlib import Path
from typing import Any

from tabrion_studio.config.files import ConfigFileError, parse_toml, read_plain_file
from tabrion_studio.paths import UnsafePathError, check_private_dir

WRITE_AREAS_FILE = "write-areas.toml"
WRITE_AREAS_VERSION = 1

logger = logging.getLogger("tabrion_studio.write_areas")


class Area(enum.StrEnum):
    """One area that writes to or administers databases; its value is the flag's key in ``write-areas.toml``."""

    DATA_EDITOR = "data_editor"
    SCHEMA_DESIGNER = "schema_designer"
    DATABASE_MANAGER = "database_manager"


#: The gate that allows the owner to turn an area's flag on (E-X9). Passing a gate turns nothing on.
GATES: dict[Area, str] = {
    Area.DATA_EDITOR: "G-S2",
    Area.SCHEMA_DESIGNER: "G-S3",
    Area.DATABASE_MANAGER: "G-S4",
}


@dataclass(frozen=True)
class WriteAreas:
    """The flags as read at start: the areas that are on. Immutable; the default has every area off."""

    enabled: frozenset[Area] = frozenset()

    def on(self, area: Area) -> bool:
        return area in self.enabled

    def any_on(self, areas: frozenset[Area] | tuple[Area, ...]) -> bool:
        return any(area in self.enabled for area in areas)

    def to_dict(self) -> dict[str, bool]:
        """``{"data_editor": false, ...}``: what the page may learn (to show or hide controls; read only)."""
        return {area.value: area in self.enabled for area in Area}


#: Every area off: the default of the server, of every test app and of every example.
ALL_OFF = WriteAreas()


def flag_dir(config_dir: Path | None, state_dir: Path | None, default: Path) -> Path:
    """The folder of ``write-areas.toml``: ``--config-dir``, else ``<--state-dir>/config``, else ``default`` (the
    platform's per-user configuration folder). Only command-line arguments count: no environment variable moves it."""
    if config_dir is not None:
        return config_dir
    if state_dir is not None:
        return state_dir / "config"
    return default


def _same_folder(first: Path, second: Path) -> bool:
    try:
        return first.expanduser().resolve() == second.expanduser().resolve()
    except (OSError, RuntimeError):
        return False  # cannot tell: not the same (fail closed)


def write_profiles_allowed(areas: WriteAreas, config_dir: Path, flags_folder: Path | None) -> bool:
    """Whether the write and admin profiles of ``config_dir/profiles.toml`` may be used (review L-1 of PR #111).

    With every area off nothing uses them, and this is True. With an area on, only when ``config_dir`` is the flag
    file's folder (:func:`flag_dir`: the command line's folder or the platform's), never a folder that only an
    environment variable chose. False is logged once as a warning (no path: it may name a project folder)."""
    if not areas.enabled:
        return True
    if flags_folder is not None and _same_folder(config_dir, flags_folder):
        return True
    logger.warning(
        "write and admin profiles are not used: the configuration folder was chosen by an environment variable, "
        "not by the command line or the platform",
        extra={"event": "studio.write_profiles_ignored"},
    )
    return False


def write_state_folder(
    areas: WriteAreas, flags_folder: Path | None, state_dir: Path, state_dir_from_env: bool
) -> Path | None:
    """The folder of the audit log and the secret confirmations: the flag file's folder (review N-1 of PR #116).

    That folder is the command line's or the platform's (:func:`flag_dir`), never one an environment variable chose,
    so ``TABRION_STUDIO_STATE_DIR`` cannot point the audit log or the confirmed pairs at a folder inside a project.
    None without a flag file folder: nothing is recorded or confirmed, every write is refused (fail closed). While an
    area is on and the variable moved the state folder elsewhere, the files there are not used; that is logged once
    as a warning (no path: it may name a project folder)."""
    if flags_folder is None:
        return None
    if areas.enabled and state_dir_from_env and not _same_folder(state_dir, flags_folder):
        logger.warning(
            "the audit log and the secret confirmations are kept in the folder of write-areas.toml; those of the "
            "state folder an environment variable chose are not used",
            extra={"event": "studio.write_state_ignored"},
        )
    return flags_folder


def _areas(doc: dict[str, Any]) -> frozenset[Area]:
    if type(doc.get("version")) is not int or doc.get("version") != WRITE_AREAS_VERSION:
        logger.warning("write areas file has no supported version", extra={"event": "studio.write_areas_invalid"})
        return frozenset()
    table = doc.get("areas", {})
    if not isinstance(table, dict):
        logger.warning("write areas file is not valid", extra={"event": "studio.write_areas_invalid"})
        return frozenset()
    known = {area.value: area for area in Area}
    enabled: set[Area] = set()
    for key, value in table.items():
        area = known.get(key) if isinstance(key, str) else None
        if area is None:
            # Never the key itself: it is text of a file.
            logger.warning("write areas file names an unknown area", extra={"event": "studio.write_areas_unknown"})
            continue
        if value is True:
            enabled.add(area)
        elif value is not False:
            logger.warning("write area flag is not a boolean", extra={"event": "studio.write_areas_invalid"})
    for key in doc:
        if key not in ("version", "areas"):
            logger.warning("write areas file has an unknown key", extra={"event": "studio.write_areas_unknown"})
    return frozenset(enabled)


def load_write_areas(folder: Path) -> WriteAreas:
    """Read ``folder/write-areas.toml`` once; :data:`ALL_OFF` for anything but a valid, private file. Never raises."""
    try:
        check_private_dir(folder)
        data = read_plain_file(folder / WRITE_AREAS_FILE, private=True)
        if data is None:
            return ALL_OFF
        return WriteAreas(_areas(parse_toml(data)))
    except (ConfigFileError, UnsafePathError, OSError) as exc:
        code = exc.code if isinstance(exc, ConfigFileError) else "config.unsafe_file"
        logger.warning(
            "write areas file not used; every write area stays off",
            extra={"event": "studio.write_areas_unusable", "code": code},
        )
        return ALL_OFF
    except Exception:
        logger.exception("write areas file not read", extra={"event": "studio.write_areas_unusable"})
        return ALL_OFF


def log_write_areas(areas: WriteAreas) -> None:
    """One record per area at start: the area and on or off, nothing else (STU-ADR-0006 point 6)."""
    for area in Area:
        state = "on" if areas.on(area) else "off"
        logger.info(
            "write area %s (%s) is %s",
            area.value,
            GATES[area],
            state,
            extra={"event": "studio.write_area_state"},
        )
