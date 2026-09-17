# .vscode — why these files exist

## `mcp.json`

VS Code reads `.vscode/mcp.json` with a **`servers`** key; Claude Code reads `.mcp.json`
with **`mcpServers`**. Same server, same args, two files, because the two hosts disagree on
the schema. There is no way to share one file, so the args are duplicated and must be kept
in step. `fabctl preflight` compares them and reports a mismatch.

The args are not arbitrary. Measured by handshaking with Fabric.Mcp.Server 1.4.0 and
calling `tools/list`:

| args | tools | approx tokens/turn |
|---|---:|---:|
| `--namespace docs --read-only` | 1 | ~340 |
| `--mode all --read-only` | 26 | ~4,741 |
| `--mode all` | 48 | includes create / run / delete |
| `--mode namespace` (documented default) | **0** | exposes nothing |
| no flags | **0** | exposes nothing |

**The Fabric VS Code extension auto-registers its own Fabric MCP server.** If you leave that
enabled alongside this one you get two servers: ours scoped to documentation, and the
extension's on whatever defaults it ships. That would give Copilot a mutation path the deny
hook has to catch, when the point of `--namespace docs --read-only` is that no such path is
ever loaded.

So: in the MCP view (**Extensions → MCP Servers**, or `MCP: List Servers`), disable the
extension's auto-registered Fabric server and keep `fabric-local` from this file. Verify with
`MCP: List Servers` — exactly one Fabric entry, exposing one tool named `docs`.

## `settings.json`

- `chat.useCustomAgentHooks` — required for Copilot to honour the hooks in
  `.claude/settings.json`. Without it, Copilot runs unguarded while Claude Code runs guarded,
  and the framework's "same rules on both surfaces" claim is false.
- `chat.mcp.access` — lets workspace MCP config apply.
- `files.eol` / `trimTrailingWhitespace` / `insertFinalNewline` — match the normaliser, so
  editing in VS Code does not create diffs the pre-commit hook immediately reverts.
- `python.analysis.extraPaths` — `fabctl` lives under `tools/`, not the repo root.
