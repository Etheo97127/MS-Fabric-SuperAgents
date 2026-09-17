"""Stop: refuse to end a turn with uncommitted changes.

Why this rather than auto-committing
------------------------------------
The obvious automation is a hook that runs `git commit -am "auto-commit"` when the turn
ends. It is the wrong thing, and it is worth being explicit about why: the purpose of this
repository's git history is an audit trail of *decisions*, and a decision is carried by the
commit message. A hook does not know what was decided or why, so every message it writes is
some variation of "changes at 14:32" — which turns a trail into a changelog of timestamps.

This hook instead blocks the turn from ending while the tree is dirty. The commit still
happens, still every turn, but the message is written by the party that knows what happened.
Enforcement stays mechanical; quality stays with the author.

Loop safety
-----------
A Stop hook that blocks forever would hang the session. If the same dirty state is seen
repeatedly, this hook gives up and warns instead: a stuck guard must degrade into a nuisance,
never into a wall.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import guard, project_dir, read_event  # noqa: E402

EXIT_BLOCK = 2
MAX_BLOCKS_PER_STATE = 2
PROTECTED_BRANCHES = {"master", "main"}


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30
    )
    return proc.stdout.strip()


def state_key(root: Path, porcelain: str) -> str:
    """Identify this dirty state so repeated blocking on it can be detected."""
    head = git(root, "rev-parse", "HEAD")
    return hashlib.sha256(f"{head}\n{porcelain}".encode()).hexdigest()[:16]


def seen_count(key: str) -> int:
    """Count how many times this exact state has already been blocked."""
    stamp = Path(tempfile.gettempdir()) / f"fabctl-clean-tree-{key}.count"
    n = 0
    try:
        n = int(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        stamp.write_text(str(n + 1), encoding="utf-8")
    except OSError:
        pass
    return n


def summarise(porcelain: str) -> tuple[list[str], int]:
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    shown = [f"  {ln}" for ln in lines[:12]]
    return shown, len(lines)


def main() -> None:
    if os.environ.get("FABCTL_ALLOW_DIRTY_EXIT") == "1":
        sys.exit(0)

    event = read_event()
    root = project_dir(event)
    if not (root / ".git").exists():
        sys.exit(0)

    porcelain = git(root, "status", "--porcelain")
    if not porcelain:
        sys.exit(0)

    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    shown, total = summarise(porcelain)
    key = state_key(root, porcelain)

    if seen_count(key) >= MAX_BLOCKS_PER_STATE:
        print(
            f"[fabctl] still {total} uncommitted path(s) after {MAX_BLOCKS_PER_STATE} "
            f"reminders; not blocking again. Commit them before the next turn, or set "
            f"FABCTL_ALLOW_DIRTY_EXIT=1 if leaving them is deliberate.",
            file=sys.stderr,
        )
        sys.exit(0)

    listing = "\n".join(shown)
    more = f"\n  ... and {total - len(shown)} more" if total > len(shown) else ""

    branch_note = ""
    if branch in PROTECTED_BRANCHES:
        branch_note = (
            f"\n\nYou are on {branch}, which is the trunk. Branch before committing:\n"
            f"    git checkout -b dev        # or the working branch for this session"
        )

    print(
        f"{total} uncommitted path(s) on branch {branch}:\n{listing}{more}\n"
        f"\n"
        f"This repository's history is the audit trail of what was decided and why, so a "
        f"turn must not end with work that has no commit behind it. Uncommitted work cannot "
        f"be reviewed, attributed or reverted, and by the next turn nobody can tell it apart "
        f"from anything else in the tree.\n"
        f"\n"
        f"Commit with a message that says *why*, not what — the diff already shows what:\n"
        f"    git add -A\n"
        f"    git commit -m \"<type>(<scope>): <what changed>\n"
        f"\n"
        f"    <the decision, and what it rules out>\n"
        f"\n"
        f"    Agent-Run-Id: <uuid>\n"
        f"    Agent-Model: claude-opus-5\n"
        f"    Agent-Surface: claude-code\n"
        f"    Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\"\n"
        f"\n"
        f"If the work is unfinished, commit it anyway and say so in the message. A trail that "
        f"records only finished work is not a trail."
        f"{branch_note}",
        file=sys.stderr,
    )
    sys.exit(EXIT_BLOCK)


if __name__ == "__main__":
    guard(main)
