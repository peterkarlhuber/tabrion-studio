"""Profile pairs, import and export of the Database Manager (STU-XDM-5; STU-ADR-0008 section 8; TX-45, GS4-21; W-10,
TB-148, K-1, N-5, I-109 j).

Imported only by a route handler after it checked the ``database_manager`` flag; with the flag off the routes answer
``405`` and neither this module nor :mod:`tabrion_studio.write.profile_export` is loaded (GS4-01).

**Profile pairs.** A pair is a read profile of the user's own store and a write or admin profile coupled to it
(``[[write_profile]]``, STU-XW-1): the existing model, unchanged. Pairs are made, changed and removed only through the
write profile routes, and the secret of a PostgreSQL pair is resolved only after its I-109 j confirmation
(:mod:`tabrion_studio.write.secret_confirm`). Import and export never touch a pair's write or admin side: the export
writes read profiles only, the import adds read profiles only, so an imported profile becomes part of a pair only
when the user couples a write or admin profile to it, and that pair then needs the I-109 j confirmation before any
secret of it is read, like every other pair.

**Export** (``export_preview``, ``export``): the shared :mod:`tabrion_studio.write.profile_export` with the area
``database_manager`` and the action ``dbm.profile_export``; its allowlist (``EXPORTED_FIELDS``) is the only one. The
preview names the write and admin profiles coupled to the chosen read profiles, which are never written (N-5). The
export is bound to the preview's ``state`` and needs the file name typed.

**Import** (``import_preview``, ``import_profiles``): a ``*.tabrion-profile.toml`` the user names, read under the
folder rules of :mod:`tabrion_studio.write.folders` (an absolute, normalized path; a folder of this user that only
they may write, reached through no link, every folder above it safe, none of Studio's own folders; the file opened
relative to the held folder without following a link, a regular file of this user that neither group nor others may
write, no hard link, at most :data:`MAX_FILE_BYTES`) and parsed by :func:`tabrion.profilefile.parse`. Refused as a
whole, never in part: a file with a host list (W-10, TB-148: never cut to one host), a TOML or format error, a plain
password, an unknown key, more than :data:`MAX_PROFILES` profiles. Each profile is then built field by field from the
same allowlist as the export (``EXPORTED_FIELDS``) and checked by Studio's own profile validator:

* it is always a **read profile** (K-1): a profile whose ``mode`` is not ``read_only`` is not imported
  (``import.mode``), and nothing of the file ever becomes a write or admin profile or a coupling;
* it never carries a secret: an ``env:NAME`` reference stays a reference; any other reference (``keyring:``,
  ``file:``, ``prompt``) becomes Studio's own ``keyring:`` entry, which holds nothing until the user enters the
  password (the prompt);
* its ``environment`` is required and kept: a PROD profile stays PROD (``import.environment_required``); a profile
  that is not PROD but names the database of a PROD profile (the user's store, a trusted project or the same file)
  is not imported (``import.prod_target``);
* a relative SQLite path is relative to the file's folder (the format's rule) and is stored as the absolute path;
* a name with a hidden character is not imported (``import.name_hidden``).

**Name clashes fail closed.** An imported profile never replaces one: a name (or the new name the user chose) that a
profile or a write or admin profile of the user's store has is refused (``studio.dbm_profile_import_exists``), at the
preview's check and again inside the save. The user chooses another name or leaves the profile out.

**Preview, typed confirmation, audit.** Every action has a preview that changes nothing; the import is bound to the
preview's ``state`` (the SHA-256 of the file's bytes) and to the user file's digest, and needs the file name typed.
``run.start`` before (no record, nothing done) and ``run.result`` after, with the area, the action, the profile names
and the file name; never the folder, a path, a reference or a secret.

**Windows** is refused for both (no ``dir_fd``, no check of a folder's access list).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import posixpath
import secrets
import sys
import tomllib
from typing import Any

from tabrion.profilefile import ConnectionProfile, ProfileFileError, parse

from tabrion_studio.config.schema import Profile, is_host_list, validate_profiles
from tabrion_studio.config.writer import SaveError
from tabrion_studio.write.audit import AuditError, AuditLog
from tabrion_studio.write.connection import WriteError, has_hidden_character
from tabrion_studio.write.folders import (
    FolderRefused,
    check_folder,
    check_new_file_path,
    held_folder,
    read_user_file,
    reserved_identities,
)
from tabrion_studio.write.prod_targets import addresses
from tabrion_studio.write.profile_export import (
    EXPORTED_FIELDS,
    SUFFIX,
    export_profiles,
    preview_export,
)

logger = logging.getLogger("tabrion_studio.write")

__all__ = [
    "AREA",
    "EXPORT_ACTION",
    "IMPORT_ACTION",
    "MAX_FILE_BYTES",
    "MAX_PROFILES",
    "export",
    "export_preview",
    "import_preview",
    "import_profiles",
]

AREA = "database_manager"
EXPORT_ACTION = "dbm.profile_export"
IMPORT_ACTION = "dbm.profile_import"
#: The largest profile file the import reads (the format's own limit, tabrion ``profilefile``).
MAX_FILE_BYTES = 256 * 1024
#: The most profiles one import file may hold (as one export writes at most).
MAX_PROFILES = 64
#: The most issues one refusal lists.
_MAX_ISSUES = 32
_PLATFORM_OK = sys.platform != "win32"

_FOLDER_REFUSALS = {
    "unsafe": "studio.dbm_profile_import_folder",
    "reserved": "studio.dbm_profile_import_reserved",
    "missing": "studio.dbm_profile_import_missing",
    "too_large": "studio.dbm_profile_import_too_large",
}


def _invalid(field: str, code: str) -> WriteError:
    return WriteError("studio.dbm_profile_import_invalid", [(f"import.{field}", f"import.{code}")])


# --- profile pairs -------------------------------------------------------------------------------------------------


def _coupled(config: Any, names: list[str]) -> list[dict[str, str]]:
    """The write and admin profiles coupled to the user profiles ``names`` (names and kinds only): what an export of
    them leaves out (N-5). Never their user, reference or secret."""
    stored = config.user.profiles().stored_write_profiles
    return [
        {"name": write.name, "kind": write.kind, "read_profile": write.read_profile}
        for write in stored
        if write.read_profile in names
    ]


# --- export ----------------------------------------------------------------------------------------------------------


def export_preview(config: Any, reserved: list[Path], doc: dict[str, Any]) -> dict[str, object]:
    """``{"profiles", "path"}``: what the export writes (the shared preview) and the coupled write and admin profiles
    it leaves out. Changes nothing."""
    answer = preview_export(config.entries(), reserved, doc)
    users = [str(item).partition(":")[2] for item in doc["profiles"] if str(item).startswith("user:")]
    return {**answer, "left_out": _coupled(config, users)}


def export(config: Any, audit: AuditLog, reserved: list[Path], doc: dict[str, Any]) -> dict[str, object]:
    """``{"profiles", "path", "typed", "state"}``: write the file through the shared export, bound to the preview."""
    state = doc.get("state")
    if not isinstance(state, str) or not state:
        raise WriteError("studio.profile_export_changed")  # no preview, no export
    exported = export_profiles(
        config.entries(),
        audit,
        reserved,
        {key: doc.get(key) for key in ("profiles", "path", "typed")},
        area=AREA,
        action=EXPORT_ACTION,
        expected_state=state,
    )
    return exported.to_dict()


# --- import: reading the file --------------------------------------------------------------------------------------


def _path(raw: object) -> tuple[str, str, str]:
    if not _PLATFORM_OK:
        raise WriteError("studio.dbm_profile_import_platform")
    try:
        return check_new_file_path(raw, (SUFFIX,))
    except FolderRefused as refused:
        raise _invalid("path", refused.code or "required") from None


def _read(folder: str, name: str, reserved: list[Path]) -> bytes:
    """The file's bytes, read under the folder rules; :class:`WriteError` for every refusal (no path in it)."""
    try:
        checked = check_folder(folder, reserved_identities(reserved))
        with held_folder(folder, checked) as descriptor:
            return read_user_file(descriptor, name, MAX_FILE_BYTES)
    except FolderRefused as refused:
        raise WriteError(_FOLDER_REFUSALS.get(refused.reason, "studio.dbm_profile_import_folder")) from None


def _has_host_list(data: bytes) -> bool:
    """Whether any profile table of the file has a ``host`` with a comma (a libpq host list, W-10). Read before the
    format's own reader, which refuses it only as a value, so the answer can say why; a file that is not TOML has
    none here (the format's reader refuses it)."""
    try:
        document = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError):
        return False
    tables = document.get("profile")
    if not isinstance(tables, list):
        return False
    return any(
        isinstance(table, dict) and isinstance(table.get("host"), str) and is_host_list(table["host"])
        for table in tables
    )


def _parsed(data: bytes) -> list[ConnectionProfile]:
    """The file's profiles; :class:`WriteError` for a file that is refused as a whole."""
    if _has_host_list(data):
        raise WriteError("studio.dbm_profile_import_multi_host", [("import.file", "import.host_list")])
    try:
        parsed = parse(data)
    except ProfileFileError as error:
        codes = sorted({issue.code.removeprefix("profilefile.") for issue in error.issues if issue.severity == "error"})
        raise WriteError(
            "studio.dbm_profile_import_invalid", [("import.file", f"import.{code}") for code in codes[:_MAX_ISSUES]]
        ) from None
    if parsed.warnings:
        # Refuse anything unexpected: an unknown key (a write key, a coupling, a key of a later format) is never
        # dropped silently.
        raise _invalid("file", "unknown_key")
    if not parsed.profiles:
        raise _invalid("file", "empty")
    if len(parsed.profiles) > MAX_PROFILES:
        raise _invalid("file", "too_many")
    return list(parsed.profiles)


# --- import: one profile -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Candidate:
    """One profile of the file as Studio would store it (``profile`` None: not importable, ``refusals`` say why)."""

    source: ConnectionProfile
    profile: Profile | None
    refusals: tuple[tuple[str, str], ...]
    dropped: tuple[str, ...]
    #: SQLite: the file as Studio would store it (a relative path made absolute against the file's folder).
    path: str | None


#: Keys of the format that Studio's profile does not have: never imported, named in the preview.
_NOT_IMPORTED = ("allow", "schema_fingerprint", "application_name", "description")


def _reference(source: ConnectionProfile) -> str | None:
    """The reference Studio stores: ``env:NAME`` kept, any other reference Studio's empty keyring entry (a prompt)."""
    ref = source.password
    if ref is None or source.engine == "sqlite":
        return None  # a SQLite file has no password
    if ref.kind == "env" and ref.target:
        return f"env:{ref.target}"
    return "keyring:"


def _raw(source: ConnectionProfile, name: str, folder: str) -> dict[str, Any]:
    """The table Studio's validator checks: the allowlisted fields (``EXPORTED_FIELDS``) and the reference only."""
    raw: dict[str, Any] = {}
    for key in EXPORTED_FIELDS:
        value = getattr(source, key)
        if value is not None:
            raw[key] = value
    raw["name"] = name
    if source.engine == "sqlite" and isinstance(raw.get("path"), str) and not posixpath.isabs(raw["path"]):
        raw["path"] = posixpath.normpath(posixpath.join(folder, raw["path"]))  # the format: relative to the file
    if not raw.get("read_only_role"):
        raw.pop("read_only_role", None)  # the format's default False: Studio keeps "not stated"
    reference = _reference(source)
    if reference is not None:
        raw["password"] = reference
    return raw


def _studio_profile(source: ConnectionProfile, name: str, folder: str) -> tuple[Profile | None, list[tuple[str, str]]]:
    if has_hidden_character(name):
        return None, [("name", "import.name_hidden")]
    checked = validate_profiles({"version": 1, "profile": [_raw(source, name, folder)]}, owner="user")
    refusals = [
        (issue.pointer.partition(".")[2] or "profile", issue.code)
        for issue in checked.issues
        if issue.severity == "error"
    ]
    if refusals or len(checked.profiles) != 1:
        return None, refusals or [("profile", "import.not_valid")]
    return checked.profiles[0], []


def _same_target(one: Profile, other: Profile) -> bool:
    """Whether two profiles name the same database: SQLite the same file (device and inode, else the same path);
    PostgreSQL the same database name and port on overlapping addresses (``prod_targets.addresses``)."""
    if one.engine != other.engine:
        return False
    if one.engine == "sqlite":
        if one.path is None or other.path is None:
            return False
        try:
            first, second = os.stat(Path(one.path).expanduser()), os.stat(Path(other.path).expanduser())
        except OSError:
            return posixpath.normpath(one.path) == posixpath.normpath(other.path)
        return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)
    if one.database != other.database or (one.port or 5432) != (other.port or 5432):
        return False
    return bool(addresses(one.host, one.port) & addresses(other.host, other.port))


def _candidate(source: ConnectionProfile, name: str, folder: str, prods: list[Profile]) -> _Candidate:
    dropped = tuple(key for key in _NOT_IMPORTED if getattr(source, key) not in (None, ()))
    refusals: list[tuple[str, str]] = []
    if source.mode != "read_only":
        refusals.append(("mode", "import.mode"))  # K-1: never a write or admin profile
    if source.environment is None:
        refusals.append(("environment", "import.environment_required"))
    profile, issues = _studio_profile(source, name, folder)
    refusals.extend(issues)
    # A profile that is not PROD may not name a PROD database: an import never relabels PROD (E-X7).
    if profile is not None and profile.environment != "prod" and any(_same_target(profile, p) for p in prods):
        refusals.append(("environment", "import.prod_target"))
    if refusals:
        profile = None
    path = _raw(source, name, folder).get("path") if source.engine == "sqlite" else None
    return _Candidate(source, profile, tuple(dict.fromkeys(refusals)), dropped, path)


def _prods(config: Any, sources: list[ConnectionProfile], folder: str) -> list[Profile]:
    """The PROD profiles an imported profile is compared with: the user's, a trusted project's, the file's own."""
    prods = [entry.profile for entry in config.entries() if entry.profile.environment == "prod"]
    for source in sources:
        if source.environment == "prod":
            profile, _ = _studio_profile(source, source.name, folder)
            if profile is not None:
                prods.append(profile)
    return prods


def _taken(config: Any) -> set[str]:
    """The names an imported profile may not take: every read, write and admin profile of the user's store."""
    user = config.user.profiles()
    return {profile.name for profile in user.profiles} | {write.name for write in user.stored_write_profiles}


def _describe(candidate: _Candidate, taken: set[str]) -> dict[str, object]:
    source, profile = candidate.source, candidate.profile
    reference = None if profile is None or profile.password is None else profile.password.to_dict()
    return {
        "name": source.name,
        "engine": source.engine,
        "environment": source.environment,
        "path": candidate.path,
        "host": source.host,
        "port": source.port,
        "database": source.database,
        "user": source.user,
        "sslmode": source.sslmode,
        "mode": source.mode,
        # Where the password will come from: an env: variable's name, or "keyring" (empty until the user enters it).
        "password": reference,
        "importable": profile is not None,
        "refusals": [{"field": field, "code": code} for field, code in candidate.refusals],
        "not_imported": list(candidate.dropped),
        "name_taken": source.name in taken,
    }


def _state(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_preview(config: Any, reserved: list[Path], doc: dict[str, Any]) -> dict[str, object]:
    """``{"path"}``: what the file holds and what of it Studio would import, as which read profile; changes nothing."""
    path, folder, name = _path(doc.get("path"))
    data = _read(folder, name, reserved)
    sources = _parsed(data)
    prods = _prods(config, sources, folder)
    taken = _taken(config)
    user = config.user.profiles()
    return {
        "path": path,
        "file_name": name,
        "state": _state(data),
        "profiles": [_describe(_candidate(source, source.name, folder, prods), taken) for source in sources],
        "user_file": {"digest": user.digest, "hand_written": not user.canonical},
    }


# --- import: the save ----------------------------------------------------------------------------------------------


def _choices(raw: object, sources: list[ConnectionProfile]) -> list[tuple[ConnectionProfile, str]]:
    """``[{"name": <in the file>, "as": <new name>}]``: each a profile of the file at most once, each new name once."""
    if not isinstance(raw, list) or not raw:
        raise _invalid("profiles", "required")
    if len(raw) > MAX_PROFILES:
        raise _invalid("profiles", "too_many")
    by_name = {source.name: source for source in sources}
    chosen: list[tuple[ConnectionProfile, str]] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"name", "as"} or not isinstance(item.get("name"), str):
            raise _invalid("profiles", "type")
        new = item.get("as", item["name"])
        if not isinstance(new, str) or not new:
            raise _invalid("profiles", "type")
        source = by_name.get(item["name"])
        if source is None:
            raise _invalid("profiles", "unknown")
        chosen.append((source, new))
    if len({source.name for source, _ in chosen}) != len(chosen) or len({new for _, new in chosen}) != len(chosen):
        raise _invalid("profiles", "duplicate")
    return chosen


def import_profiles(config: Any, audit: AuditLog, reserved: list[Path], doc: dict[str, Any]) -> dict[str, object]:
    """``{"path", "typed", "state", "profiles", "digest", "overwrite"}``: add the chosen profiles of the previewed
    file to the user's store as read profiles. Raises :class:`WriteError` or :class:`SaveError`."""
    _, folder, name = _path(doc.get("path"))
    typed = doc.get("typed")
    if not isinstance(typed, str) or typed != name:
        raise WriteError("studio.dbm_profile_import_unconfirmed")
    overwrite = doc.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise _invalid("overwrite", "type")
    data = _read(folder, name, reserved)
    if doc.get("state") != _state(data):
        raise WriteError("studio.dbm_profile_import_changed")  # the file is not the one previewed
    sources = _parsed(data)
    chosen = _choices(doc.get("profiles"), sources)
    prods = _prods(config, sources, folder)
    taken = _taken(config)
    profiles: list[Profile] = []
    refused: list[tuple[str, str]] = []
    for source, new in chosen:
        candidate = _candidate(source, new, folder, prods)
        if candidate.profile is None:
            refused.extend(("import.profiles", code) for _, code in candidate.refusals)
        else:
            profiles.append(candidate.profile)
    if refused:
        raise WriteError("studio.dbm_profile_import_refused", list(dict.fromkeys(refused))[:_MAX_ISSUES])
    if any(profile.name in taken for profile in profiles):
        raise WriteError("studio.dbm_profile_import_exists", [("import.profiles", "import.name_taken")])
    try:
        audit.check()
    except AuditError:
        raise WriteError("studio.audit_unavailable") from None
    names = [profile.name for profile in profiles]
    run = secrets.token_hex(8)
    common: dict[str, object] = {"run": run, "area": AREA, "action": IMPORT_ACTION}
    try:
        audit.append("run.start", **common, objects=names, target=name)
    except AuditError:
        logger.warning("profiles not imported: no audit record", extra={"event": "studio.audit_unavailable"})
        raise WriteError("studio.audit_unavailable") from None

    def change(current: list[Profile]) -> list[Profile]:
        # Again inside the save: a profile added since the check above is never replaced (fail closed).
        present = {profile.name for profile in current} | {
            write.name for write in config.user.profiles().stored_write_profiles
        }
        if any(profile.name in present for profile in profiles):
            raise WriteError("studio.dbm_profile_import_exists", [("import.profiles", "import.name_taken")])
        return [*current, *profiles]

    result = "failed"
    try:
        new_digest = config.user.save_profiles(change, doc.get("digest"), overwrite=overwrite)
        result = "committed"
    except (WriteError, SaveError) as refused_save:
        result = refused_save.code
        raise
    except ValueError:
        result = "studio.config_not_storable"
        raise
    finally:
        pending = not audit.record("run.result", **common, result=result)
    logger.info("profiles imported", extra={"event": "studio.profiles_imported"})
    return {
        "imported": names,
        "file_name": name,
        "digest": new_digest,
        "audit_pending": pending,
    }
