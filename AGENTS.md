# AGENTS.md — iiq-mcp

SailPoint IdentityIQ 8.5 MCP Server.

---

## Knowledge Base: iiq-reference / iiq-curator

This project uses `iiq-curate` (from the `iiq-curator` package) for RAG lookups
into the `iiq-reference/` Obsidian vault, which contains SailPoint IIQ 8.5
documentation, patterns, blueprints, and gotchas.

### Vault Discovery

The vault root is resolved at runtime (by `iiq-curate`) via this chain:

1. `--vault <path>` flag
2. `$IIQ_CURATOR_VAULT` environment variable
3. `~/.config/iiq-curator/config.toml` → `vault_root`
4. `.iiq-curator-vault` marker file (walks up from CWD)
5. Fallback: `<project>/iiq-reference`

For this project, the global vault at `~/.iiq-curator/iiq-reference` should be
available (set up by `iiq-curator`'s installer). If not, set the env var:

```bash
export IIQ_CURATOR_VAULT=~/.iiq-curator/iiq-reference
```

### When to Query the Vault

Before writing any SCIM, BeanShell, or IIQ-related code, an agent MUST run:

```bash
iiq-curate query "<topic>" --top-k 5 --format md
```

This returns cited chunks from blueprints, patterns, examples, javadocs, and
gotchas. The agent synthesises the answer from the chunks.

Examples:
```bash
# Before writing a SCIM filter
iiq-curate query "SCIM filter syntax SailPoint"

# Before implementing a ProvisioningPlan
iiq-curate query "ProvisioningPlan structure IIQ"

# Before writing an LCM workflow
iiq-curate query "LCM Provisioning workflow"

# When troubleshooting an auth error
iiq-curate query "SCIM authentication Basic auth IIQ 8.5"
```

### Checking Vault Health

```bash
iiq-curate health
iiq-curate lint
iiq-curate status
```

---

## Source of Truth

- **`docs/superpowers/specs/2026-06-18-iiq-mcp-server-design.md`** — the
  approved architecture, tool API, and design for v1. Every agent MUST read this
  before generating any code.
- **`AGENTS.md`** (this file) — project conventions.
- **`pyproject.toml`** — dependencies, scripts, metadata.
- **`README.md`** — user-facing quickstart.

---

## Architecture (tl;dr)

```
AI Client (Claude Code / OpenCode)
       │ MCP (stdio)
       ▼
  iiq-mcp (Python)
       │ SCIM 2.0 (httpx + Basic Auth)
       ▼
  SailPoint IIQ 8.5
       │ LCM Provisioning workflow
       ▼
  Role / Entitlement provisioning
```

- **Transport:** stdio only in v1 (HTTP+SSE deferred)
- **Auth:** IIQ credentials stored in OS keychain (`keyring`), sent as Basic Auth on every SCIM call
- **Read:** SCIM `/Roles`, `/Entitlements`, `/Users` with SCIM filters
- **Write:** Build a `ProvisioningPlan`, wrap in `LaunchedWorkflow` → `LCM Provisioning`
- **User model:** one OS user = one MCP server = one IIQ user; "the user" is implicit

See the design doc for the full 6-tool API.

---

## Naming Conventions

| Thing | Convention | Example |
|---|---|---|
| Python package | `iiq_mcp` | |
| Module names | snake_case | `scim.py`, `plan.py` |
| Class names | PascalCase | `ScimClient`, `AuthManager` |
| MCP tool names | snake_case | `search_roles`, `request_entitlement` |
| Exception classes | PascalCase + Error suffix | `ScimAuthError`, `ScimNotFoundError` |
| Commands (CLI) | lowercase | `iiq-mcp login`, `iiq-mcp serve` |
| Environment vars | UPPER_SNAKE | `IIQ_BASE_URL`, `IIQ_USERNAME` |

---

## Logging Standards

- Use Python's `logging.getLogger(__name__)` — standard Python convention.
- Do NOT use `print()` for operational output (use `click.echo()` for CLI output,
  `logger.info/warning/error` for diagnostics).
- The MCP toolkit (`mcp` SDK) handles its own transport logging.
- Error messages should be descriptive enough for an AI agent to act on them
  (e.g. "Insufficient permission to search roles" not "403").

---

## Java / BeanShell Standards (for any IIQ-side code)

If the project requires IIQ-side artifacts (workflows, rules):
- Follow the logging standard from `iiq-reference/blueprints/logging-standard.md`
  (custom logger `de.stroeer.beanshell.Technical` with `[CUSTOM LOG]` prefix).
- Match the naming conventions in `iiq-reference/blueprints/`.
- Lint with `iiq-curate lint` before committing any XML.

---

## Delegation Rules

| When you need to… | Delegate to | How |
|---|---|---|
| Find an IIQ pattern, blueprint, or gotcha | `iiq-curator` subagent | `iiq-curate query "<topic>"` |
| Check vault health | `iiq-curator` subagent | `iiq-curate health` |
| Generate a ProvisioningPlan | `iiq-rule-dev` subagent | (if it involves BeanShell rules) |
| Review a design decision | `iiq-curator` subagent | Query vault for similar patterns |

---

## Development Workflow

1. **Read the design doc** (`docs/superpowers/specs/2026-06-18-iiq-mcp-server-design.md`).
2. **Read AGENTS.md** (this file).
3. **Query iiq-reference** for any IIQ-specific patterns needed:
   `iiq-curate query "<topic>" --top-k 5 --format md`
4. **Implement** — follow the module structure in the design doc.
5. **Test** — add unit tests alongside every module.
6. **Lint & format** — `ruff check . && ruff format --check .`
7. **Commit** — conventional commit messages (feat:, fix:, docs:, chore:, refactor:, test:).
