"""The user's own configuration: ``settings.toml`` and ``profiles.toml`` (03 section 4.3, STU-006).

Both live in the configuration folder (``platformdirs.user_config_dir("tabrion-studio")``, ``--config-dir`` or
``$TABRION_STUDIO_CONFIG_DIR``). They belong to the user, so they need no trust dialog, but ``profiles.toml`` must be
private (0600, owned by the user; POSIX) and neither may be a link. A file that cannot be used is reported as an
issue and ignored: Studio then runs with the default settings and without user profiles.

Studio writes both files itself (STU-008, :mod:`tabrion_studio.config.writer`): :meth:`UserConfig.save_profiles` and
:meth:`UserConfig.save_settings` check the folder (0700, no link), refuse a file that changed since the page read it
(``studio.config_stale``) or that has issues (``studio.config_invalid``), ask before they replace a file written by
hand (``studio.config_hand_written``, its comments are not kept) and write atomically with mode 0600.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tabrion_studio.config.files import ConfigFileError, parse_toml, read_plain_file
from tabrion_studio.config.schema import Issue, Profile, Settings, validate_profiles, validate_settings
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


class UserConfig:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir

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
            parsed = validate_profiles(parse_toml(data))
        except ConfigFileError as exc:
            pointer = PROFILES_FILE if exc.line is None else f"{PROFILES_FILE} line {exc.line}"
            return UserProfiles(issues=[Issue(pointer, exc.code)], digest=digest(data), canonical=False)
        # Valid profiles are used even if others in the file are not; the issues say which ones were skipped.
        return UserProfiles(
            parsed.profiles, parsed.issues, digest(data), canonical=dump_profiles(parsed.profiles) == data
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
        data = checked_profiles(change(list(current.profiles)))
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
