# AGENT.md — core-mcp branch

## Scope of this branch

This branch explores the **Fabric Core MCP Server** — Microsoft Fabric's *remote* MCP integration. It is the counterpart to the `local-mcp` branch, which covers the local/stdio server instead. Do not merge Core-specific and local-specific config together; they are different transports with different auth models.

## Connection details

| Property | Value |
|---|---|
| Endpoint URL | `https://api.fabric.microsoft.com/v1/mcp/core` |
| Transport | Streamable HTTP |
| Auth type | OAuth 2.0 Bearer Token |
| Auth scope | `https://api.fabric.microsoft.com/.default` |
| Identity provider | Microsoft Entra ID |

## MCP host

**Claude Code** is the chosen host for this branch (decision made when this repo was set up — see repo history / the Engineering Harness project notes for the reasoning). Config lives in `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "fabric-core": {
      "type": "http",
      "url": "https://api.fabric.microsoft.com/v1/mcp/core",
      "oauth": {
        "scopes": "https://api.fabric.microsoft.com/.default"
      }
    }
  }
}
```

**Important:** `.mcp.json` cannot be written by a remote/cloud Claude session — it's a deliberate safety guardrail, since an MCP config file grants tool/server access. This file must be created or edited locally (by a human, or by a Claude Code session actually running on this machine). If it's missing on a fresh clone of this branch, recreate it from the snippet above before doing anything else.

## Authentication

This server requires an interactive OAuth login — it cannot be authorized from a non-interactive or remote session. To connect:

1. Open this repo in Claude Code, on this branch, with `.mcp.json` present.
2. Run `/mcp` and select `fabric-core`.
3. Complete the Microsoft Entra ID sign-in in the browser that opens.

Claude Code will first attempt Dynamic Client Registration against Entra ID. If your tenant has DCR locked down, the automatic flow will fail — in that case you'll need a registered app's client ID from your Fabric/Entra admin, added as `oauth.clientId` under the `fabric-core` block above. Tokens are cached and auto-refreshed after the first successful login (stored in the OS keychain / credentials file, not in this repo).

**Known state as of last check:** `fabric-core` shows as requiring authorization — its tools are unavailable until the `/mcp` login above is completed locally. Don't assume the server is broken if tools are missing; check auth status first.

## What Core actually gives you

Per Microsoft's docs, Core (remote) supports:
- Workspace management
- Item CRUD operations (full, not just create)
- Permission management
- Folder management

It does **not** give OneLake file access or local file system integration — that's the local server's job (see `local-mcp` branch). Audit trail: every Core action is logged to Fabric's own audit logs, since operations go through the real hosted API.

## Related work

- The Databricks managed MCP server (remote, Unity-Catalog-backed, also OAuth) is being explored in parallel as a comparison point for the "which platform's harness fits us better" question — see the Engineering Harness Claude Project for that comparison.
- Repo remote: `https://github.com/Etheo97127/MS-Fabric-SuperAgents.git`, this branch tracks `origin/core-mcp`.
