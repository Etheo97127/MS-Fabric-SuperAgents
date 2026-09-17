# MCP server exploration — findings

Consolidated from the `core-mcp` and `local-mcp` branch notes (September 2026), plus what
was established while designing the framework. This is the **record of what was tried and
what it cost**, kept because the conclusions are non-obvious and someone will reasonably
ask "why aren't we using the Fabric MCP server?".

The operational rules that follow from this live in [AGENTS.md](../../AGENTS.md). This file
is history and reference; it is not instructions.

---

## What was explored

Two Fabric MCP servers, one branch each:

| | Core (remote) | Local (pro-dev) |
|---|---|---|
| Endpoint | `https://api.fabric.microsoft.com/v1/mcp/core` | stdio subprocess on this machine |
| Transport | Streamable HTTP | stdio |
| Auth | OAuth 2.0 Bearer via Microsoft Entra ID, scope `https://api.fabric.microsoft.com/.default` | inherits the host's Fabric account context |
| Install | none — hosted | VS Code extension, `npx @microsoft/fabric-mcp`, or .NET source build |
| Gives you | workspace management, item CRUD, permissions, folders | OneLake file ops, item creation, offline API specs and item JSON schemas |
| Does **not** give you | OneLake access, job execution, **git integration** | workspace/permission/folder management, full item CRUD |

## Outcome: Core is not used, Local is used for knowledge only

### Core MCP — dropped

Two independent reasons, either sufficient:

1. **It cannot be authenticated from Claude Code.** The tenant has Dynamic Client
   Registration disabled. Pre-configuring `oauth.clientId` in `.mcp.json` does not help —
   Claude Code still attempts DCR (anthropics/claude-code
   [#38102](https://github.com/anthropics/claude-code/issues/38102),
   [#26675](https://github.com/anthropics/claude-code/issues/26675),
   [#67258](https://github.com/anthropics/claude-code/issues/67258), all open as of this
   writing). VS Code + Copilot can connect because the extension ships a pre-registered
   first-party client ID.

2. **Even connected, it is redundant.** Its
   [30 tools](https://learn.microsoft.com/en-us/rest/api/fabric/articles/mcp-servers/core-remote/tools-core-mcp-server)
   contain no git integration, no job execution and no OneLake access. The `fab` CLI covers
   all of it and more, from every surface including CI.

Reads were considered separately and also rejected — see section 1 of the framework doc.
The short version: MCP costs tokens twice (schemas in every request, verbose results), a
split read path breaks skill portability, and reads are evidence for writes so they belong
on the audited path.

The original core-mcp branch config is preserved at `doc/framework/mcp.json.pre-framework.bak`
if it ever needs restoring.

### Local MCP — kept, knowledge only

Its unique value is the one thing a CLI genuinely cannot provide: bundled OpenAPI specs,
per-item-type JSON schemas and Microsoft's best practices, offline, at authoring time. That
is what makes it possible to hand-write a correct `.platform` and item definition rather
than guessing and failing a sync — which matters because the framework creates new items
repo-first.

Its OneLake **write** tools are denied by the `PreToolUse` hook. Not because they don't work,
but because a write that goes agent → Fabric directly leaves no entry in `ledger/`, and
there is no interception point after the fact.

Current config in `.mcp.json`:

```json
{
  "mcpServers": {
    "fabric-local": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@microsoft/fabric-mcp@latest", "server", "start",
               "--transport", "stdio", "--mode", "namespace", "--read-only"]
    }
  }
}
```

Measured against `Fabric.Mcp.Server 1.4.0` on 2026-09-17 by handshaking with the server and
calling `tools/list` — not read from `--help`, which is misleading here (register V8).

| args | tools | approx tokens/turn |
|---|---:|---:|
| `--namespace docs --read-only` | **1** | **~340**  <- in use |
| `--mode all --read-only` | 26 | ~4,741 |
| `--mode all` | 48 | includes create / run / delete |
| `--mode namespace` (documented default) | **0** | exposes nothing |
| no flags | **0** | exposes nothing |

An earlier draft of this file used `--mode namespace` on the strength of the help text
calling it the default. It exposes no tools whatsoever. The config would have looked fine,
connected fine, and contributed nothing — a reminder that "the server connects" says nothing
about whether it works.

`--read-only` is real enforcement, not a hint: it drops 48 tools to 26, removing
`core_create-item`, `datafactory_create-*` and `run-pipeline`. Narrowing further to
`--namespace docs` leaves one router tool covering all six documentation tools
(`api-examples`, `best-practices`, `item-api-spec`, `item-definitions`, `list-item-types`,
`platform-api-spec`) — which is the entire reason this server is configured. Nothing else is
loaded, so the deny hook has nothing to catch here; the restriction is structural.

**VS Code needs its own copy.** The extension reads `.vscode/mcp.json` (key: `servers`);
Claude Code reads `.mcp.json` (key: `mcpServers`). Same server, same args, two files, because
the hosts disagree on schema. `fabctl preflight` compares them and reports drift, since a
stale copy on one surface would hand that agent tools the other does not have. The Fabric VS
Code extension also auto-registers its own server — disable it, or Copilot gets a second,
unscoped Fabric server alongside this one. See `.vscode/README.md`.

Expect npm cache activity on first run. If `npm install` is ever run in this directory
instead of relying on `npx`, a `node_modules/` appears — it is already gitignored.

---

## Practical notes worth keeping

- **`.mcp.json` cannot be written by a remote or cloud Claude session.** That is a
  deliberate guardrail: an MCP config grants tool and server access. Create or edit it
  locally. If it is missing on a fresh clone, restore it from this file.
- **Verifying a server is live:** run `/mcp` in Claude Code. stdio servers show connected
  without an OAuth step; HTTP servers need the browser login first.
- **Core MCP is in preview.** Its tool list may change. Since nothing in the framework
  depends on it, that risk is contained to zero.
- **Auth for Local was never explicitly documented by Microsoft** — it integrates with
  whatever Fabric account context the host already has. If calls fail with an auth error
  once tools are live, that is the signal a credential step exists that the docs omit.

---

## Branch history

The exploration originally lived on two branches off `master`:

- `core-mcp` — carried `.mcp.json` and `AGENT.md` for the remote server
- `local-mcp` — created from master but never received a commit; its notes lived only in an
  untracked file outside the repo

**Both branches were deleted on 2026-09-17**, locally and on the remote. They no longer
represented alternatives once the framework settled the question: one server is used, one is
not, and both facts belong in the same place — this file. Keeping branches named after a
decision that has been made invites someone to assume the decision is still open.

Nothing was lost. `local-mcp` was byte-identical to `master`. `core-mcp` carried one commit,
`f65db77`, which had already been merged into `master` locally and is carried on the
`agent/framework-bootstrap` branch.

One thing to know if you are reading this from a fresh clone: at the time of the deletion
`origin/master` was still at the initial commit `d77e230`, so `f65db77` survived on the
remote only because `agent/framework-bootstrap` had been pushed first. The safety check that
cleared the deletion compared against *local* master, which had already been fast-forwarded —
a check that gave the right answer for a slightly wrong reason. **Compare against the remote
ref, not the local one, before deleting a remote branch.**
