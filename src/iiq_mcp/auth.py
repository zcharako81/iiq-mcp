"""OS keychain credential management for IIQ credentials."""

from __future__ import annotations

import keyring
from urllib.parse import urlparse

from iiq_mcp.errors import CredentialsNotFound

SERVICE_NAME = "iiq-mcp"


class AuthManager:
    """Read/write/delete IIQ credentials from the OS keychain."""

    def __init__(self, base_url: str, username: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._key = self._make_key(base_url, username)

    @staticmethod
    def _make_key(base_url: str, username: str) -> str:
        host = urlparse(base_url).hostname
        return f"{username}@{host}"

    def get_password(self) -> str:
        pwd = keyring.get_password(SERVICE_NAME, self._key)
        if pwd is None:
            raise CredentialsNotFound(
                f"No credentials for {self._key}. Run `iiq-mcp login` first."
            )
        return pwd

    def set_password(self, password: str) -> None:
        keyring.set_password(SERVICE_NAME, self._key, password)

    def delete(self) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, self._key)
        except keyring.errors.PasswordDeleteError:
            pass
