"""PreToolUse: refuse MCP tool calls that would mutate Fabric or OneLake.

Why this exists
---------------
An MCP tool call goes agent -> Fabric directly. Fabric records it in Microsoft's audit
log, but the repo ledger never sees it and there is no interception point after the fact.
So the ledger can only be complete if mutations cannot take that path at all.

The classification lives here, in code, rather than in the settings.json matcher. The
matcher stays broad (``mcp__.*``) and this script decides, so the rule is unit-testable
and a reviewer reads one list instead of a regex.

Local MCP is still wanted for what it is good at — offline API specs, per-item-type JSON
schemas, best practices. Those are reads and pass straight through.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import deny, guard, read_event  # noqa: E402

#: Verbs that change remote state. Matched against the tool name after the server prefix.
MUTATING_VERBS = (
    "create", "update", "delete", "remove", "upload", "write", "put", "post", "patch",
    "move", "rename", "add", "set", "run", "execute", "import", "publish", "provision",
    "assign", "grant", "revoke", "connect", "disconnect", "commit", "deploy",
)

#: Read-only names that would otherwise trip a substring match above.
READ_ALLOWLIST = (
    "list", "get_", "search", "describe", "read", "show", "fetch", "query", "schema",
    "spec", "docs", "knowledge", "best_practice", "example",
)

_TOOL = re.compile(r"^mcp__(?P<server>[^_]+(?:_[^_]+)*?)__(?P<tool>.+)$")


def classify(tool_name: str) -> tuple[bool, str]:
    """Return (is_mutation, tool_suffix). Non-MCP tools are never mutations here."""
    match = _TOOL.match(tool_name or "")
    if not match:
        return False, ""
    tool = match.group("tool").lower()

    if any(tool.startswith(prefix) or f"_{prefix}" in tool for prefix in READ_ALLOWLIST):
        # An explicit read name wins: get_notebook_definition is a read even though
        # "definition" sits next to write-ish words.
        if not any(tool.startswith(v) for v in MUTATING_VERBS):
            return False, tool

    return any(
        tool == v or tool.startswith(f"{v}_") or f"_{v}_" in tool or tool.endswith(f"_{v}")
        for v in MUTATING_VERBS
    ), tool


def main() -> None:
    event = read_event()
    tool_name = event.get("tool_name", "")
    is_mutation, tool = classify(tool_name)
    if not is_mutation:
        sys.exit(0)

    deny(
        f"MCP mutation refused: {tool_name}\n"
        f"\n"
        f"Mutations reach Fabric only through fabctl, because that is the single point "
        f"where the audit ledger can record what happened. An MCP call would change "
        f"Fabric with no entry in ledger/.\n"
        f"\n"
        f"Use instead:\n"
        f"  fabctl <verb> ...          # see tools/fabctl/verbs.py for the verb list\n"
        f"\n"
        f"If this is a genuine read that the guard misclassified, the fix is to add its "
        f"name to READ_ALLOWLIST in tools/fabctl/hooks/deny_mcp_mutation.py — not to "
        f"disable the hook."
    )


if __name__ == "__main__":
    guard(main)
