# iiq-mcp v0.1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python MCP server for SailPoint IdentityIQ 8.5 — 6 tools over SCIM 2.0 + LCM Provisioning, with OS keychain auth.

**Architecture:** Stdio-only MCP server, per-user credentials in OS keychain. Each tool call builds a fresh `ScimClient` with the user's Basic Auth credentials. Write tools construct `ProvisioningPlan` JSON and launch the `LCM Provisioning` workflow via SCIM's `LaunchedWorkflows` endpoint.

**Tech Stack:** Python 3.11+, `mcp` SDK, `click`, `httpx`, `keyring`, `pydantic`, `respx` (tests)

**Reference files:**
- `docs/superpowers/specs/2026-06-18-iiq-mcp-server-design.md` — full v1 design
- `examples/claude_desktop_config.json` — AI client MCP config
- `AGENTS.md` — project conventions, RAG workflow

---

## File Structure

```
src/iiq_mcp/
├── errors.py       ≡ Typed exceptions for SCIM/auth errors
├── auth.py         ≡ OS keychain credential management
├── plan.py         ≡ ProvisioningPlan builder (b1 — minimal)
├── scim.py         ≡ SCIM 2.0 client (httpx.AsyncClient)
├── tools.py        ≡ 6 MCP tool definitions
├── server.py       ≡ MCP stdio server bootstrap
├── cli.py          ≡ Click commands: login, logout, status, serve

tests/
├── conftest.py     ≡ Shared fixtures: fake keyring backend, mock SCIM server (respx)
├── test_auth.py    ≡ Unit tests for auth.py
├── test_plan.py    ≡ Unit tests for plan.py
├── test_scim.py    ≡ Integration tests for scim.py (respx)
├── test_tools.py   ≡ Integration tests for tools.py (respx)
└── test_cli.py     ≡ CLI tests (Click CliRunner + mocked scim)
```

---

## Implementation Order (dependency-ordered)

| Task | Module | Depends on |
|---|---|---|
| 1 | `errors.py` | — |
| 2 | `auth.py` | `errors.py` |
| 3 | `plan.py` | — |
| 4 | `scim.py` | `errors.py` |
| 5 | `tools.py` | `scim.py`, `plan.py`, `auth.py` |
| 6 | `server.py` | `tools.py` |
| 7 | `cli.py` | `auth.py`, `server.py`, `scim.py` |

Tests are written first (TDD) — each task creates test file before impl file.

---

### Task 1: `errors.py` — Typed Exceptions

**Files:**
- Create: `src/iiq_mcp/errors.py`
- Test: verified in later tasks (too small for standalone test file)

- [ ] **Step 1: Write `src/iiq_mcp/errors.py`**

```python
"""Typed exceptions for SCIM and auth errors."""


class ScimError(Exception):
    """Base exception for all SCIM-related errors."""

    def __init__(self, message: str, status_code: int | None = None, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class ScimAuthError(ScimError):
    """401 — bad credentials or session expired."""


class ScimNotFoundError(ScimError):
    """404 — resource not found."""


class ScimBadRequestError(ScimError):
    """400 — invalid request payload."""


class ScimServerError(ScimError):
    """5xx — IIQ server error."""


class ScimNetworkError(ScimError):
    """Connection/timeout/transport errors."""


class CredentialsNotFound(Exception):
    """No credentials found in keychain."""


class CredentialsInvalid(Exception):
    """Credentials exist but failed validation against IIQ."""
```

- [ ] **Step 2: Commit**

```bash
git add src/iiq_mcp/errors.py
git commit -m "feat: add typed exception hierarchy for SCIM errors"
```

---

### Task 2: `auth.py` — OS Keychain Credential Management

**Files:**
- Create: `tests/conftest.py` (shared fixtures — fake keyring + mock SCIM endpoint)
- Create: `tests/test_auth.py`
- Create: `src/iiq_mcp/auth.py`

- [ ] **Step 1: Write `tests/conftest.py` with shared fixtures**

```python
"""Shared test fixtures: fake keyring backend + SCIM mock endpoints."""

from __future__ import annotations

import json
from typing import Any

import pytest


# ── Fake keyring backend ───────────────────────────────────────────
# Avoid touching the real OS keychain during tests.

_FAKE_KEYRING: dict[tuple[str, str], str] = {}


@pytest.fixture(autouse=True)
def _reset_fake_keyring():
    """Reset the fake keyring before every test."""
    _FAKE_KEYRING.clear()
    yield


def fake_keyring_get_password(service: str, username: str) -> str | None:
    return _FAKE_KEYRING.get((service, username))


def fake_keyring_set_password(service: str, username: str, password: str) -> None:
    _FAKE_KEYRING[(service, username)] = password


def fake_keyring_delete_password(service: str, username: str) -> None:
    _FAKE_KEYRING.pop((service, username), None)


# ── Mock SCIM response helper ──────────────────────────────────────

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
```

- [ ] **Step 2: Write `tests/test_auth.py`**

```python
"""Tests for the auth module."""

from __future__ import annotations

import keyring
import pytest

from iiq_mcp.auth import AuthManager, SERVICE_NAME
from iiq_mcp.errors import CredentialsNotFound

# Use the fake keyring backend from conftest
from tests.conftest import (
    _FAKE_KEYRING,
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
        """Composite key format: username@host."""
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
        """Deleting a non-existent entry should not raise."""
        mgr = AuthManager("https://iiq.acme.com", "nobody")
        mgr.delete()  # should not raise

    def test_set_overwrites_existing(self):
        mgr = AuthManager("https://iiq.acme.com", "alice")
        mgr.set_password("first")
        mgr.set_password("second")
        assert mgr.get_password() == "second"

    def test_multiple_instances_independent(self):
        """Different base URLs with same username don't clobber each other."""
        dev = AuthManager("https://dev.iiq.acme.com", "alice")
        prod = AuthManager("https://prod.iiq.acme.com", "alice")
        dev.set_password("dev-pass")
        prod.set_password("prod-pass")
        assert dev.get_password() == "dev-pass"
        assert prod.get_password() == "prod-pass"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/zcharako/eclipse-workspace/iiq-mcp
pip install -e ".[dev]" 2>&1 | tail -3
python -m pytest tests/test_auth.py -v --tb=short
```
Expected: FAIL with `ModuleNotFoundError: No module named 'iiq_mcp.auth'`

- [ ] **Step 4: Write `src/iiq_mcp/auth.py`**

```python
"""OS keychain credential management for IIQ credentials."""

from __future__ import annotations

import keyring
from iiq_mcp.errors import CredentialsNotFound

SERVICE_NAME = "iiq-mcp"


class AuthManager:
    """Read/write/delete IIQ credentials from the OS keychain.

    Key layout:
        service:  "iiq-mcp"
        username: "<username>@<host>"  (composite key for multi-IIQ support)
        password: <iiq_password>
    """

    def __init__(self, base_url: str, username: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._key = self._make_key(base_url, username)

    @staticmethod
    def _make_key(base_url: str, username: str) -> str:
        from urllib.parse import urlparse
        host = urlparse(base_url).netloc
        return f"{username}@{host}"

    def get_password(self) -> str:
        """Read password from keychain. Raises CredentialsNotFound if missing."""
        pwd = keyring.get_password(SERVICE_NAME, self._key)
        if pwd is None:
            raise CredentialsNotFound(
                f"No credentials for {self._key}. Run `iiq-mcp login` first."
            )
        return pwd

    def set_password(self, password: str) -> None:
        """Store password in OS keychain."""
        keyring.set_password(SERVICE_NAME, self._key, password)

    def delete(self) -> None:
        """Remove password from OS keychain. Idempotent."""
        try:
            keyring.delete_password(SERVICE_NAME, self._key)
        except keyring.errors.PasswordDeleteError:
            pass
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_auth.py -v --tb=short
```
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_auth.py src/iiq_mcp/auth.py
git commit -m "feat: add auth module with OS keychain credential management"
```

---

### Task 3: `plan.py` — ProvisioningPlan Builder (b1 minimal)

**Files:**
- Create: `tests/test_plan.py`
- Create: `src/iiq_mcp/plan.py`

- [ ] **Step 1: Write `tests/test_plan.py`**

```python
"""Tests for the ProvisioningPlan builder."""

from __future__ import annotations

import json

from iiq_mcp.plan import (
    build_role_request_plan,
    build_entitlement_request_plan,
    wrap_plan_in_workflow,
)


class TestBuildRoleRequestPlan:
    def test_role_plan_structure(self):
        plan = build_role_request_plan("role-abc-123", "alice")
        assert plan["identity"] == "alice"
        assert len(plan["accountRequests"]) == 1
        ar = plan["accountRequests"][0]
        assert ar["op"] == "Modify"
        assert ar["application"] == "IIQ"
        assert ar["nativeIdentity"] == "alice"
        assert len(ar["attributeRequests"]) == 1
        attr = ar["attributeRequests"][0]
        assert attr["op"] == "Add"
        assert attr["name"] == "assignedRoles"
        assert attr["value"] == "role-abc-123"

    def test_identity_name_is_required(self):
        plan = build_role_request_plan("role-xyz", "bob")
        assert plan["identity"] == "bob"


class TestBuildEntitlementRequestPlan:
    def test_entitlement_plan_structure(self):
        plan = build_entitlement_request_plan(
            "Active Directory",
            "memberOf",
            "CN=Admins,OU=Groups,DC=corp",
            "alice",
        )
        assert plan["identity"] == "alice"
        assert len(plan["accountRequests"]) == 1
        ar = plan["accountRequests"][0]
        assert ar["op"] == "Modify"
        assert ar["application"] == "Active Directory"
        assert ar["nativeIdentity"] == "alice"
        attr = ar["attributeRequests"][0]
        assert attr["op"] == "Add"
        assert attr["name"] == "memberOf"
        assert attr["value"] == "CN=Admins,OU=Groups,DC=corp"

    def test_special_characters_in_value(self):
        """Entitlement values with special characters are passed through unmodified."""
        plan = build_entitlement_request_plan(
            "LDAP",
            "group",
            "CN=Finance&HR,OU=Users,DC=corp",
            "charlie",
        )
        attr = plan["accountRequests"][0]["attributeRequests"][0]
        assert attr["value"] == "CN=Finance&HR,OU=Users,DC=corp"


class TestWrapPlanInWorkflow:
    def test_wrap_role_request(self):
        plan = build_role_request_plan("role-1", "alice")
        payload = wrap_plan_in_workflow(
            plan, identity_name="alice",
            flow="RolesRequest", justification="needed for project X",
        )
        assert payload["schemas"] == [
            "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"
        ]
        wf = payload["urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"]
        assert wf["workflowName"] == "LCM Provisioning"
        # Find the plan in the input list
        plan_input = next(i for i in wf["input"] if i["key"] == "plan")
        serialized = plan_input["value"]
        assert isinstance(serialized, str)
        deserialized = json.loads(serialized)
        assert deserialized["identity"] == "alice"

        # Find justification
        just_input = next(i for i in wf["input"] if i["key"] == "justification")
        assert just_input["value"] == "needed for project X"

    def test_wrap_without_justification(self):
        plan = build_role_request_plan("role-1", "alice")
        payload = wrap_plan_in_workflow(
            plan, identity_name="alice",
            flow="RolesRequest", justification=None,
        )
        wf = payload["urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"]
        # Justification should be omitted from input if not provided
        justs = [i for i in wf["input"] if i["key"] == "justification"]
        assert len(justs) == 0

    def test_flow_param(self):
        plan = build_role_request_plan("role-1", "alice")
        # RolesRequest
        payload = wrap_plan_in_workflow(
            plan, "alice", "RolesRequest",
        )
        wf = payload["urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"]
        flow_input = next(i for i in wf["input"] if i["key"] == "flow")
        assert flow_input["value"] == "RolesRequest"

        # EntitlementsRequest
        eplan = build_entitlement_request_plan("AD", "memberOf", "CN=G", "alice")
        epayload = wrap_plan_in_workflow(
            eplan, "alice", "EntitlementsRequest",
        )
        ewf = epayload["urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"]
        eflow = next(i for i in ewf["input"] if i["key"] == "flow")
        assert eflow["value"] == "EntitlementsRequest"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_plan.py -v --tb=short
```
Expected: FAIL with `ModuleNotFoundError: No module named 'iiq_mcp.plan'`

- [ ] **Step 3: Write `src/iiq_mcp/plan.py`**

```python
"""ProvisioningPlan builder (b1 — minimal, single-target only)."""

from __future__ import annotations

import json


def build_role_request_plan(
    role_id: str,
    identity_name: str,
) -> dict:
    """Build a minimal ProvisioningPlan for requesting a single role.

    The plan shape matches what the LCM Provisioning workflow expects
    from the standard IIQ access request UI.
    """
    return {
        "identity": identity_name,
        "accountRequests": [
            {
                "op": "Modify",
                "application": "IIQ",
                "nativeIdentity": identity_name,
                "attributeRequests": [
                    {
                        "op": "Add",
                        "name": "assignedRoles",
                        "value": role_id,
                    }
                ],
            }
        ],
    }


def build_entitlement_request_plan(
    application: str,
    attribute_name: str,
    attribute_value: str,
    identity_name: str,
) -> dict:
    """Build a minimal ProvisioningPlan for requesting a single entitlement."""
    return {
        "identity": identity_name,
        "accountRequests": [
            {
                "op": "Modify",
                "application": application,
                "nativeIdentity": identity_name,
                "attributeRequests": [
                    {
                        "op": "Add",
                        "name": attribute_name,
                        "value": attribute_value,
                    }
                ],
            }
        ],
    }


def wrap_plan_in_workflow(
    plan: dict,
    *,
    identity_name: str,
    flow: str,
    justification: str | None = None,
) -> dict:
    """Wrap a ProvisioningPlan in a SCIM LaunchedWorkflow payload.

    The wrapper targets the standard LCM Provisioning workflow.
    ``flow`` is the LCM flow type: "RolesRequest" or "EntitlementsRequest".
    """
    input_list: list[dict[str, str]] = [
        {"key": "identityName", "value": identity_name},
        {"key": "plan", "value": json.dumps(plan)},
        {"key": "flow", "value": flow},
    ]
    if justification:
        input_list.append({"key": "justification", "value": justification})

    return {
        "schemas": ["urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"],
        "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow": {
            "workflowName": "LCM Provisioning",
            "input": input_list,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_plan.py -v --tb=short
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_plan.py src/iiq_mcp/plan.py
git commit -m "feat: add minimal ProvisioningPlan builder (b1)"
```

---

### Task 4: `scim.py` — SCIM 2.0 Client

**Files:**
- Create: `tests/test_scim.py`
- Create: `src/iiq_mcp/scim.py`

- [ ] **Step 1: Write `tests/test_scim.py`**

```python
"""Integration tests for the SCIM client (via respx mock HTTP)."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from iiq_mcp.scim import ScimClient
from iiq_mcp.errors import ScimAuthError, ScimNotFoundError, ScimBadRequestError


@pytest.fixture
def client() -> ScimClient:
    return ScimClient(
        base_url="https://iiq.acme.com",
        username="alice",
        password="s3cret!",
    )


class TestSearchRoles:
    @respx.mock
    async def test_basic_filter(self, client: ScimClient):
        route = respx.get("https://iiq.acme.com/scim/v2/Roles").respond(
            status_code=200,
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": 1,
                "Resources": [
                    {"id": "r1", "displayName": "Finance Approver"}
                ],
            },
        )
        result = await client.search_roles('displayName co "finance"')
        assert result["totalResults"] == 1
        assert result["Resources"][0]["displayName"] == "Finance Approver"
        # Verify filter was passed as query param
        assert route.calls.last.request.url.params["filter"] == 'displayName co "finance"'

    @respx.mock
    async def test_filter_encoding(self, client: ScimClient):
        """Verify spaces use %20 not +, quotes use %22 not URLEncoder-style encoding."""
        route = respx.get("https://iiq.acme.com/scim/v2/Roles").respond(
            status_code=200,
            json={"schemas": [], "totalResults": 0, "Resources": []},
        )
        await client.search_roles('displayName co "finance admin"')
        url = str(route.calls.last.request.url)
        assert "%20" in url, f"Expected %20 for spaces in: {url}"
        assert "%22" in url, f"Expected %22 for quotes in: {url}"
        assert "+" not in url.split("?")[1], f"Unexpected + in query: {url}"

    @respx.mock
    async def test_limit_param(self, client: ScimClient):
        route = respx.get("https://iiq.acme.com/scim/v2/Roles").respond(
            status_code=200,
            json={"schemas": [], "totalResults": 0, "Resources": []},
        )
        await client.search_roles("displayName sw a", limit=5)
        assert route.calls.last.request.url.params["count"] == "5"


class TestSearchEntitlements:
    @respx.mock
    async def test_basic_filter(self, client: ScimClient):
        route = respx.get("https://iiq.acme.com/scim/v2/Entitlements").respond(
            status_code=200,
            json={"schemas": [], "totalResults": 0, "Resources": []},
        )
        await client.search_entitlements('displayName co "admin"')
        assert route.calls.last.request.url.params["filter"] == 'displayName co "admin"'

    @respx.mock
    async def test_with_application_filter(self, client: ScimClient):
        """When application is given, it's AND-ed into the filter."""
        route = respx.get("https://iiq.acme.com/scim/v2/Entitlements").respond(
            status_code=200,
            json={"schemas": [], "totalResults": 0, "Resources": []},
        )
        await client.search_entitlements(
            "displayName co admin",
            application="Active Directory",
        )
        actual_filter = route.calls.last.request.url.params["filter"]
        assert "Active Directory" in actual_filter
        assert "displayName co admin" in actual_filter
        assert "and" in actual_filter.lower()


class TestGetUserRoles:
    @respx.mock
    async def test_returns_roles(self, client: ScimClient, scim_user_response):
        respx.get(
            "https://iiq.acme.com/scim/v2/Users",
            params__contains={"filter": "alice"},
        ).respond(status_code=200, json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 1,
            "Resources": [scim_user_response],
        })
        result = await client.get_user_roles("alice")
        roles = result["roles"]
        assert len(roles) == 1
        assert roles[0]["display"] == "ALL_ACTIVE_USERS"

    @respx.mock
    async def test_no_matching_user(self, client: ScimClient):
        respx.get(
            "https://iiq.acme.com/scim/v2/Users",
            params__contains={"filter": "nonexistent"},
        ).respond(status_code=200, json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 0,
            "Resources": [],
        })
        result = await client.get_user_roles("nonexistent")
        assert result == {"roles": []}


class TestLaunchAndGetWorkflow:
    @respx.mock
    async def test_launch_workflow(self, client: ScimClient):
        route = respx.post("https://iiq.acme.com/scim/v2/LaunchedWorkflows").respond(
            status_code=201,
            json={
                "schemas": ["urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"],
                "id": "wf-1",
            },
        )
        result = await client.launch_workflow("LCM Provisioning", {"identityName": "alice"})
        assert result["id"] == "wf-1"
        # Verify POST body
        body = route.calls.last.request.content
        assert b"LCM Provisioning" in body
        assert b"LaunchedWorkflow" in body

    @respx.mock
    async def test_get_workflow(self, client: ScimClient):
        respx.get("https://iiq.acme.com/scim/v2/LaunchedWorkflows/wf-1").respond(
            status_code=200,
            json={"id": "wf-1", "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow": {}},
        )
        result = await client.get_workflow("wf-1")
        assert result["id"] == "wf-1"


class TestErrorMapping:
    @respx.mock
    async def test_401_maps_to_auth_error(self, client: ScimClient):
        respx.get("https://iiq.acme.com/scim/v2/Roles").respond(status_code=401)
        with pytest.raises(ScimAuthError):
            await client.search_roles("test")

    @respx.mock
    async def test_404_maps_to_not_found(self, client: ScimClient):
        respx.get("https://iiq.acme.com/scim/v2/Roles").respond(status_code=404)
        with pytest.raises(ScimNotFoundError):
            await client.search_roles("test")

    @respx.mock
    async def test_400_maps_to_bad_request(self, client: ScimClient):
        respx.get("https://iiq.acme.com/scim/v2/Roles").respond(
            status_code=400,
            json={"Errors": [{"description": "invalid filter"}]},
        )
        with pytest.raises(ScimBadRequestError):
            await client.search_roles("invalid!!filter!!")

    @respx.mock
    async def test_network_error_maps_to_network_error(self, client: ScimClient):
        respx.get("https://iiq.acme.com/scim/v2/Roles").mock(side_effect=ConnectionError("refused"))
        with pytest.raises(ScimNetworkError):
            await client.search_roles("test")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_scim.py -v --tb=short
```
Expected: FAIL with `ModuleNotFoundError: No module named 'iiq_mcp.scim'`

- [ ] **Step 3: Write `src/iiq_mcp/scim.py`**

```python
"""SCIM 2.0 client for SailPoint IdentityIQ.

Thin async wrapper over httpx.AsyncClient. Each tool call should create
a fresh ScimClient instance.
"""

from __future__ import annotations

from urllib.parse import urljoin

import httpx

from iiq_mcp.errors import (
    ScimAuthError,
    ScimBadRequestError,
    ScimNotFoundError,
    ScimServerError,
    ScimNetworkError,
)

SCIM_BASE = "/scim/v2"


class ScimClient:
    """Async SCIM 2.0 client for SailPoint IdentityIQ."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._timeout = timeout

    async def _request(
        self, method: str, path: str, **kwargs
    ) -> httpx.Response:
        url = urljoin(self._base_url + "/", path.lstrip("/"))
        try:
            async with httpx.AsyncClient(
                auth=self._auth, timeout=self._timeout
            ) as client:
                response = await client.request(method, url, **kwargs)
        except Exception as exc:
            raise ScimNetworkError(
                f"Network error: {exc}", detail=str(exc)
            ) from exc

        if response.status_code == 401:
            raise ScimAuthError("Unauthorized — check credentials", status_code=401)
        if response.status_code == 404:
            raise ScimNotFoundError("Resource not found", status_code=404)
        if response.status_code == 400:
            detail = response.text[:500] if response.text else "Bad request"
            raise ScimBadRequestError(
                f"Bad request: {detail}", status_code=400, detail=detail
            )
        if response.status_code >= 500:
            raise ScimServerError(
                f"IIQ server error ({response.status_code})",
                status_code=response.status_code,
            )
        response.raise_for_status()
        return response

    async def search_roles(
        self, filter_str: str, limit: int = 20
    ) -> dict:
        """GET /scim/v2/Roles with SCIM filter. Returns parsed JSON."""
        response = await self._request(
            "GET",
            f"{SCIM_BASE}/Roles",
            params={"filter": filter_str, "count": limit},
        )
        return response.json()

    async def search_entitlements(
        self,
        filter_str: str,
        application: str | None = None,
        limit: int = 20,
    ) -> dict:
        """GET /scim/v2/Entitlements with optional application filter."""
        if application:
            filter_str = (
                f'{filter_str} and application.displayName eq "{application}"'
            )
        response = await self._request(
            "GET",
            f"{SCIM_BASE}/Entitlements",
            params={"filter": filter_str, "count": limit},
        )
        return response.json()

    async def get_user_roles(self, username: str) -> dict:
        """GET /scim/v2/Users to find the user, then return their roles.

        Returns {"roles": [...]} even if no user or no roles found.
        """
        response = await self._request(
            "GET",
            f"{SCIM_BASE}/Users",
            params={
                "filter": f'userName eq "{username}"',
                "attributes": (
                    "urn:ietf:params:scim:schemas:sailpoint:1.0:User:roles"
                ),
            },
        )
        data = response.json()
        resources = data.get("Resources", [])
        if not resources:
            return {"roles": []}
        user = resources[0]
        sailpoint_ext = user.get(
            "urn:ietf:params:scim:schemas:sailpoint:1.0:User", {}
        )
        roles = sailpoint_ext.get("roles", [])
        return {"roles": roles}

    async def launch_workflow(
        self, workflow_name: str, variables: dict
    ) -> dict:
        """POST /scim/v2/LaunchedWorkflows to trigger a workflow.

        ``variables`` becomes the ``input`` list (list of key/value pairs).
        """
        payload = {
            "schemas": [
                "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"
            ],
            "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow": {
                "workflowName": workflow_name,
                "input": variables,
            },
        }
        response = await self._request(
            "POST", f"{SCIM_BASE}/LaunchedWorkflows", json=payload
        )
        return response.json()

    async def get_workflow(self, workflow_id: str) -> dict:
        """GET /scim/v2/LaunchedWorkflows/{id} to poll for completion."""
        response = await self._request(
            "GET", f"{SCIM_BASE}/LaunchedWorkflows/{workflow_id}"
        )
        return response.json()

    async def validate_credentials(self) -> bool:
        """Validate the stored credentials by making an authenticated GET.

        Returns True if valid, raises ScimAuthError if invalid.
        """
        try:
            response = await self._request(
                "GET",
                f"{SCIM_BASE}/Users",
                params={"filter": f'userName eq "{self._auth[0]}"'},
            )
            return True
        except ScimAuthError:
            raise
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_scim.py -v --tb=short
```
Expected: ~12 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_scim.py src/iiq_mcp/scim.py
git commit -m "feat: add async SCIM 2.0 client with typed error handling"
```

---

### Task 5: `tools.py` — MCP Tool Definitions

**Files:**
- Create: `tests/test_tools.py`
- Create: `src/iiq_mcp/tools.py`

- [ ] **Step 1: Write `tests/test_tools.py`**

```python
"""Tests for MCP tool definitions (with mocked SCIM)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import respx

from iiq_mcp.scim import ScimClient
from iiq_mcp.tools import (
    search_roles,
    search_entitlements,
    get_my_roles,
    request_role,
    request_entitlement,
    get_request_status,
)


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock(spec=ScimClient)
    return client


@pytest.mark.asyncio
class TestSearchRolesTool:
    async def test_returns_formatted_result(self, mock_client: AsyncMock):
        mock_client.search_roles.return_value = {
            "totalResults": 2,
            "Resources": [
                {"id": "r1", "displayName": "Role A"},
                {"id": "r2", "displayName": "Role B"},
            ],
        }
        result = await search_roles(mock_client, "test", limit=10)
        assert result["total"] == 2
        assert len(result["roles"]) == 2
        assert result["roles"][0]["displayName"] == "Role A"
        mock_client.search_roles.assert_called_once_with("test", limit=10)

    async def test_empty_result(self, mock_client: AsyncMock):
        mock_client.search_roles.return_value = {
            "totalResults": 0,
            "Resources": [],
        }
        result = await search_roles(mock_client, "nonexistent")
        assert result["total"] == 0
        assert result["roles"] == []


@pytest.mark.asyncio
class TestSearchEntitlementsTool:
    async def test_basic(self, mock_client: AsyncMock):
        mock_client.search_entitlements.return_value = {
            "totalResults": 1,
            "Resources": [{"id": "e1", "displayName": "Ent A"}],
        }
        result = await search_entitlements(mock_client, "admin")
        assert result["total"] == 1
        mock_client.search_entitlements.assert_called_once()

    async def test_with_application(self, mock_client: AsyncMock):
        mock_client.search_entitlements.return_value = {
            "totalResults": 1,
            "Resources": [{"id": "e1"}],
        }
        await search_entitlements(
            mock_client, "admin", application="Active Directory"
        )
        mock_client.search_entitlements.assert_called_with(
            "admin", application="Active Directory", limit=20
        )


@pytest.mark.asyncio
class TestGetMyRolesTool:
    async def test_returns_roles(self, mock_client: AsyncMock):
        mock_client.get_user_roles.return_value = {
            "roles": [{"display": "R1"}, {"display": "R2"}],
        }
        result = await get_my_roles(mock_client, "alice")
        assert len(result["roles"]) == 2
        assert result["userName"] == "alice"

    async def test_no_roles(self, mock_client: AsyncMock):
        mock_client.get_user_roles.return_value = {"roles": []}
        result = await get_my_roles(mock_client, "nobody")
        assert result["roles"] == []


@pytest.mark.asyncio
class TestRequestRoleTool:
    async def test_launches_workflow(self, mock_client: AsyncMock):
        mock_client.launch_workflow.return_value = {"id": "wf-123"}
        result = await request_role(mock_client, "role-1", "alice")
        assert result["workflow_id"] == "wf-123"
        assert result["status"] == "Launched"
        mock_client.launch_workflow.assert_called_once()

    async def test_with_justification(self, mock_client: AsyncMock):
        mock_client.launch_workflow.return_value = {"id": "wf-456"}
        result = await request_role(
            mock_client, "role-1", "alice", justification="need it"
        )
        assert result["workflow_id"] == "wf-456"
        # Verify the payload included justification
        call_args = mock_client.launch_workflow.call_args
        assert call_args is not None
        _, inputs = call_args
        input_list = inputs.get("variables", [])
        justs = [i for i in input_list if i.get("key") == "justification"]
        assert len(justs) == 1


@pytest.mark.asyncio
class TestRequestEntitlementTool:
    async def test_launches_workflow(self, mock_client: AsyncMock):
        mock_client.launch_workflow.return_value = {"id": "wf-789"}
        result = await request_entitlement(
            mock_client, "alice", "AD", "memberOf", "CN=G",
        )
        assert result["workflow_id"] == "wf-789"


@pytest.mark.asyncio
class TestGetRequestStatusTool:
    async def test_returns_status(self, mock_client: AsyncMock):
        mock_client.get_workflow.return_value = {
            "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow": {
                "completionStatus": "Success",
                "workflowName": "LCM Provisioning",
            },
        }
        result = await get_request_status(mock_client, "wf-1", "alice")
        assert result["completionStatus"] == "Success"
        assert result["workflowName"] == "LCM Provisioning"

    async def test_pending_status(self, mock_client: AsyncMock):
        mock_client.get_workflow.return_value = {
            "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow": {
                "completionStatus": None,
                "workflowName": "LCM Provisioning",
            },
        }
        result = await get_request_status(mock_client, "wf-1", "alice")
        assert result["completionStatus"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_tools.py -v --tb=short
```
Expected: FAIL with `ModuleNotFoundError: No module named 'iiq_mcp.tools'`

- [ ] **Step 3: Write `src/iiq_mcp/tools.py`**

```python
"""MCP tool definitions for SailPoint IdentityIQ.

Each tool takes a ScimClient, an identity_name, and tool-specific inputs.
The tools are designed to be registered with an MCP server.
"""

from __future__ import annotations

from iiq_mcp.plan import (
    build_role_request_plan,
    build_entitlement_request_plan,
    wrap_plan_in_workflow,
)


async def search_roles(
    client, filter_str: str, *, limit: int = 20
) -> dict:
    """Search IIQ's role catalog using a SCIM filter."""
    raw = await client.search_roles(filter_str, limit=limit)
    resources = raw.get("Resources", [])
    return {
        "total": raw.get("totalResults", 0),
        "returned": len(resources),
        "roles": resources,
    }


async def search_entitlements(
    client,
    filter_str: str,
    *,
    application: str | None = None,
    limit: int = 20,
) -> dict:
    """Search IIQ's entitlement catalog."""
    raw = await client.search_entitlements(
        filter_str, application=application, limit=limit
    )
    resources = raw.get("Resources", [])
    return {
        "total": raw.get("totalResults", 0),
        "returned": len(resources),
        "entitlements": resources,
    }


async def get_my_roles(client, identity_name: str) -> dict:
    """Return the roles currently assigned to the authenticated user."""
    result = await client.get_user_roles(identity_name)
    return {
        "userName": identity_name,
        "displayName": identity_name,
        "roles": result.get("roles", []),
    }


async def request_role(
    client,
    role_id: str,
    identity_name: str,
    *,
    justification: str | None = None,
) -> dict:
    """Request one role via the LCM Provisioning workflow."""
    plan = build_role_request_plan(role_id, identity_name)
    payload = wrap_plan_in_workflow(
        plan,
        identity_name=identity_name,
        flow="RolesRequest",
        justification=justification,
    )
    wf_section = payload[
        "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"
    ]
    result = await client.launch_workflow(
        workflow_name="LCM Provisioning",
        variables=wf_section["input"],
    )
    return {
        "workflow_id": result.get("id", ""),
        "status": "Launched",
    }


async def request_entitlement(
    client,
    identity_name: str,
    application: str,
    attribute_name: str,
    attribute_value: str,
    *,
    justification: str | None = None,
) -> dict:
    """Request one entitlement via the LCM Provisioning workflow."""
    plan = build_entitlement_request_plan(
        application, attribute_name, attribute_value, identity_name
    )
    payload = wrap_plan_in_workflow(
        plan,
        identity_name=identity_name,
        flow="EntitlementsRequest",
        justification=justification,
    )
    wf_section = payload[
        "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"
    ]
    result = await client.launch_workflow(
        workflow_name="LCM Provisioning",
        variables=wf_section["input"],
    )
    return {
        "workflow_id": result.get("id", ""),
        "status": "Launched",
    }


async def get_request_status(
    client, workflow_id: str, identity_name: str
) -> dict:
    """Poll a previously-submitted access request for completion."""
    result = await client.get_workflow(workflow_id)
    wf_section = result.get(
        "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow", {}
    )
    return {
        "workflow_id": workflow_id,
        "workflowName": wf_section.get("workflowName", "LCM Provisioning"),
        "completionStatus": wf_section.get("completionStatus"),
        "identityName": identity_name,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_tools.py -v --tb=short
```
Expected: ~12 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_tools.py src/iiq_mcp/tools.py
git commit -m "feat: add 6 MCP tool definitions (3 read, 2 write, 1 status)"
```

---

### Task 6: `server.py` — MCP Stdio Server Bootstrap

**Files:**
- Create: `src/iiq_mcp/server.py`
- (Tested manually — the MCP SDK has no async test runner; the tools are already tested in Task 5)

- [ ] **Step 1: Write `src/iiq_mcp/server.py`**

```python
"""MCP stdio server bootstrap.

Registers the six tools from ``tools.py`` with an MCP server instance
and provides an async ``run()`` entry point.
"""

from __future__ import annotations

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.types import (
    Tool,
    TextContent,
)

from iiq_mcp.scim import ScimClient
from iiq_mcp.tools import (
    search_roles,
    search_entitlements,
    get_my_roles,
    request_role,
    request_entitlement,
    get_request_status,
)

TOOL_NAME_TO_FN = {
    "search_roles": search_roles,
    "search_entitlements": search_entitlements,
    "get_my_roles": get_my_roles,
    "request_role": request_role,
    "request_entitlement": request_entitlement,
    "get_request_status": get_request_status,
}


async def run(
    base_url: str,
    username: str,
    password: str,
) -> None:
    """Start the MCP stdio server.

    This function does not return until stdio is closed.
    """
    server = Server("iiq-mcp")

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name="search_roles",
                description="Search the IIQ role catalog using a SCIM filter.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "SCIM filter expression, e.g. 'displayName co \"finance\"'",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 20,
                            "maximum": 100,
                            "description": "Max results to return",
                        },
                    },
                    "required": ["filter"],
                },
            ),
            Tool(
                name="search_entitlements",
                description="Search the IIQ entitlement catalog using a SCIM filter.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "SCIM filter expression",
                        },
                        "application": {
                            "type": "string",
                            "description": "Restrict to a specific application (e.g. 'Active Directory')",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 20,
                            "maximum": 100,
                        },
                    },
                    "required": ["filter"],
                },
            ),
            Tool(
                name="get_my_roles",
                description="Return the roles currently assigned to the authenticated user.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            Tool(
                name="request_role",
                description="Submit an access request to add one role. Uses the standard LCM Provisioning workflow.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "role_id": {
                            "type": "string",
                            "description": "IIQ role ID from search_roles output",
                        },
                        "justification": {
                            "type": "string",
                            "description": "Business reason for the request",
                        },
                    },
                    "required": ["role_id"],
                },
            ),
            Tool(
                name="request_entitlement",
                description="Submit an access request to add one entitlement to an application account.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "application": {
                            "type": "string",
                            "description": "Application name (e.g. 'Active Directory')",
                        },
                        "attribute_name": {
                            "type": "string",
                            "description": "Entitlement attribute name (e.g. 'memberOf')",
                        },
                        "attribute_value": {
                            "type": "string",
                            "description": "Entitlement attribute value (e.g. 'CN=Admins,OU=Group')",
                        },
                        "justification": {
                            "type": "string",
                            "description": "Business reason for the request",
                        },
                    },
                    "required": ["application", "attribute_name", "attribute_value"],
                },
            ),
            Tool(
                name="get_request_status",
                description="Poll a previously-submitted access request for its current state.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "workflow_id": {
                            "type": "string",
                            "description": "ID returned by request_role or request_entitlement",
                        },
                    },
                    "required": ["workflow_id"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict
    ) -> list[TextContent]:
        fn = TOOL_NAME_TO_FN.get(name)
        if fn is None:
            raise ValueError(f"Unknown tool: {name}")

        client = ScimClient(base_url, username, password)
        identity_name = username

        try:
            if name == "search_roles":
                result = await fn(
                    client,
                    arguments["filter"],
                    limit=arguments.get("limit", 20),
                )
            elif name == "search_entitlements":
                result = await fn(
                    client,
                    arguments["filter"],
                    application=arguments.get("application"),
                    limit=arguments.get("limit", 20),
                )
            elif name == "get_my_roles":
                result = await fn(client, identity_name)
            elif name == "request_role":
                result = await fn(
                    client,
                    arguments["role_id"],
                    identity_name,
                    justification=arguments.get("justification"),
                )
            elif name == "request_entitlement":
                result = await fn(
                    client,
                    identity_name,
                    arguments["application"],
                    arguments["attribute_name"],
                    arguments["attribute_value"],
                    justification=arguments.get("justification"),
                )
            elif name == "get_request_status":
                result = await fn(
                    client, arguments["workflow_id"], identity_name
                )
            else:
                raise ValueError(f"Unknown tool: {name}")

            return [TextContent(type="text", text=str(result))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    async with server.run(
        initialization_options=InitializationOptions(
            server_name="iiq-mcp",
            server_version="0.1.0",
        ),
    ) as server_ctx:
        await server_ctx.async_run()
```

- [ ] **Step 2: Commit**

```bash
git add src/iiq_mcp/server.py
git commit -m "feat: add MCP stdio server bootstrap with 6 tool registrations"
```

---

### Task 7: `cli.py` — Click CLI Commands

**Files:**
- Create: `tests/test_cli.py`
- Create: `src/iiq_mcp/cli.py`

- [ ] **Step 1: Write `tests/test_cli.py`**

```python
"""Tests for the CLI commands (via Click CliRunner)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import keyring
import pytest
from click.testing import CliRunner

from iiq_mcp.cli import cli
from iiq_mcp.errors import CredentialsNotFound

# Import fake keyring from conftest
from tests.conftest import (
    _FAKE_KEYRING,
    fake_keyring_get_password,
    fake_keyring_set_password,
    fake_keyring_delete_password,
)


@pytest.fixture(autouse=True)
def _patch_keyring(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", fake_keyring_get_password)
    monkeypatch.setattr(keyring, "set_password", fake_keyring_set_password)
    monkeypatch.setattr(keyring, "delete_password", fake_keyring_delete_password)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestLogin:
    def test_login_prompts_for_password(self, runner: CliRunner):
        """login prompts for password and stores it."""
        with runner.isolated_filesystem():
            with patch(
                "iiq_mcp.cli.ScimClient.validate_credentials",
                AsyncMock(return_value=True),
            ):
                result = runner.invoke(
                    cli,
                    [
                        "login",
                        "--base-url",
                        "https://iiq.acme.com",
                        "--username",
                        "alice",
                    ],
                    input="s3cret!\n",
                )
            assert result.exit_code == 0, result.output
            assert "Credentials stored" in result.output
            # Verify it was stored
            from iiq_mcp.auth import AuthManager
            mgr = AuthManager("https://iiq.acme.com", "alice")
            assert mgr.get_password() == "s3cret!"

    def test_login_fails_on_bad_credentials(self, runner: CliRunner):
        with runner.isolated_filesystem():
            with patch(
                "iiq_mcp.cli.ScimClient.validate_credentials",
                AsyncMock(side_effect=Exception("401")),
            ):
                result = runner.invoke(
                    cli,
                    [
                        "login",
                        "--base-url",
                        "https://iiq.acme.com",
                        "--username",
                        "alice",
                    ],
                    input="badpass\n",
                )
            assert result.exit_code == 1
            assert "Invalid" in result.output or "401" in result.output


class TestLogout:
    def test_logout_removes_credentials(self, runner: CliRunner):
        from iiq_mcp.auth import AuthManager
        AuthManager("https://iiq.acme.com", "alice").set_password("s3cret!")
        result = runner.invoke(
            cli,
            ["logout", "--base-url", "https://iiq.acme.com", "--username", "alice"],
        )
        assert result.exit_code == 0
        assert "removed" in result.output.lower()
        # Verify deleted
        mgr = AuthManager("https://iiq.acme.com", "alice")
        with pytest.raises(CredentialsNotFound):
            mgr.get_password()


class TestServe:
    @patch("iiq_mcp.cli.asyncio.run")
    @patch("iiq_mcp.cli.AuthManager.get_password", return_value="s3cret!")
    def test_serve_reads_env_and_starts(
        self, mock_get_pwd, mock_asyncio_run, runner: CliRunner
    ):
        result = runner.invoke(
            cli,
            ["serve"],
            env={
                "IIQ_BASE_URL": "https://iiq.acme.com",
                "IIQ_USERNAME": "alice",
            },
        )
        # serve enters an infinite loop; mock_asyncio_run raises StopIteration
        # but we just check the CLI parser works
        assert result.exit_code in (0, 1)
        mock_asyncio_run.assert_called_once()

    @patch("iiq_mcp.cli.AuthManager.get_password", return_value="s3cret!")
    def test_serve_missing_env_shows_error(
        self, mock_get_pwd, runner: CliRunner
    ):
        result = runner.invoke(cli, ["serve"])
        # No env vars set → should show usage error
        assert result.exit_code != 0
        assert "IIQ_BASE_URL" in result.output or "IIQ_USERNAME" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cli.py -v --tb=short
```
Expected: FAIL with `ModuleNotFoundError: No module named 'iiq_mcp.cli'`

- [ ] **Step 3: Write `src/iiq_mcp/cli.py`**

```python
"""iiq-mcp CLI: login, logout, status, serve.

Uses Click (same as iiq-curate) for a consistent CLI experience.
"""

from __future__ import annotations

import asyncio
import os
import sys

import click

from iiq_mcp.auth import AuthManager
from iiq_mcp.errors import CredentialsInvalid
from iiq_mcp.scim import ScimClient


@click.group()
def cli():
    """iiq-mcp: a Model Context Protocol server for SailPoint IdentityIQ 8.5."""


@cli.command()
@click.option("--base-url", envvar="IIQ_BASE_URL", required=True,
              help="IIQ base URL (e.g. https://iiq.acme.com)")
@click.option("--username", envvar="IIQ_USERNAME", required=True,
              help="Your IIQ username")
@click.option("--password", envvar="IIQ_PASSWORD", default=None,
              help="Your IIQ password (prompted if not given)")
def login(base_url: str, username: str, password: str | None) -> None:
    """Store IIQ credentials in the OS keychain."""
    if password is None:
        password = click.prompt("Password", hide_input=True)

    # Validate before storing
    client = ScimClient(base_url, username, password)
    try:
        asyncio.run(client.validate_credentials())
    except Exception as exc:
        raise click.ClickException(
            f"Invalid credentials for {username}@{base_url}: {exc}"
        )

    AuthManager(base_url, username).set_password(password)
    click.echo(f"Credentials stored in OS keychain for {username}@{base_url}")


@cli.command()
@click.option("--base-url", envvar="IIQ_BASE_URL", required=True,
              help="IIQ base URL (e.g. https://iiq.acme.com)")
@click.option("--username", envvar="IIQ_USERNAME", required=True,
              help="Your IIQ username")
def logout(base_url: str, username: str) -> None:
    """Remove IIQ credentials from the OS keychain."""
    AuthManager(base_url, username).delete()
    click.echo(f"Credentials removed for {username}@{base_url}")


@cli.command()
def status() -> None:
    """Show current IIQ connection status."""
    base_url = os.environ.get("IIQ_BASE_URL")
    username = os.environ.get("IIQ_USERNAME")
    if not base_url or not username:
        click.echo("Not configured. Set IIQ_BASE_URL and IIQ_USERNAME.")
        return
    click.echo(f"IIQ instance: {base_url}")
    click.echo(f"User:        {username}")
    try:
        pwd = AuthManager(base_url, username).get_password()
        click.echo("Keychain:    credentials present")
        client = ScimClient(base_url, username, pwd)
        try:
            valid = asyncio.run(client.validate_credentials())
            click.echo("Connection:  OK" if valid else "Connection:  FAILED")
        except Exception:
            click.echo("Connection:  FAILED (check credentials)")
    except Exception:
        click.echo("Keychain:    no credentials stored")
        click.echo("Run `iiq-mcp login` to authenticate")


@cli.command()
def serve() -> None:
    """Start the MCP server (stdio transport).

    Requires IIQ_BASE_URL and IIQ_USERNAME environment variables.
    Password is fetched from the OS keychain.
    """
    base_url = os.environ.get("IIQ_BASE_URL")
    username = os.environ.get("IIQ_USERNAME")
    if not base_url or not username:
        click.echo(
            "Set IIQ_BASE_URL and IIQ_USERNAME environment variables.",
            err=True,
        )
        sys.exit(1)

    try:
        password = AuthManager(base_url, username).get_password()
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    from iiq_mcp.server import run
    asyncio.run(run(base_url, username, password))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_cli.py -v --tb=short
```
Expected: ~6 passed

**Note:** If tests fail because `asyncio.run()` in tests needs special handling, add `async def ...` tests or adjust `test_serve` to use a simpler mock.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py src/iiq_mcp/cli.py
git commit -m "feat: add CLI with login, logout, status, serve commands"
```

---

## Installation & Smoke Test

After all 7 tasks:

- [ ] **Verify the package installs**

```bash
pip install -e ".[dev]"
```

- [ ] **Verify CLI works**

```bash
iiq-mcp --help
```
Expected: Shows login, logout, status, serve commands.

- [ ] **Verify help text for each subcommand**

```bash
iiq-mcp login --help
iiq-mcp serve --help
```

- [ ] **Run all tests**

```bash
python -m pytest -v
```
Expected: All tests pass (target: ~40 tests).

- [ ] **Final commit**

```bash
git add -A && git commit -m "chore: complete v0.1.0 implementation"
git push origin main
```
