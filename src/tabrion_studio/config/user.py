"""The user's own configuration: ``settings.toml`` and ``profiles.toml`` (03 section 4.3, STU-006).

Both live in the configuration folder (``platformdirs.user_config_dir("tabrion-studio")``, ``--config-dir`` or
``$TABRION_STUDIO_CONFIG_DIR``). They belong to the user, so they need no trust dialog, but ``profiles.toml`` must be
private (0600, owned by the user; POSIX) and neither may be a link. A file that cannot be used is reported as an
issue and ignored: Studio then runs with the default settings and without user profiles.

Studio writes both files itself (STU-008, :mod:`tabrion_studio.config.writer`): :meth:`UserConfig.save_profiles` and
:meth:`UserConfig.save_settings` check the folder (0700, no link), refuse a file that changed since the page read it
(``studio.config_stale``) or that has issues (``studio.config_invalid``), ask before they replace a file written by
hand (``studio.config_hand_written``, its comments are not kept) and write atomically with mode 0600.

**Write and admin profiles (STU-XW-1, review L-1 of PR #111).** ``[[write_profile]]`` tables are used only when the
configuration folder is one the command line named (``--config-dir``, ``<--state-dir>/config``) or the platform's
folder: the rule of the flag file (``write-areas.toml``, N-1 of PR #109). When a write area is on and the folder came
from ``TABRION_STUDIO_CONFIG_DIR`` or ``TABRION_STUDIO_STATE_DIR`` (which a per-folder tool such as direnv can set),
the server builds :class:`UserConfig` with ``write_profiles_allowed=False``: :meth:`UserConfig.profiles` then has no
write profiles (they are kept in the file, :attr:`UserProfiles.stored_write_profiles`, so a save never drops them)
and :meth:`UserConfig.save_write_profiles` refuses (``studio.write_profiles_ignored``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tabrion_studio.config.files import ConfigFileError, parse_toml, read_plain_file
from tabrion_studio.config.schema import Issue, Profile, Settings, WriteProfile, validate_profiles, validate_settings
from tabrion_studio.config.writer import (
    SaveError,
    checked_profiles,
    checked_settings,
    digest,
    dump_profiles,
    dump_settings,
    write_atomic,
)
from tabrion_studio.paths import UnsafePathError, ensure_private_dir

SETTINGS_FILE = "settings.toml"
PROFILES_FILE = "profiles.toml"


@dataclass
class UserProfiles:
    profiles: list[Profile] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    #: The SHA-256 of the file as read (None: there is none); a save must name it (``studio.config_stale``).
    digest: str | None = None
    #: Whether the file is exactly what Studio would write (False: written by hand, comments would be lost).
    canonical: bool = True
    #: The write and admin profiles (STU-XW-1): only here, never in a project file (K-1). Empty when the folder may
    #: not supply them (``write_profiles_allowed=False``, review L-1 of PR #111).
    write_profiles: list[WriteProfile] = field(default_factory=list)
    #: Every write and admin profile of the file, also those not used: for keeping the file whole when it is saved
    #: and for keeping their couplings valid. Never for unlocking or listing.
    stored_write_profiles: list[WriteProfile] = field(default_factory=list)
    #: Whether the file has write or admin profiles that are not used because of where the folder came from.
    write_profiles_ignored: bool = False


class UserConfig:
    def __init__(self, config_dir: Path, *, write_profiles_allowed: bool = True) -> None:
        self.config_dir = config_dir
        #: False: the folder came from an environment variable while a write area is on (L-1 of PR #111).
        self.write_profiles_allowed = write_profiles_allowed

    def settings(self) -> tuple[Settings, list[Issue]]:
        settings, issues, _, _ = self.settings_file()
        return settings, issues

    def settings_file(self) -> tuple[Settings, list[Issue], str | None, bool]:
        """The settings, their issues, the file's digest and whether Studio wrote it as it is."""
        data: bytes | None = None
        try:
            data = read_plain_file(self.config_dir / SETTINGS_FILE)
            if data is None:
                return Settings(), [], None, True
            settings, issues = validate_settings(parse_toml(data))
        except ConfigFileError as exc:
            pointer = SETTINGS_FILE if exc.line is None else f"{SETTINGS_FILE} line {exc.line}"
            return Settings(), [Issue(pointer, exc.code)], digest(data), False
        return settings, issues, digest(data), dump_settings(settings) == data

    def profiles(self) -> UserProfiles:
        data: bytes | None = None
        try:
            data = read_plain_file(self.config_dir / PROFILES_FILE, private=True)
            if data is None:
                return UserProfiles()
            parsed = validate_profiles(parse_toml(data), owner="user")
        except ConfigFileError as exc:
            pointer = PROFILES_FILE if exc.line is None else f"{PROFILES_FILE} line {exc.line}"
            return UserProfiles(issues=[Issue(pointer, exc.code)], digest=digest(data), canonical=False)
        # Valid profiles are used even if others in the file are not; the issues say which ones were skipped.
        allowed = self.write_profiles_allowed
        return UserProfiles(
            parsed.profiles,
            parsed.issues,
            digest(data),
            canonical=dump_profiles(parsed.profiles, parsed.write_profiles) == data,
            write_profiles=list(parsed.write_profiles) if allowed else [],
            stored_write_profiles=list(parsed.write_profiles),
            write_profiles_ignored=not allowed and bool(parsed.write_profiles),
        )

    def _folder(self) -> None:
        try:
            ensure_private_dir(self.config_dir)
        except (OSError, UnsafePathError):
            raise SaveError("studio.config_dir_unsafe") from None

    @staticmethod
    def _check(current: str | None, issues: list[Issue], canonical: bool, expected: object, overwrite: bool) -> None:
        if expected != current:
            raise SaveError("studio.config_stale")  # changed by hand (or by another tab) since the page read it
        if issues:
            raise SaveError("studio.config_invalid")  # never drop what the user wrote, not even an unknown key
        if not canonical and not overwrite:
            raise SaveError("studio.config_hand_written")

    def save_profiles(
        self, change: Callable[[list[Profile]], list[Profile]], expected: object, *, overwrite: bool = False
    ) -> str:
        """Apply ``change`` to the user's profiles and write the file; the new digest. Raises :class:`SaveError`."""
        self._folder()
        current = self.profiles()
        self._check(current.digest, current.issues, current.canonical, expected, overwrite)
        # The write and admin profiles stay as they are (their own save below), also those not used here (L-1).
        data = checked_profiles(change(list(current.profiles)), current.stored_write_profiles)
        self._recheck(PROFILES_FILE, current.digest, private=True)
        self._write(PROFILES_FILE, data)
        return digest(data) or ""

    def save_write_profiles(
        self,
        change: Callable[[list[Profile], list[WriteProfile]], list[WriteProfile]],
        expected: object,
        *,
        overwrite: bool = False,
    ) -> str:
        """Apply ``change`` to the user's write and admin profiles (STU-XW-1) and write the file; the new digest.

        ``change`` gets the read profiles too, to check a coupling; the read profiles stay as they are. Raises
        :class:`SaveError`, and ValueError for a list that would not read back (a coupling that is not valid).
        Refused (``studio.write_profiles_ignored``) when this folder may not supply write profiles (L-1)."""
        if not self.write_profiles_allowed:
            raise SaveError("studio.write_profiles_ignored")
        self._folder()
        current = self.profiles()
        self._check(current.digest, current.issues, current.canonical, expected, overwrite)
        reads = list(current.profiles)
        data = checked_profiles(reads, change(reads, list(current.write_profiles)))
        self._recheck(PROFILES_FILE, current.digest, private=True)
        self._write(PROFILES_FILE, data)
        return digest(data) or ""

    def save_settings(self, settings: Settings, expected: object, *, overwrite: bool = False) -> str:
        """Write ``settings``; the new digest. Raises :class:`SaveError`."""
        self._folder()
        _, issues, current, canonical = self.settings_file()
        self._check(current, issues, canonical, expected, overwrite)
        data = checked_settings(settings)
        self._recheck(SETTINGS_FILE, current, private=False)
        self._write(SETTINGS_FILE, data)
        return digest(data) or ""

    def _recheck(self, name: str, expected: str | None, *, private: bool) -> None:
        """Read the file once more just before the replace (I-3 of stu-008-review.md): a hand edit meanwhile wins.

        This narrows the window to the replace itself; Studio takes no lock, so an edit in that last moment is lost
        (TD-011).
        """
        try:
            now = digest(read_plain_file(self.config_dir / name, private=private))
        except ConfigFileError:
            now = "unreadable"
        if now != expected:
            raise SaveError("studio.config_stale")

    def _write(self, name: str, data: bytes) -> None:
        try:
            write_atomic(self.config_dir / name, data)
        except OSError:
            raise SaveError("studio.config_unwritable") from None
