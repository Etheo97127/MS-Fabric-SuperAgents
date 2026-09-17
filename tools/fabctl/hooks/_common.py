"""Shared plumbing for agent hooks.

Hooks run for both Claude Code and VS Code Copilot. Copilot reads .claude/settings.json
natively, so one committed config governs both — but the two payloads differ slightly and
Copilot's is preview, so everything here treats missing fields as normal rather than
exceptional.

A hook must never crash the agent. Any unexpected failure exits 0 (no decision) and writes
a note to stderr: a broken guard that blocks all work is worse than a guard that is
temporarily absent and says so.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Exit codes the hosts understand.
EXIT_NO_DECISION = 0
EXIT_BLOCK = 2


def read_event() -> dict[str, Any]:
    """Parse the hook payload from stdin. Returns {} if there is nothing readable."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def project_dir(event: dict[str, Any]) -> Path:
    """Best available guess at the repo root, in the order the hosts provide it."""
    for candidate in (
        os.environ.get("CLAUDE_PROJECT_DIR"),
        event.get("cwd"),
        os.getcwd(),
    ):
        if not candidate:
            continue
        here = Path(candidate).resolve()
        for d in (here, *here.parents):
            if (d / ".git").exists():
                return d
    return Path.cwd()


def deny(reason: str) -> None:
    """Refuse the tool call, giving the agent the guidance it needs to proceed correctly."""
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(EXIT_NO_DECISION)


def allow_silently() -> None:
    sys.exit(EXIT_NO_DECISION)


def warn(message: str) -> None:
    """Surface a concern without blocking. Used while a guard is still in warn mode."""
    print(f"[fabctl] {message}", file=sys.stderr)
    sys.exit(EXIT_NO_DECISION)


def guard(main) -> None:
    """Run a hook body, converting any unexpected failure into a non-decision."""
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a hook must not take the session down
        print(f"[fabctl] hook error ({type(exc).__name__}: {exc}); allowing by default",
              file=sys.stderr)
        sys.exit(EXIT_NO_DECISION)
