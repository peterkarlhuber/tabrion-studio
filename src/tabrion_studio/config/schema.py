"""TOML schema version 1 of the profile files and the settings file (03 section 4.3, STU-006).

Profile files (the project's ``.tabrion/studio.toml`` and the user's ``profiles.toml``)::

    version = 1
    [[profile]]
    name = "parts-dev"
    engine = "sqlite"            # sqlite | postgresql
    path = "data/parts.db"       # relative to the folder of the profile owner
    environment = "dev"          # dev | test | prod (required)

    [[profile]]
    name = "measurements-central"
    engine = "postgresql"
    host = "db.example.internal"
    port = 5432
    database = "measurements"
    user = "studio_ro"
    password = "keyring:"        # keyring:, env:NAME, file:PATH; never a plain password
    sslmode = "verify-full"      # the default for a host that is not loopback (T-10)
    environment = "prod"
    read_only_role = true
    statement_timeout_ms = 15000

Write and admin profiles (STU-XW-1, STU-ADR-0006, E-X8) exist **only in the user's** ``profiles.toml`` (review K-1
of PR #109), as ``[[write_profile]]`` tables coupled to one user read profile of the same file::

    [[write_profile]]
    name = "measurements-write"
    kind = "write"               # write | admin
    read_profile = "measurements-dev"   # a user profile of this file, never a project profile; never a PROD one
    user = "measurements_writer"        # PostgreSQL: its own role (required, not the read profile's)
    password = "keyring:"               # its own secret reference

A write profile has no target of its own: host, port, database or file, TLS mode and environment are the read
profile's, so it can never name another target or another environment than the profile it is coupled to (TX-04).
A project file (``validate_profiles(..., owner="project")``, the default) that has a ``[[write_profile]]`` table is
invalid, and in either file a ``[[profile]]`` with a key that would make it a write profile or couple it
(:data:`WRITE_KEYS`) is refused (``config.write_key_not_allowed``).

The validator never echoes a value it rejects: an issue names the key path (``profile[2].password``) and a code
(``config.plain_password``), so a plain password written into a file by mistake never reaches the page or a log.
Unknown keys are reported and ignored. The format is Studio-local until Tabrion's profile format v1 (TAB-CR-1,
change request 14) exists; it is meant to stay compatible with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import re
from typing import Any, Literal

from tabrion_studio.config.locations import network_or_device

Owner = Literal["user", "project"]

SCHEMA_VERSION = 1
ENGINES = ("sqlite", "postgresql")
ENVIRONMENTS = ("dev", "test", "prod")
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
MAX_PROFILES = 200
MAX_NAME = 200
MAX_TEXT = 1024

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
#: Refused in a SQLite ``path`` (``config.url_character``), as tabrion 0.2's ``tabrion.profilefile`` does
#: (``profilefile.value``, G-T2 B-1): in a SQLAlchemy URL "?" and "#" start the query and the fragment and "%" is
#: unquoted, so ``%2F%2Fhost/share/x.db`` would become a network path there (TD-013). This aligns only the SQLite
#: ``path``: tabrion's reader also refuses ``/?#%@`` in a PostgreSQL ``database``, which Studio still accepts (it
#: builds that URL with ``URL.create``, so the name is never read as a URL; TODO TD-013).
_URL_CHARACTERS = frozenset("?#%")

_COMMON = {"name", "engine", "environment", "password", "statement_timeout_ms", "read_only_role"}
_SQLITE = _COMMON | {"path"}
_POSTGRES = _COMMON | {"host", "port", "database", "user", "sslmode"}
_KNOWN = _SQLITE | _POSTGRES
#: Keys that would make a read profile a write or admin profile or couple it to one: refused in every ``[[profile]]``
#: of either file (K-1). Write and admin profiles are ``[[write_profile]]`` tables of the user's file only.
WRITE_KEYS = frozenset(
    {
        "mode",
        "kind",
        "couples_to",
        "coupled_to",
        "coupling",
        "read_profile",
        "write_profile",
        "admin_profile",
        "write_user",
        "write_password",
        "admin_user",
        "admin_password",
    }
)
WRITE_KINDS = ("write", "admin")
WRITE_PROFILE_KEYS = ("name", "kind", "read_profile", "user", "password")

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    """One finding of the validator: where (a key path, never a value) and what (a code)."""

    pointer: str
    code: str
    severity: Severity = "error"

    def to_dict(self) -> dict[str, str]:
        return {"pointer": self.pointer, "code": self.code, "severity": self.severity}


@dataclass(frozen=True)
class SecretRef:
    """A reference to a secret; the secret itself is resolved only when connecting (STU-007)."""

    source: Literal["keyring", "env", "file"]
    target: str  # "" for keyring, the variable name for env, the path as written for file

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "target": self.target}


@dataclass(frozen=True)
class Profile:
    name: str
    engine: str
    environment: str
    path: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: SecretRef | None = None
    sslmode: str | None = None
    read_only_role: bool | None = None
    statement_timeout_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"name": self.name, "engine": self.engine, "environment": self.environment}
        for key in ("path", "host", "port", "database", "user", "sslmode", "read_only_role", "statement_timeout_ms"):
            value = getattr(self, key)
            if value is not None:
                doc[key] = value
        doc["password"] = None if self.password is None else self.password.to_dict()
        return doc


#: The fields that say where and how a connection goes: a stored secret belongs to these (M-1 of stu-007-review.md;
#: ``sslmode``, the effective one after the default, from L-1 of stu-008-review.md: a downgrade from ``verify-full``
#: to ``disable`` must not keep the saved password). Binding for STU-009: every parameter Studio passes to the driver
#: must be in this list, and connections are built from :class:`Profile` only.
TARGET_FIELDS = ("engine", "host", "port", "database", "user", "path", "sslmode")


def target_fingerprint(doc: dict[str, Any]) -> str:
    """16 hex digits of the SHA-256 of a profile's target (``Profile.to_dict()`` form; port None means 5432).

    A secret stored for a profile is bound to this: when a project commit keeps the name ``prod`` but changes the host,
    the fingerprint changes and the stored password is not sent to the new host (C-1 of stu-007-review.md).
    """
    target: list[Any] = [doc.get(key) for key in TARGET_FIELDS]
    if doc.get("engine") == "postgresql" and target[2] is None:
        target[2] = 5432
    text = json.dumps(target, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


@dataclass(frozen=True)
class WriteProfile:
    """A write or admin profile of the user's own file (E-X8, K-1): its own role and secret, coupled to one user read
    profile whose target and environment it uses."""

    name: str
    kind: str  # "write" or "admin"
    read_profile: str  # the name of a user profile of the same file
    user: str | None = None
    password: SecretRef | None = None

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"name": self.name, "kind": self.kind, "read_profile": self.read_profile}
        if self.user is not None:
            doc["user"] = self.user
        doc["password"] = None if self.password is None else self.password.to_dict()
        return doc

    def connection_profile(self, read: Profile) -> Profile:
        """The profile a write connection uses: the read profile's target and environment, this role and secret."""
        return Profile(
            name=read.name,
            engine=read.engine,
            environment=read.environment,
            path=read.path,
            host=read.host,
            port=read.port,
            database=read.database,
            user=self.user if read.engine == "postgresql" else None,
            password=self.password if read.engine == "postgresql" else None,
            sslmode=read.sslmode,
            read_only_role=None,
            statement_timeout_ms=read.statement_timeout_ms,
        )


@dataclass
class ProfileFile:
    """The result of validating a profile file: the valid profiles, every issue and the unknown keys."""

    profiles: list[Profile] = field(default_factory=list)
    #: Only for the user's own file (K-1); always empty for a project file.
    write_profiles: list[WriteProfile] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    unknown_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


def _key(text: str) -> str:
    """A key as it appears in a pointer: plain if it is a simple name, else quoted with escapes."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text):
        return text
    return '"' + _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text[:64]).replace('"', '\\"') + '"'


def is_loopback(host: str) -> bool:
    """A Unix socket folder, the literal ``localhost`` or a loopback IP address; never a DNS name (review T-4).

    ``127.evil.example`` or ``127.0.0.1.nip.io`` are names that anyone can point anywhere, so they are remote and
    default to ``verify-full``.
    """
    if host.startswith("/"):
        return True
    name = host.lower()
    if name == "localhost":
        return True
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_secret_ref(value: str) -> SecretRef | None:
    """``keyring:``, ``env:NAME`` or ``file:PATH``; None for anything else (a plain password is never accepted)."""
    if value == "keyring:":
        return SecretRef("keyring", "")
    if value.startswith("env:") and _ENV_NAME.fullmatch(value[4:]):
        return SecretRef("env", value[4:])
    if (
        value.startswith("file:")
        and 0 < len(value) - 5 <= MAX_TEXT
        and not _CONTROL.search(value)
        and _encodable(value)
    ):
        return SecretRef("file", value[5:])
    return None


def _text(raw: dict[str, Any], key: str, where: str, issues: list[Issue], *, limit: int = MAX_TEXT) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        issues.append(Issue(f"{where}.{key}", "config.type"))
        return None
    if not value or len(value) > limit:
        issues.append(Issue(f"{where}.{key}", "config.length"))
        return None
    if not _encodable(value):
        # A lone surrogate is valid JSON but not UTF-8: Studio could not write it (L-2 of stu-008-review.md).
        issues.append(Issue(f"{where}.{key}", "config.encoding"))
        return None
    return value


def _choice(raw: dict[str, Any], key: str, where: str, options: tuple[str, ...], issues: list[Issue]) -> str | None:
    value = _text(raw, key, where, issues)
    if value is not None and value not in options:
        issues.append(Issue(f"{where}.{key}", "config.choice"))
        return None
    return value


def _integer(raw: dict[str, Any], key: str, where: str, low: int, high: int, issues: list[Issue]) -> int | None:
    if key not in raw:
        return None
    value = raw[key]
    if type(value) is not int:
        issues.append(Issue(f"{where}.{key}", "config.type"))
        return None
    if not low <= value <= high:
        issues.append(Issue(f"{where}.{key}", "config.range"))
        return None
    return value


def _profile(raw: Any, where: str, issues: list[Issue], unknown: list[str]) -> Profile | None:
    if not isinstance(raw, dict):
        issues.append(Issue(where, "config.type"))
        return None
    start = len(issues)
    name = _text(raw, "name", where, issues, limit=MAX_NAME)
    if "name" not in raw:
        issues.append(Issue(f"{where}.name", "config.required"))
    elif name is not None and _CONTROL.search(name):
        issues.append(Issue(f"{where}.name", "config.control_character"))
        name = None
    engine = _choice(raw, "engine", where, ENGINES, issues)
    if "engine" not in raw:
        issues.append(Issue(f"{where}.engine", "config.required"))
    environment = _choice(raw, "environment", where, ENVIRONMENTS, issues)
    if "environment" not in raw:
        issues.append(Issue(f"{where}.environment", "config.required"))
    for key in raw:
        if key in WRITE_KEYS:
            # K-1: a read profile never becomes a write profile or a coupling, in a project file or the user's.
            issues.append(Issue(f"{where}.{key}", "config.write_key_not_allowed"))
        elif key not in _KNOWN:
            unknown.append(f"{where}.{_key(str(key))}")
        elif (engine == "sqlite" and key not in _SQLITE) or (engine == "postgresql" and key not in _POSTGRES):
            issues.append(Issue(f"{where}.{key}", "config.not_for_engine"))
    password: SecretRef | None = None
    if "password" in raw:
        value = raw["password"]
        password = parse_secret_ref(value) if isinstance(value, str) else None
        if password is None:
            # Never the value: it may be a real password written into the file by mistake.
            issues.append(Issue(f"{where}.password", "config.plain_password"))
        elif password.source == "file" and network_or_device(password.target):
            # T-2: another host, a device or a drive-relative path; refused as text, before any file-system access.
            issues.append(Issue(f"{where}.password", "config.network_path"))
    timeout = _integer(raw, "statement_timeout_ms", where, 1, 24 * 3600 * 1000, issues)
    read_only_role = raw.get("read_only_role")
    if read_only_role is not None and not isinstance(read_only_role, bool):
        issues.append(Issue(f"{where}.read_only_role", "config.type"))
        read_only_role = None
    path = host = database = user = sslmode = None
    port: int | None = None
    if engine == "sqlite":
        path = _text(raw, "path", where, issues)
        if "path" not in raw:
            issues.append(Issue(f"{where}.path", "config.required"))
        elif path is not None and _CONTROL.search(path):
            issues.append(Issue(f"{where}.path", "config.control_character"))
        elif path is not None and network_or_device(path):
            issues.append(Issue(f"{where}.path", "config.network_path"))
        elif path is not None and not _URL_CHARACTERS.isdisjoint(path):
            issues.append(Issue(f"{where}.path", "config.url_character"))
    elif engine == "postgresql":
        host = _text(raw, "host", where, issues, limit=255)
        if "host" not in raw:
            issues.append(Issue(f"{where}.host", "config.required"))
        elif host is not None and (_CONTROL.search(host) or any(c.isspace() for c in host)):
            issues.append(Issue(f"{where}.host", "config.control_character"))
        port = _integer(raw, "port", where, 1, 65535, issues)
        database = _text(raw, "database", where, issues, limit=255)
        if "database" not in raw:
            issues.append(Issue(f"{where}.database", "config.required"))
        user = _text(raw, "user", where, issues, limit=255)
        sslmode = _choice(raw, "sslmode", where, SSL_MODES, issues)
        if sslmode is None and "sslmode" not in raw and host is not None:
            # 03 T-10: new remote profiles default to verify-full, stricter than the library's "auto".
            sslmode = "prefer" if is_loopback(host) else "verify-full"
    if len(issues) > start or name is None or engine is None or environment is None:
        return None
    return Profile(
        name=name,
        engine=engine,
        environment=environment,
        path=path,
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        sslmode=sslmode,
        read_only_role=read_only_role,
        statement_timeout_ms=timeout,
    )


def validate_profiles(doc: dict[str, Any], *, owner: Owner = "project") -> ProfileFile:
    """Validate a parsed profile file of schema version 1.

    ``owner`` is ``"user"`` only for the user's own ``profiles.toml``: only there may ``[[write_profile]]`` tables
    appear (K-1). The default is the stricter ``"project"``.
    """
    result = ProfileFile()
    issues, unknown = result.issues, result.unknown_keys
    version = doc.get("version")
    if version is None:
        issues.append(Issue("version", "config.required"))
        return result
    if type(version) is not int or version != SCHEMA_VERSION:
        issues.append(Issue("version", "config.unsupported_version"))
        return result
    for key in doc:
        if key == "write_profile":
            if owner != "user":
                # K-1: a project file never defines a write or admin profile; the whole file is refused.
                issues.append(Issue("write_profile", "config.write_profile_in_project"))
        elif key not in ("version", "profile"):
            unknown.append(_key(str(key)))
    raw_profiles = doc.get("profile", [])
    if not isinstance(raw_profiles, list):
        issues.append(Issue("profile", "config.type"))
        return result
    if len(raw_profiles) > MAX_PROFILES:
        issues.append(Issue("profile", "config.too_many"))
        return result
    seen: set[str] = set()
    for index, raw in enumerate(raw_profiles):
        profile = _profile(raw, f"profile[{index}]", issues, unknown)
        if profile is None:
            continue
        if profile.name in seen:
            issues.append(Issue(f"profile[{index}].name", "config.duplicate"))
            continue
        seen.add(profile.name)
        result.profiles.append(profile)
    if owner == "user" and "write_profile" in doc:
        _write_profiles(doc["write_profile"], result, seen)
    for pointer in unknown:
        issues.append(Issue(pointer, "config.unknown_key", "warning"))
    return result


def _write_profile(raw: Any, where: str, issues: list[Issue]) -> WriteProfile | None:
    if not isinstance(raw, dict):
        issues.append(Issue(where, "config.type"))
        return None
    start = len(issues)
    for key in raw:
        if key not in WRITE_PROFILE_KEYS:
            # Stricter than a read profile: a write profile has no target of its own and no other key (TX-04).
            issues.append(Issue(f"{where}.{_key(str(key))}", "config.unknown_key"))
    name = _text(raw, "name", where, issues, limit=MAX_NAME)
    if "name" not in raw:
        issues.append(Issue(f"{where}.name", "config.required"))
    elif name is not None and _CONTROL.search(name):
        issues.append(Issue(f"{where}.name", "config.control_character"))
    kind = _choice(raw, "kind", where, WRITE_KINDS, issues)
    if "kind" not in raw:
        issues.append(Issue(f"{where}.kind", "config.required"))
    read_profile = _text(raw, "read_profile", where, issues, limit=MAX_NAME)
    if "read_profile" not in raw:
        issues.append(Issue(f"{where}.read_profile", "config.required"))
    user = _text(raw, "user", where, issues, limit=255)
    password: SecretRef | None = None
    if "password" in raw:
        value = raw["password"]
        password = parse_secret_ref(value) if isinstance(value, str) else None
        if password is None:
            issues.append(Issue(f"{where}.password", "config.plain_password"))
        elif password.source == "file" and network_or_device(password.target):
            issues.append(Issue(f"{where}.password", "config.network_path"))
    if len(issues) > start or name is None or kind is None or read_profile is None:
        return None
    return WriteProfile(name=name, kind=kind, read_profile=read_profile, user=user, password=password)


def parse_write_profile(raw: Any) -> tuple[WriteProfile | None, list[Issue]]:
    """One write or admin profile as a form sends it; its issues point at ``write_profile[0].<key>``."""
    issues: list[Issue] = []
    return _write_profile(raw, "write_profile[0]", issues), issues


def coupling_issue(write: WriteProfile, read: Profile | None) -> str | None:
    """Why ``write`` cannot be coupled to ``read`` (a ``config.*`` code), or None (K-1, E-X7, E-X8, TX-04)."""
    if read is None:
        return "config.coupling_unknown"  # only a user profile of the same file; never a project profile
    if read.environment == "prod":
        return "config.coupling_prod"  # E-X7: a PROD profile never has a write or admin profile
    if read.engine == "sqlite" and (write.user is not None or write.password is not None):
        return "config.not_for_engine"
    if read.engine == "postgresql":
        if write.user is None:
            return "config.coupling_role_required"
        if write.user == read.user:
            return "config.coupling_same_role"  # E-X8: its own database role
    return None


def _coupling_pointer(code: str) -> str:
    return "user" if code in ("config.coupling_role_required", "config.coupling_same_role") else "read_profile"


def _write_profiles(raw_list: Any, result: ProfileFile, read_names: set[str]) -> None:
    issues = result.issues
    if not isinstance(raw_list, list):
        issues.append(Issue("write_profile", "config.type"))
        return
    if len(raw_list) + len(result.profiles) > MAX_PROFILES:
        issues.append(Issue("write_profile", "config.too_many"))
        return
    reads = {profile.name: profile for profile in result.profiles}
    seen: set[str] = set()
    for index, raw in enumerate(raw_list):
        where = f"write_profile[{index}]"
        write = _write_profile(raw, where, issues)
        if write is None:
            continue
        if write.name in seen or write.name in read_names:
            issues.append(Issue(f"{where}.name", "config.duplicate"))
            continue
        problem = coupling_issue(write, reads.get(write.read_profile))
        if problem is not None:
            issues.append(Issue(f"{where}.{_coupling_pointer(problem)}", problem))
            continue
        seen.add(write.name)
        result.write_profiles.append(write)


# --- settings.toml ---------------------------------------------------------------------------------------------

LANGUAGES = ("system", "en", "de")
THEMES = ("system", "light", "dark")
DENSITIES = ("compact", "normal")
#: The query history (STU-020): kept this many days by default, at most ten years.
HISTORY_RETENTION_DAYS = 30
MAX_HISTORY_RETENTION_DAYS = 3650


@dataclass(frozen=True)
class Settings:
    """The user's settings with their defaults. Telemetry does not exist: it is always off (03 section 4.3)."""

    language: str = "system"
    theme: str = "system"
    density: str = "compact"
    log_sql: bool = False
    max_rows: int = 10_000
    statement_timeout_ms: int = 30_000
    #: 03 section 5.3: bytes per result in the backend, 20 MB by default, configurable up to 200 MB.
    max_result_mb: int = 20
    #: The query history (03 section 2 C, STU-020): local, per profile, never with parameter values; can be turned off.
    history_enabled: bool = True
    history_retention_days: int = HISTORY_RETENTION_DAYS

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "theme": self.theme,
            "density": self.density,
            "log_sql": self.log_sql,
            "telemetry": False,
            "limits": {
                "max_rows": self.max_rows,
                "statement_timeout_ms": self.statement_timeout_ms,
                "max_result_mb": self.max_result_mb,
            },
            "history": {"enabled": self.history_enabled, "retention_days": self.history_retention_days},
        }


def validate_settings(doc: dict[str, Any]) -> tuple[Settings, list[Issue]]:
    """The settings of a parsed ``settings.toml``; a key with an invalid value keeps its default."""
    issues: list[Issue] = []
    version = doc.get("version", SCHEMA_VERSION)
    if type(version) is not int or version != SCHEMA_VERSION:
        return Settings(), [Issue("version", "config.unsupported_version")]
    values: dict[str, Any] = {}
    for key, choices in (("language", LANGUAGES), ("theme", THEMES), ("density", DENSITIES)):
        value = _choice(doc, key, "settings", choices, issues)
        if value is not None:
            values[key] = value
    if "log_sql" in doc:
        if isinstance(doc["log_sql"], bool):
            values["log_sql"] = doc["log_sql"]
        else:
            issues.append(Issue("settings.log_sql", "config.type"))
    if doc.get("telemetry") not in (None, False):
        issues.append(Issue("settings.telemetry", "config.telemetry_unsupported"))
    limits = doc.get("limits", {})
    if isinstance(limits, dict):
        rows = _integer(limits, "max_rows", "settings.limits", 1, 1_000_000, issues)
        timeout = _integer(limits, "statement_timeout_ms", "settings.limits", 1, 24 * 3600 * 1000, issues)
        if rows is not None:
            values["max_rows"] = rows
        result_mb = _integer(limits, "max_result_mb", "settings.limits", 1, 200, issues)
        if timeout is not None:
            values["statement_timeout_ms"] = timeout
        if result_mb is not None:
            values["max_result_mb"] = result_mb
        for key in limits:
            if key not in ("max_rows", "statement_timeout_ms", "max_result_mb"):
                issues.append(Issue(f"settings.limits.{_key(str(key))}", "config.unknown_key", "warning"))
    else:
        issues.append(Issue("settings.limits", "config.type"))
    _history_settings(doc, values, issues)
    for key in doc:
        if key not in ("version", "language", "theme", "density", "log_sql", "telemetry", "limits", "history"):
            issues.append(Issue(f"settings.{_key(str(key))}", "config.unknown_key", "warning"))
    return Settings(**values), issues


def _history_settings(doc: dict[str, Any], values: dict[str, Any], issues: list[Issue]) -> None:
    """``[history]``: ``enabled`` (bool) and ``retention_days`` (1 to 3650)."""
    history = doc.get("history", {})
    if not isinstance(history, dict):
        issues.append(Issue("settings.history", "config.type"))
        return
    if "enabled" in history:
        if isinstance(history["enabled"], bool):
            values["history_enabled"] = history["enabled"]
        else:
            issues.append(Issue("settings.history.enabled", "config.type"))
    days = _integer(history, "retention_days", "settings.history", 1, MAX_HISTORY_RETENTION_DAYS, issues)
    if days is not None:
        values["history_retention_days"] = days
    for key in history:
        if key not in ("enabled", "retention_days"):
            issues.append(Issue(f"settings.history.{_key(str(key))}", "config.unknown_key", "warning"))
