"""The profile export (STU-CR-6 part 1; TX-30, GS3-22, W-10, TB-148, N-5; STU-ADR-0007 section 12): read profiles
written as a tabrion connection profile file (format v1, :func:`tabrion.profilefile.dump`) into a new file the user
names. Shared: the profile export of STU-XDM-5 (the Database Manager) calls :func:`export_profiles` with its own area
and action, after its preview (:func:`preview_export`) and bound to it (``expected_state``).

Imported only by a route handler after it checked its area's flag (the Schema Designer's ``schema_designer`` or the
Database Manager's ``database_manager``); with the flag off the route answers ``405`` and this module is not loaded
(GS3-01, GS4-01).

**What is written: an allowlist, never a copy (N-5, TX-30).** Only **read profiles** (``user:<name>`` and, while
the project file is trusted, ``project:<name>``, as :meth:`Configuration.entries` lists them) can be named; a write
or admin profile has no such id and is never read here. Each exported profile is built field by field from
:data:`EXPORTED_FIELDS` of the read profile, with ``mode = "read_only"``; nothing else of Studio's profile, its
coupled write or admin profiles, or any secret store is read. The password is a reference only: an ``env:NAME``
reference is kept (a variable's name, no secret); a ``keyring:`` or ``file:`` reference becomes ``prompt`` (Studio's
keyring entry and a local file path mean nothing to another program; the consumer asks the user). No secret value
is ever resolved by the export.

**Single host only (W-10, TB-148).** A profile whose host is a libpq host list (``db1,db2``) is refused with
``studio.profile_export_multi_host``, never cut to its first host. Studio's own validator refuses such a profile
already (``config.host_list``); the export checks again, so a later change of the validator cannot let one through.

**The file (the rules of :mod:`tabrion_studio.write.folders`).** ``path`` is an absolute, normalized local path whose
name ends in ``.tabrion-profile.toml``; ``typed`` is that file name (the confirmation). The folder is this user's,
only this user may write it, it is reached through no link, every folder above it is safe, and neither it nor a
folder above it is one of Studio's own folders (by device and inode). The file is created relative to the held
folder with ``O_CREAT|O_EXCL|O_NOFOLLOW`` and mode ``0600``: an existing file or link is never overwritten
(``studio.profile_export_exists``). On Windows Studio writes no export (``studio.profile_export_platform``): it does
not check a folder's access list there and has no ``dir_fd``.

**Preview and binding (STU-XDM-5).** :func:`preview_export` runs every check but the typed name and writes nothing:
it answers what each profile is written with (engine, environment, the kind of reference, never a secret) and a
``state``, the SHA-256 of the file's text and path. :func:`export_profiles` given that ``expected_state`` refuses an
export whose text or path is no longer the one previewed (``studio.profile_export_changed``).

**Audit.** ``run.start`` before the file is created (no record, no file: ``studio.audit_unavailable``) and
``run.result`` after it, with the area, the action, the profile names and the file name, never the folder or a
secret reference.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import secrets
import sys
from typing import Any

from tabrion.profilefile import ConnectionProfile, ProfileFileError, SecretRef, dump

from tabrion_studio.config.schema import Profile, is_abstract_socket, is_host_list
from tabrion_studio.write.audit import AuditError, AuditLog
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.folders import (
    FolderRefused,
    check_folder,
    check_new_file_path,
    create_new_file,
    held_folder,
    reserved_identities,
)

logger = logging.getLogger("tabrion_studio.write")

__all__ = [
    "EXPORTED_FIELDS",
    "MAX_PROFILES",
    "SUFFIX",
    "ExportedFile",
    "export_profiles",
    "exported_profile",
    "preview_export",
    "reference_kind",
]

#: The ending of an export's file name (doc 14 section 3.5: ``*.tabrion-profile.toml``).
SUFFIX = ".tabrion-profile.toml"
#: The most profiles one export writes.
MAX_PROFILES = 64
#: The fields of a read profile an export copies, and only these (N-5, TX-30). The password is handled apart
#: (:func:`_reference`); ``mode`` is always ``read_only``.
EXPORTED_FIELDS = (
    "name",
    "engine",
    "path",
    "host",
    "port",
    "database",
    "user",
    "sslmode",
    "environment",
    "read_only_role",
    "statement_timeout_ms",
)
_ID_PREFIXES = ("user:", "project:")
_PLATFORM_OK = sys.platform != "win32"


def _invalid(field: str, code: str) -> WriteError:
    return WriteError("studio.profile_export_invalid", [(f"export.{field}", f"export.{code}")])


def _reference(profile: Profile) -> SecretRef | None:
    """The password reference written for ``profile``: ``env:NAME`` as it is, any other reference ``prompt``."""
    ref = profile.password
    if ref is None:
        return None
    if ref.source == "env":
        return SecretRef("env", ref.target)
    return SecretRef("prompt")


def reference_kind(profile: Profile) -> str | None:
    """What the file says about the password: ``env`` (the variable's name), ``prompt`` or None (no password)."""
    ref = _reference(profile)
    return None if ref is None else ref.kind


def exported_profile(profile: Profile) -> ConnectionProfile:
    """The tabrion profile written for one Studio read profile: :data:`EXPORTED_FIELDS`, the reference, read only.

    A PostgreSQL profile without a port is written with ``5432``, the port Studio connects to; the file states it, so
    it reads back the same."""
    values: dict[str, Any] = {key: getattr(profile, key) for key in EXPORTED_FIELDS}
    values["read_only_role"] = bool(values["read_only_role"])
    host = values["host"]
    if values["port"] is None and host is not None and not host.startswith("/"):
        values["port"] = 5432  # Studio's own reading of no port (config.schema); the file states it
    return ConnectionProfile(**values, password=_reference(profile), mode="read_only")


def _chosen(entries: Iterable[Any], raw: object) -> list[Profile]:
    """The read profiles the request names, in its order; :class:`WriteError` for a list that is not valid."""
    if not isinstance(raw, list) or not raw:
        raise _invalid("profiles", "required")
    if len(raw) > MAX_PROFILES:
        raise _invalid("profiles", "too_many")
    if not all(isinstance(item, str) and item.startswith(_ID_PREFIXES) for item in raw):
        raise _invalid("profiles", "type")
    if len(set(raw)) != len(raw):
        raise _invalid("profiles", "duplicate")
    by_id = {entry.id: entry.profile for entry in entries}
    chosen = [by_id.get(item) for item in raw]
    found = [profile for profile in chosen if profile is not None]
    if len(found) != len(raw):
        raise _invalid("profiles", "unknown")
    if len({profile.name for profile in found}) != len(found):
        raise _invalid("profiles", "same_name")  # a user and a project profile of one name: the file needs one
    return found


@dataclass(frozen=True)
class ExportedFile:
    path: str
    name: str
    profiles: int
    audit_pending: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "file_name": self.name,
            "profiles": self.profiles,
            "mode": "0600",
            "audit_pending": self.audit_pending,
        }


_REFUSALS = {
    "unsafe": "studio.profile_export_folder",
    "reserved": "studio.profile_export_reserved",
    "exists": "studio.profile_export_exists",
}


def _prepared(entries: Sequence[Any], doc: dict[str, Any]) -> tuple[list[Profile], str, str, str, str]:
    """The chosen profiles, the path, its folder and file name, and the file's text; :class:`WriteError` if the
    request is not valid (every check that needs no folder)."""
    profiles = _chosen(entries, doc.get("profiles"))
    for profile in profiles:
        if profile.host is not None and is_host_list(profile.host):
            raise WriteError("studio.profile_export_multi_host", [("export.profiles", "export.host_list")])
        if profile.host is not None and is_abstract_socket(profile.host):
            # L-1 of #153: refused by the validator already; never written to a file another program reads.
            raise WriteError("studio.profile_export_invalid", [("export.profiles", "export.host_abstract_socket")])
    try:
        path, folder, name = check_new_file_path(doc.get("path"), (SUFFIX,))
    except FolderRefused as refused:
        raise _invalid("path", refused.code or "required") from None
    try:
        text = dump(exported_profile(profile) for profile in profiles)
    except ProfileFileError:
        raise _invalid("profiles", "not_storable") from None
    return profiles, path, folder, name, text


def _state(path: str, text: str) -> str:
    """The binding of a preview: the SHA-256 of the file's path and text (no secret is in either)."""
    return hashlib.sha256(f"{path}\0{text}".encode()).hexdigest()


def preview_export(entries: Sequence[Any], reserved: Iterable[Path], doc: dict[str, Any]) -> dict[str, object]:
    """What :func:`export_profiles` would write for ``doc`` (``profiles``, ``path``), checked as the export checks it
    (but for the typed name), writing nothing: the file name, each profile's name, source, engine, environment and
    kind of reference, and the ``state`` the export must be given. Raises :class:`WriteError` (see the module)."""
    if not _PLATFORM_OK:
        raise WriteError("studio.profile_export_platform")
    profiles, path, folder, name, text = _prepared(entries, doc)
    try:
        check_folder(folder, reserved_identities(reserved))
    except FolderRefused as refused:
        raise WriteError(_REFUSALS.get(refused.reason, "studio.profile_export_folder")) from None
    if os.path.lexists(path):
        raise WriteError("studio.profile_export_exists")  # the export checks it again when it creates the file
    ids: list[str] = doc["profiles"]  # checked by _chosen: one id of an entry per profile, in this order
    return {
        "path": path,
        "file_name": name,
        "profiles": [
            {
                "id": profile_id,
                "name": profile.name,
                "source": profile_id.partition(":")[0],
                "engine": profile.engine,
                "environment": profile.environment,
                "password": reference_kind(profile),
                "mode": "read_only",
            }
            for profile_id, profile in zip(ids, profiles, strict=True)
        ],
        "state": _state(path, text),
    }


def export_profiles(
    entries: Sequence[Any],
    audit: AuditLog,
    reserved: Iterable[Path],
    doc: dict[str, Any],
    *,
    area: str,
    action: str,
    expected_state: object = None,
) -> ExportedFile:
    """Write the read profiles ``doc["profiles"]`` names (ids of ``entries``) to the new file ``doc["path"]``.

    ``entries`` are the read profiles the page lists (:meth:`Configuration.entries`); ``area`` and ``action`` name
    the caller in the audit log. ``expected_state`` (when not None) is the ``state`` of :func:`preview_export`: a
    file whose text or path differs is refused. Raises :class:`WriteError` (see the module text)."""
    profiles, path, folder, name, text = _prepared(entries, doc)
    typed = doc.get("typed")
    if not isinstance(typed, str) or typed != name:
        raise WriteError("studio.profile_export_unconfirmed")
    if expected_state is not None and expected_state != _state(path, text):
        raise WriteError("studio.profile_export_changed")
    if not _PLATFORM_OK:
        raise WriteError("studio.profile_export_platform")
    try:
        checked = check_folder(folder, reserved_identities(reserved))
    except FolderRefused as refused:
        raise WriteError(_REFUSALS.get(refused.reason, "studio.profile_export_folder")) from None
    run = secrets.token_hex(8)
    common: dict[str, object] = {"run": run, "area": area, "action": action}
    try:
        audit.append("run.start", **common, objects=[profile.name for profile in profiles], target=name)
    except AuditError:
        logger.warning("profiles not exported: no audit record", extra={"event": "studio.audit_unavailable"})
        raise WriteError("studio.audit_unavailable") from None
    result = "failed"
    try:
        with held_folder(folder, checked) as descriptor:
            create_new_file(descriptor, name, text.encode("utf-8"))
        result = "committed"
    except FolderRefused as refused:
        result = _REFUSALS.get(refused.reason, "studio.profile_export_folder")
        raise WriteError(result) from None
    except OSError:
        result = "studio.profile_export_failed"
        logger.warning("profiles not exported", extra={"event": "studio.profile_export_failed"})
        raise WriteError(result) from None
    finally:
        pending = not audit.record("run.result", **common, result=result)
    logger.info("profiles exported", extra={"event": "studio.profiles_exported"})
    return ExportedFile(path, name, len(profiles), pending)
