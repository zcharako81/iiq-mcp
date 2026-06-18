# iiq-mcp — MCP Server for SailPoint IdentityIQ 8.5

**Status:** Design (v1)
**Date:** 2026-06-18
**Author:** iiq-curator / brainstorming session

---

## 1. Goal

Build a **Model Context Protocol (MCP) server** that allows AI agents (Claude Code, OpenCode, etc.) to search and request roles/entitlements in SailPoint IdentityIQ 8.5. The server authenticates as the calling user, enforces per-user authorization via IIQ's native security model, and uses IIQ's standard LCM Provisioning workflow for access requests.

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              AI Client (Claude Code, OpenCode)              │
│                                                             │
│  stdio transport (local)                                    │
└────────────────────────────┬────────────────────────────────┘
                             │ MCP protocol (JSON-RPC over stdio)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              iiq-mcp server (Python)                        │
│                                                             │
│  Tools:                                                     │
│    search_roles, search_entitlements, get_my_roles,         │
│    request_role, request_entitlement, get_request_status    │
│                                                             │
│  Auth:    OS keychain (keyring library)                     │
│  HTTP:    httpx → IIQ SCIM                                 │
│  Plan:    Minimal ProvisioningPlan builder (b1)             │
└────────────────────────────┬────────────────────────────────┘
                             │ SCIM 2.0 (HTTP/JSON, Basic Auth)
                             ▼
                  ┌──────────────────────┐
                  │  SailPoint IIQ 8.5   │
                  │  /scim/v2            │
                  │  LCM Provisioning WF │
                  └──────────────────────┘
```

### Transport: stdio only (v1)

The server speaks MCP over stdio. Remote HTTP+SSE support is deferred to v2. Each OS user runs one server instance. This is the simplest model possible:

- No TLS, no port binding, no daemonization
- AI clients launch the server as a subprocess (native MCP pattern)
- Environment variables (`IIQ_BASE_URL`, `IIQ_USERNAME`) tell the server which IIQ instance to talk to and which user to authenticate as
- Password is read from the OS keychain, not from env

### Transport: HTTP+SSE (v2)

Deferred. Adds remote deployment, per-request authentication, and multi-user support behind a TLS-terminating reverse proxy.

## 3. Tool API (v1)

Six tools, organized into Read / Write / Status categories.

### 3.1 Read tools

#### `search_roles`

Search IIQ's role catalog using a SCIM filter.

- **Input:**
  - `filter` (string, required) — SCIM filter expression, e.g. `'displayName co "finance"'`
  - `limit` (int, default 20, max 100) — max results to return
- **Output:** `{ total, returned, roles: [{ id, displayName, description, owner, type, requestable, ...all SCIM Role attributes} ] }` — the output shape is whatever IIQ's SCIM returns; no attribute filtering happens server-side
- **SCIM call:** `GET /scim/v2/Roles?filter=...&count=limit`

#### `search_entitlements`

Search IIQ's entitlement catalog using a SCIM filter.

- **Input:**
  - `filter` (string, required) — SCIM filter expression
  - `application` (string, optional) — restrict to a specific application name
  - `limit` (int, default 20, max 100)
- **Output:** `{ total, returned, entitlements: [{ id, displayName, type, application, ...per-app schema attrs }] }`
- **SCIM call:** `GET /scim/v2/Entitlements?filter=...&count=limit`

#### `get_my_roles`

Return the roles currently assigned to the authenticated user.

- **Input:** (none)
- **Output:** `{ userName, displayName, roles: [{ id, displayName }] }`
- **SCIM call:** `GET /Users?filter=userName eq "<user>"&attributes=urn:...:sailpoint:1.0:User:roles`

### 3.2 Write tools (b1 — single-target only)

#### `request_role`

Submit an access request to add one role to the authenticated user. Uses the standard LCM Provisioning workflow.

- **Input:**
  - `role_id` (string, required) — IIQ role ID from `search_roles`
  - `justification` (string, optional) — business reason (may be required by IIQ policy)
- **Output:** `{ workflow_id, status }`
- **SCIM call:** `POST /scim/v2/LaunchedWorkflows` with:
  - `workflowName: "LCM Provisioning"`
  - `input` containing: `identityName`, plan (serialized `ProvisioningPlan`), `flow: "RolesRequest"`, `justification`

#### `request_entitlement`

Submit an access request to add one entitlement to the authenticated user's account on a specific application.

- **Input:**
  - `application` (string, required) — application name in IIQ
  - `attribute_name` (string, required) — e.g. `memberOf` or `group`
  - `attribute_value` (string, required) — e.g. `CN=Admins,OU=Groups,DC=corp`
  - `justification` (string, optional)
- **Output:** `{ workflow_id, status }`
- **SCIM call:** As above, with `flow: "EntitlementsRequest"` and a plan targeting the specified application.

### 3.3 Status tool

#### `get_request_status`

Poll a previously-submitted access request for completion.

- **Input:**
  - `workflow_id` (string, required) — ID returned by `request_role` or `request_entitlement`
- **Output:** `{ workflow_id, workflowName, completionStatus (null | "Success" | "Error" | "Terminated" | "Warning"), identityName }`
- **SCIM call:** `GET /scim/v2/LaunchedWorkflows/{id}`

## 4. SCIM Client

A thin async wrapper over `httpx.AsyncClient`.

### Module: `scim.py`

```python
class ScimClient:
    def __init__(self, base_url, username, password, timeout=30.0)
    async def search_roles(self, filter_str, limit=20) -> dict
    async def search_entitlements(self, filter_str, application=None, limit=20) -> dict
    async def get_user_roles(self, user_id) -> dict
    async def get_current_user(self) -> dict
    async def launch_workflow(self, workflow_name, variables) -> dict
    async def get_workflow(self, workflow_id) -> dict
```

### Behaviour

- **Fresh client per tool call.** No connection pooling across calls in v1. Simplifies auth state management.
- **Filter encoding:** Passed via `httpx`'s `params` argument (correctly uses `%20` for spaces, `%22` for quotes — the `URLEncoder` problem documented in the test framework is avoided).
- **Error handling:** Typed exceptions:
  - `ScimAuthError` (401) → clear keychain, prompt re-login
  - `ScimNotFoundError` (404) → return "not found" to the AI agent
  - `ScimBadRequestError` (400) → attach IIQ's error message
  - `ScimServerError` (5xx) → return as server error
  - `ScimNetworkError` (connection/timeout) → return as network error
- **No retry logic in v1.** The AI agent retries on network errors if desired.

### Endpoints consumed

| Endpoint | Method | Used by |
|---|---|---|
| `/scim/v2/ServiceProviderConfig` | GET | `login` validation |
| `/scim/v2/Roles` | GET | `search_roles` |
| `/scim/v2/Roles/{id}` | GET | (reserved for v2 detail view) |
| `/scim/v2/Entitlements` | GET | `search_entitlements` |
| `/scim/v2/Users` + `?filter=...` | GET | `get_my_roles` |
| `/scim/v2/LaunchedWorkflows` | POST | `request_role`, `request_entitlement` |
| `/scim/v2/LaunchedWorkflows/{id}` | GET | `get_request_status` |

## 5. ProvisioningPlan Builder (b1 — minimal)

### Module: `plan.py`

```python
def build_role_request_plan(role_id: str, identity_name: str) -> dict:
    ...

def build_entitlement_request_plan(application: str, attribute_name: str,
                                   attribute_value: str, identity_name: str) -> dict:
    ...

def wrap_plan_in_workflow(plan: dict, identity_name: str,
                          flow: str, justification: str | None = None) -> dict:
    ...
```

### Plan shape (role request example)

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow"],
  "urn:ietf:params:scim:schemas:sailpoint:1.0:LaunchedWorkflow": {
    "workflowName": "LCM Provisioning",
    "input": [
      {"key": "identityName", "value": "john.doe"},
      {"key": "plan", "value": "<serialized ProvisioningPlan JSON>"},
      {"key": "flow", "value": "RolesRequest"},
      {"key": "justification", "value": "needed for project X"}
    ]
  }
}
```

### Constraints

- v1 supports **add only** — single role or single entitlement per request.
- The plan is serialized as a JSON string in the workflow's `input` list (matching IIQ's LCM UI convention).
- `flow` is set to `RolesRequest` or `EntitlementsRequest` (tells LCM Provisioning what kind of flow to run).
- v2 adds: bulk (multiple roles/entitlements), removal operations, multi-application plans.

## 6. Auth Flow

### Module: `auth.py`

```python
SERVICE_NAME = "iiq-mcp"

class AuthManager:
    def __init__(self, base_url: str, username: str): ...
    def get_password(self) -> str: ...
    def set_password(self, password: str) -> None: ...
    def delete(self) -> None: ...
```

### Keychain key format

`<username>@<host>` — composite key derived from `urlparse(base_url).netloc`, e.g. `john.doe@iiq.acme.com`. Supports separate credentials for different IIQ instances (dev/staging/prod).

### CLI commands

#### `iiq-mcp login`

- Takes `--base-url`, `--username`, `--password` (prompted if omitted)
- Validates credentials against IIQ (makes a real authenticated call — e.g. `GET /Users?filter=userName eq "<username>"` — any invalid creds return 401)
- Stores password in OS keychain
- Fails fast on bad credentials (credential validation is synchronous before any store)

#### `iiq-mcp logout`

- Takes `--base-url`, `--username`
- Removes the keychain entry for that instance

#### `iiq-mcp serve`

- Reads `IIQ_BASE_URL` + `IIQ_USERNAME` from environment variables
- Fetches password from OS keychain
- Starts the MCP stdio server
- Environment-variable config matches the AI client's MCP server launch pattern

#### `iiq-mcp status`

- Shows the current IIQ instance from environment variables
- Reports whether the keychain has credentials for it

### Environment variables

Used by the AI client to configure the MCP server launch:

```json
{
  "mcpServers": {
    "iiq": {
      "command": "iiq-mcp",
      "args": ["serve"],
      "env": {
        "IIQ_BASE_URL": "https://iiq.acme.com",
        "IIQ_USERNAME": "alice"
      }
    }
  }
}
```

### Why Basic Auth + OS keychain (not OAuth, not session cookies)

- Basic auth is the documented SCIM auth in IIQ 8.5 (OAuth is supported but must be configured per-IIQ-instance)
- OS keychain provides OS-native encryption (macOS Keychain, Windows Credential Manager, Linux Secret Service)
- No token refresh mechanism needed — credentials are valid as long as the user's IIQ password is valid
- OAuth is deferred to v2 as an alternative authentication method

## 7. File Layout

```
iiq-mcp/
├── pyproject.toml
├── README.md
├── LICENSE                       (MIT)
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml
├── src/
│   └── iiq_mcp/
│       ├── __init__.py
│       ├── __main__.py           # python -m iiq_mcp
│       ├── cli.py                # Click: login, logout, status, serve
│       ├── auth.py               # OS keychain via keyring
│       ├── scim.py               # SCIM 2.0 client (httpx.AsyncClient)
│       ├── plan.py               # ProvisioningPlan builder (b1)
│       ├── tools.py              # MCP tool definitions
│       ├── server.py             # MCP stdio server
│       └── errors.py             # Typed exceptions
├── tests/
│   ├── conftest.py               # Shared fixtures: mock SCIM via respx
│   ├── test_auth.py
│   ├── test_scim.py
│   ├── test_plan.py
│   ├── test_tools.py
│   └── test_cli.py
└── examples/
    ├── claude_desktop_config.json
    └── opencode_config.json
```

## 8. Dependencies

| Package | Version | Purpose |
|---|---|---|
| `click` | >=8.1 | CLI framework (matches iiq-curator convention) |
| `httpx` | >=0.27 | Async HTTP client for SCIM calls |
| `mcp` | >=1.0 | Official MCP Python SDK (JSON-RPC over stdio, tool registry) |
| `pydantic` | >=2.0 | Input validation + JSON schema generation for tools |
| `keyring` | >=24.0 | OS keychain integration |
| `pytest` (dev) | >=7 | Test runner |
| `pytest-asyncio` (dev) | >=0.23 | Async test support for httpx |
| `respx` (dev) | >=0.21 | HTTP mock for httpx |
| `ruff` (dev) | >=0.4 | Linting + formatting |

## 9. Testing Strategy

### Layer 1: Unit tests

- **plan.py (highest priority):** `build_role_request_plan` / `build_entitlement_request_plan` / `wrap_plan_in_workflow` return correct dict shapes. Edge cases: special characters, empty justification, unicode in identity names.
- **auth.py:** Composite key format, set/get/delete round-trip (fake keyring backend), `CredentialsNotFound` raised on missing entry, `delete` idempotent.
- **tools.py:** Each tool returns expected shape with mocked ScimClient. Error mapping: auth errors → "run login first", not-found → "not found".
- **cli.py:** Commands exit with expected codes, `login` validates before storing, bad creds exit 1.

### Layer 2: Integration tests with mocked IIQ (respx)

- **scim.py:** Filter encoding (asserts `%20` vs `+`), error mapping (401→ScimAuthError, 404→ScimNotFoundError), LaunchedWorkflow POST body shape, pagination with `count` parameter.

### Layer 3: Manual smoke tests against real IIQ

Documented in README.md:

```bash
iiq-mcp login --base-url https://iiq-test.acme.com --username alice
iiq-mcp serve

# In Claude Code:
# "search for roles related to finance"
# "what roles do I currently have?"
# "request the Senior Approver role for me"
# "check the status of my last request"
```

### Coverage targets

| Module | Target | Rationale |
|---|---|---|
| plan.py | 100% | Most failure-prone code; wrong plan shape = silent IIQ rejections |
| auth.py | 95% | Keychain backend edge cases |
| scim.py | 90% | Client-edge cases (timeout, encoding); httpx itself is pre-tested |
| tools.py | 90% | Input validation + error mapping |
| cli.py | 80% | User-facing surface; actual logic lives elsewhere |

## 10. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| `LCM Provisioning` input shape varies across IIQ versions | High | Validate first in manual smoke tests; documented as the first test step |
| SCIM filter encoding different across httpx/urllib versions | Medium | Explicit test asserts `%20` for spaces, `%22` for quotes |
| `keyring` has no backend on headless Linux | Medium | `login` checks backend, warns if not OS-native; user can fall back to env var password override |
| `get_current_user` via userName filter is fragile | Medium | Falls back gracefully; v2 adds `/Me` endpoint if IIQ supports it |
| No request idempotency → duplicate requests on retry | Low | IIQ de-duplicates active requests on most workflows; not guaranteed |
| Compromised AI client can do anything the IIQ user can do | Low | By design (per-user authorization). No artificial restriction. |

## 11. Out of Scope (v1)

- HTTP+SSE remote transport (v2)
- Bulk operations (v2)
- Removal operations (v2)
- Identity CRUD
- OAuth 2.0
- Multi-user sessions per server instance
- Catalog caching
- Policy violation pre-checks
- Custom workflow configuration
- Auditing/telemetry

## 12. Future Direction (v2, not committed)

- Remote transport with HTTP+SSE
- Bulk operations (multi-target plans)
- Removal operations
- Planned caching with TTL
- Workflow result enrichment (approval chains, comments)
- Identity lookup tools
- Policy violation pre-check via `POST /CheckedPolicyViolations`
- OAuth 2.0 auth method
- Integration with iiq-curator (documentation RAG)
