"""Tests for the auth module."""

from __future__ import annotations

import keyring
import pytest

from iiq_mcp.auth import AuthManager
from iiq_mcp.errors import CredentialsNotFound

from tests.conftest import (
    fake_keyring_get_password,
    fake_keyring_set_password,
    fake_keyring_delete_password,
)


@pytest.fixture(autouse=True)
def _patch_keyring(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", fake_keyring_get_password)
    monkeypatch.setattr(keyring, "set_password", fake_keyring_set_password)
    monkeypatch.setattr(keyring, "delete_password", fake_keyring_delete_password)


class TestAuthManager:
    def test_make_key(self):
        mgr = AuthManager("https://iiq.acme.com:8443", "alice")
        assert mgr._key == "alice@iiq.acme.com"

    def test_set_and_get_password(self):
        mgr = AuthManager("https://iiq.acme.com", "alice")
        mgr.set_password("s3cret!")
        assert mgr.get_password() == "s3cret!"

    def test_get_password_raises_when_missing(self):
        mgr = AuthManager("https://iiq.acme.com", "bob")
        with pytest.raises(CredentialsNotFound):
            mgr.get_password()

    def test_delete_removes_entry(self):
        mgr = AuthManager("https://iiq.acme.com", "alice")
        mgr.set_password("s3cret!")
        mgr.delete()
        with pytest.raises(CredentialsNotFound):
            mgr.get_password()

    def test_delete_idempotent(self):
        mgr = AuthManager("https://iiq.acme.com", "nobody")
        mgr.delete()

    def test_set_overwrites_existing(self):
        mgr = AuthManager("https://iiq.acme.com", "alice")
        mgr.set_password("first")
        mgr.set_password("second")
        assert mgr.get_password() == "second"

    def test_multiple_instances_independent(self):
        dev = AuthManager("https://dev.iiq.acme.com", "alice")
        prod = AuthManager("https://prod.iiq.acme.com", "alice")
        dev.set_password("dev-pass")
        prod.set_password("prod-pass")
        assert dev.get_password() == "dev-pass"
        assert prod.get_password() == "prod-pass"
