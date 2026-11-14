"""An app with the write foundation (STU-XW-1) for in-process tests: a user configuration, a connection registry, the
query engine and the write areas the test chooses (every area off unless a test turns one on, as only a "flag on"
test may)."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.secrets import KeyringStore, Secrets, SessionSecrets
from tabrion_studio.config.user import UserConfig
from tabrion_studio.config.write_areas import ALL_OFF, Area, WriteAreas
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.drafts import Drafts
from tabrion_studio.services.history import History
from tabrion_studio.services.queries import QueryEngine
from tabrion_studio.services.state import StateStore
from tests.support.access import ORIGIN, Studio, make_studio
from tests.support.fake_keyring import FakeKeyring

#: Marked values: none may ever appear in an answer or a log (TX-14, N-5).
CANARY_SECRET = f"write-secret-canary-{uuid.uuid4()}"
CANARY_VALUE = f"cell-value-canary-{uuid.uuid4()}"

DATA_EDITOR = WriteAreas(frozenset({Area.DATA_EDITOR}))
DATABASE_MANAGER = WriteAreas(frozenset({Area.DATABASE_MANAGER}))
EVERY_AREA = WriteAreas(frozenset(Area))


def make_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE parts (id INTEGER PRIMARY KEY, material TEXT)")
    connection.execute("INSERT INTO parts VALUES (1, 'Steel')")
    connection.commit()
    connection.close()
    path.chmod(0o600)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WriteEnv:
    def __init__(self, tmp_path: Path, static_dir: Path, areas: WriteAreas = ALL_OFF) -> None:
        self.tmp_path = tmp_path
        self.project = tmp_path / "project"
        (self.project / ".tabrion").mkdir(parents=True)
        self.state = tmp_path / "state"
        self.state.mkdir(mode=0o700)
        self.config_dir = tmp_path / "config"
        #: The audit log (STU-XW-2): created by the write package only while an area is on.
        self.audit_dir = self.state / "audit"
        self.fake = FakeKeyring()
        secrets = Secrets(KeyringStore(lambda: self.fake), SessionSecrets(), {})
        project = ProjectConfig(self.project, self.state, "k1", self.config_dir)
        self.config = Configuration(project, UserConfig(self.config_dir), secrets)
        self.registry = ConnectionRegistry(self.config)
        store = StateStore(self.state)
        self.history = History(store, "k1", self.config.limits)
        self.studio: Studio = make_studio(
            static_dir,
            config=self.config,
            connections=self.registry,
            queries=QueryEngine(self.registry, self.config.limits, history=self.history),
            history=None,
            drafts=Drafts(store, "k1", self.config.limits),
            write_areas=areas,
            audit_dir=self.audit_dir,
        )
        self.store = store
        self.headers = {**self.studio.sign_in(), "Origin": ORIGIN}
        self.db = tmp_path / "parts.db"
        make_database(self.db)

    def close(self) -> None:
        self.studio.client.close()
        self.registry.close_all()
        self.store.close()

    def send(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any:
        return self.studio.client.request(method, path, json=body, headers=self.headers if headers is None else headers)

    def user_digest(self) -> str | None:
        digest: str | None = self.send("GET", "/api/profiles").json()["user_file"]["digest"]
        return digest

    def create_read(self, name: str = "parts", environment: str = "dev", path: Path | None = None) -> None:
        profile = {"name": name, "engine": "sqlite", "path": str(path or self.db), "environment": environment}
        response = self.send("POST", "/api/profiles", {"profile": profile, "digest": self.user_digest()})
        assert response.status_code == 201, response.text

    def write_file(self, text: str) -> None:
        """Write ``profiles.toml`` by hand (0600), as a user may."""
        self.config_dir.mkdir(mode=0o700, exist_ok=True)
        file = self.config_dir / "profiles.toml"
        file.write_text(text, encoding="utf-8")
        file.chmod(0o600)

    def create_write(self, name: str = "parts-write", read: str = "parts", kind: str = "write", **extra: Any) -> Any:
        profile = {"name": name, "kind": kind, "read_profile": read, **extra}
        return self.send(
            "POST",
            "/api/write/profiles",
            {"profile": profile, "digest": self.user_digest(), "confirm_no_prod_credentials": True},
        )

    def open(self, profile_id: str = "user:parts", environment: str | None = None) -> str:
        body: dict[str, Any] = {"profile_id": profile_id}
        if environment is not None:
            body["confirmed_environment"] = environment
        response = self.send("POST", "/api/connections", body)
        assert response.status_code == 201, response.text
        connection_id: str = response.json()["connection_id"]
        return connection_id

    def unlock(self, connection_id: str, write_id: str = "write:parts-write", typed: str = "parts-write") -> Any:
        return self.send(
            "POST",
            "/api/write/unlocks",
            {"connection_id": connection_id, "write_profile_id": write_id, "typed_name": typed},
        )

    def confirm_pair(self, write_id: str = "write:parts-write") -> Any:
        """Preview and confirm the I-109 j pair of server and secret reference of a PostgreSQL write profile (D-15,
        STU-XW-4), as the dialog does: the typed text is the host the preview names."""
        preview = self.send("POST", "/api/write/secret-confirmations", {"write_profile_id": write_id})
        assert preview.status_code == 200, preview.text
        doc = preview.json()
        return self.send(
            "POST",
            "/api/write/secret-confirmations/confirm",
            {"confirmation_hash": doc["confirmation_hash"], "typed": doc["confirm_text"]},
        )

    @property
    def unlocks(self) -> Any:
        return self.studio.client.app.state.write_unlocks  # type: ignore[attr-defined]

    def session(self) -> str:
        """The session digest the access middleware gives the routes (as ``Unlocks`` sees it)."""
        cookie = next(iter(self.studio.client.cookies.values()))
        return hashlib.sha256(cookie.encode("utf-8")).hexdigest()
