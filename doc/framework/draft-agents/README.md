# Draft subagents — designed, not active

These are complete and validated but deliberately **not** in `.claude/agents/`, so they are
not loaded. Only `fabric-gatekeeper` is active for now.

The reasoning for each is in [../subagents.md](../subagents.md).

| Draft | Activate when |
|---|---|
| `fabric-dml.md` | there is data to mutate — needs `fabctl dml plan` implemented first |
| `drift-reconciler.md` | a workspace is git-connected and can actually drift |
| `fabric-reader.md` | read volume becomes a real cost; the gatekeeper covers read-and-report for now |

To activate, move the file into `.claude/agents/`. `tools/tests/test_agents.py` checks
whatever is there, so a draft that goes live is held to the same rules — no write tools,
name matches filename, description long enough to route on.
