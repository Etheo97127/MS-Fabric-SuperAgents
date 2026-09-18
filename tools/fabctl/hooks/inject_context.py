"""UserPromptSubmit: re-inject the current boundary into context on every turn.

Why this rather than relying on a subagent's fresh context
----------------------------------------------------------
Compaction erodes instructions stated once, early, in a long session. A subagent avoids that
by starting fresh — but only for work you remember to delegate. This hook is stronger for the
specific problem: it re-states the boundary on **every single turn**, so there is no window in
which it has been summarised away. Compaction cannot erode something that is re-added after
each compaction.

The cost is paid every turn, so the output must stay small — a few lines, never a report.
`fabctl preflight` exists for the full picture; this is the part that must never be forgotten.

Speed matters for the same reason. This reads local files only: the lock, the profile name,
the git branch. No network, no `fab` call. Roughly ten milliseconds.

What it deliberately does NOT do: block anything. Injection keeps the boundary visible;
`guard_raw_fab.py` and `fabctl` are what make it real. A reminder that could refuse would be
a confusing place to put enforcement.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import guard, project_dir, read_event  # noqa: E402

MAX_LISTED = 6  # beyond this, summarise rather than enumerate


def git_branch(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


def read_lock(root: Path, profile_name: str) -> dict | None:
    path = root / "profiles" / f"{profile_name}.lock.yaml"
    if not path.exists():
        return None
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None


def build_block(root: Path) -> str:
    profile_name = os.environ.get("FABCTL_PROFILE", "poc")
    lock = read_lock(root, profile_name)
    branch = git_branch(root)

    lines = [f"profile {profile_name} | branch {branch}"]

    if lock is None:
        lines += [
            "IN SCOPE: nothing - no workspace lock exists.",
            "No workspace-targeted command will be permitted until one does:",
            f"    fabctl profile lock --profile {profile_name}",
        ]
    else:
        workspaces = lock.get("workspaces") or {}
        tenant = lock.get("tenant_id", "")
        if tenant:
            lines[0] += f" | tenant {tenant[:8]}"
        lines.append(f"IN SCOPE ({len(workspaces)}), and nothing else in this tenant:")
        for key in sorted(workspaces)[:MAX_LISTED]:
            entry = workspaces[key]
            lines.append(f"    {key:<8} {entry.get('display_name','?')}  [{entry.get('id','?')}]")
        if len(workspaces) > MAX_LISTED:
            lines.append(f"    ... and {len(workspaces) - MAX_LISTED} more; see `fabctl scope`")

    lines += [
        "The signed-in account is a tenant admin and can reach workspaces belonging to",
        "other projects. Anything not listed above is refused before it reaches Fabric.",
        "Mutations go through fabctl, never raw `fab`. DML needs plan -> approval -> apply.",
    ]
    return "\n".join(lines)


def main() -> None:
    event = read_event()
    root = project_dir(event)
    block = f"<fabctl-boundary>\n{build_block(root)}\n</fabctl-boundary>"

    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": block,
        }
    }, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    guard(main)
