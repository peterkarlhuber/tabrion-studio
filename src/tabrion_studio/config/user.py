"""The user's own configuration: ``settings.toml`` and ``profiles.toml`` (03 section 4.3, STU-006).

Both live in the configuration folder (``platformdirs.user_config_dir("tabrion-studio")``, ``--config-dir`` or
``$TABRION_STUDIO_CONFIG_DIR``). They belong to the user, so they need no trust dialog, but ``profiles.toml`` must be
private (0600, owned by the user; POSIX) and neither may be a link. A file that cannot be used is reported as an
issue and ignored: Studio then runs with the default settings and without user profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tabrion_studio.config.files import ConfigFileError, parse_toml, read_plain_file
from tabrion_studio.config.schema import Issue, Profile, Settings, validate_profiles, validate_settings

SETTINGS_FILE = "settings.toml"
PROFILES_FILE = "profiles.toml"


@dataclass
class UserProfiles:
    profiles: list[Profile] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


class UserConfig:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir

    def settings(self) -> tuple[Settings, list[Issue]]:
        try:
            data = read_plain_file(self.config_dir / SETTINGS_FILE)
            if data is None:
                return Settings(), []
            return validate_settings(parse_toml(data))
        except ConfigFileError as exc:
            pointer = SETTINGS_FILE if exc.line is None else f"{SETTINGS_FILE} line {exc.line}"
            return Settings(), [Issue(pointer, exc.code)]

    def profiles(self) -> UserProfiles:
        try:
            data = read_plain_file(self.config_dir / PROFILES_FILE, private=True)
            if data is None:
                return UserProfiles()
            parsed = validate_profiles(parse_toml(data))
        except ConfigFileError as exc:
            pointer = PROFILES_FILE if exc.line is None else f"{PROFILES_FILE} line {exc.line}"
            return UserProfiles(issues=[Issue(pointer, exc.code)])
        # Valid profiles are used even if others in the file are not; the issues say which ones were skipped.
        return UserProfiles(parsed.profiles, parsed.issues)
