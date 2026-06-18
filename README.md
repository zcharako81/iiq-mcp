# iiq-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server for SailPoint IdentityIQ 8.5.

Search and request roles and entitlements in IdentityIQ through SCIM 2.0,
directly from any MCP-compatible AI client (Claude Code, OpenCode, etc.).

## Status

**v0.1.0** — early development. See [design spec](docs/superpowers/specs/2026-06-18-iiq-mcp-server-design.md).

## Install

```bash
pip install git+https://github.com/zcharako81/iiq-mcp.git

# Or editable dev install:
git clone https://github.com/zcharako81/iiq-mcp.git
cd iiq-mcp && pip install -e ".[dev]"
```

## Quickstart

```bash
# 1. Authenticate
iiq-mcp login --base-url https://iiq.acme.com --username alice

# 2. Start the server (for Claude Code / OpenCode)
iiq-mcp serve

# 3. In your AI client:
#    "search for roles related to finance"
#    "what roles do I currently have?"
#    "request the Senior Approver role for me"
```

## Commands

| Command | Purpose |
|---|---|
| `iiq-mcp login` | Store IIQ credentials in OS keychain |
| `iiq-mcp logout` | Remove credentials from OS keychain |
| `iiq-mcp serve` | Start MCP server (stdio transport) |
| `iiq-mcp status` | Show connection status |

## Configuration

The AI client launches `iiq-mcp serve` with these environment variables:

| Variable | Purpose |
|---|---|
| `IIQ_BASE_URL` | Your IIQ base URL (e.g. `https://iiq.acme.com`) |
| `IIQ_USERNAME` | Your IIQ username (for keychain lookup) |

## License

MIT
