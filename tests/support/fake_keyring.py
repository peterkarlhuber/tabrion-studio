"""An in-memory keyring backend for the STU-007 tests (no test ever touches the real OS keyring)."""

from __future__ import annotations

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class FakeKeyring(KeyringBackend):
    """Keeps secrets in a dict; ``fail`` makes every call raise, like a locked or broken keyring."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.values: dict[tuple[str, str], str] = {}
        self.reads = 0
        self.fail = False

    def get_password(self, service: str, username: str) -> str | None:
        self.reads += 1
        if self.fail:
            raise RuntimeError("keyring locked: secret-in-message")
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("keyring locked")
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.values:
            raise PasswordDeleteError("none")
        del self.values[(service, username)]
