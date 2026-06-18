"""Shared test fixtures: fake keyring backend + SCIM mock helpers."""

from __future__ import annotations

from typing import Any

import pytest


_FAKE_KEYRING: dict[tuple[str, str], str] = {}


@pytest.fixture(autouse=True)
def _reset_fake_keyring():
    _FAKE_KEYRING.clear()
    yield


def fake_keyring_get_password(service: str, username: str) -> str | None:
    return _FAKE_KEYRING.get((service, username))


def fake_keyring_set_password(service: str, username: str, password: str) -> None:
    _FAKE_KEYRING[(service, username)] = password


def fake_keyring_delete_password(service: str, username: str) -> None:
    _FAKE_KEYRING.pop((service, username), None)


@pytest.fixture
def scim_list_response() -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 1,
        "Resources": [],
    }


@pytest.fixture
def scim_user_response() -> dict[str, Any]:
    return {
        "schemas": [
            "urn:ietf:params:scim:schemas:core:2.0:User",
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            "urn:ietf:params:scim:schemas:sailpoint:1.0:User",
        ],
        "id": "test-user-id-1",
        "userName": "alice",
        "displayName": "Alice Smith",
        "active": True,
        "urn:ietf:params:scim:schemas:sailpoint:1.0:User": {
            "roles": [
                {"display": "ALL_ACTIVE_USERS", "value": "role-1"},
            ]
        },
    }


@pytest.fixture
def scim_workflow_response() -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"],
        "id": "wf-test-1",
        "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow": {
            "workflowName": "LCM Provisioning",
            "completionStatus": None,
        },
    }
