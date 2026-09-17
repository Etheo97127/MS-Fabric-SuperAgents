"""PostToolUse: backstop ledger record for any MCP call that got through.

This is not the primary ledger writer. fabctl writes the authoritative record on the
sanctioned path, with the intent, the diff digest and the undo pointer — context a hook
could never reconstruct. This script exists to make bypasses *visible*, so a gap in the
trail shows up as a record marked ``via: hook-backstop`` rather than as silence.

Copilot's PostToolUse payload does not document ``tool_response``, so on that surface the
record may capture intent without outcome. That is recorded honestly in the record itself
rather than guessed at.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _common import guard, project_dir, read_event  # noqa: E402

MAX_CAPTURED = 2000  # characters of tool input/response retained per record


def summarise(value: object) -> object:
    """Keep records reviewable: truncate anything long, note that it was truncated."""
    if isinstance(value, str) and len(value) > MAX_CAPTURED:
        return value[:MAX_CAPTURED] + f"... [truncated, {len(value)} chars]"
    if isinstance(value, dict):
        return {k: summarise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [summarise(v) for v in value[:20]]
    return value


def main() -> None:
    event = read_event()
    tool_name = event.get("tool_name", "")
    if not tool_name.startswith("mcp__"):
        sys.exit(0)

    from fabctl.ledger import Actor, Ledger  # noqa: PLC0415 - keep hook startup cheap

    root = project_dir(event)
    ledger = Ledger(root=root / "ledger")

    response = event.get("tool_response")
    ledger.append({
        "via": "hook-backstop",
        "intent": "(not captured: hook backstop records the call, not the reasoning)",
        "action": f"mcp.{tool_name}",
        "actor": Actor.detect().to_dict(),
        "session_id": event.get("session_id"),
        "tool_use_id": event.get("tool_use_id"),
        "inputs": summarise(event.get("tool_input")),
        "result": (
            {"status": "unknown", "note": "host did not supply tool_response"}
            if response is None else
            {"status": "returned", "response": summarise(response)}
        ),
        "warning": (
            "Recorded by the PostToolUse backstop, which means this call did not go "
            "through fabctl. Investigate why."
        ),
    })


if __name__ == "__main__":
    guard(main)
