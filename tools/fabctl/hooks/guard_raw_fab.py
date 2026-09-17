"""PreToolUse on Bash: enforce workspace scope, and catch `fab` calls that bypass fabctl.

Two separate concerns, deliberately held to different standards.

**Workspace scope — always enforced, never a warning.**
Whether a command may touch a given workspace is a boundary, and a boundary that warns is
not a boundary. Any workspace id in the command that is not pinned in the lock is refused
outright, regardless of MODE, regardless of which `fab` subcommand it is. This matters most
right now: the signed-in account is a tenant admin who can see and delete workspaces
belonging to other projects, so the only thing standing between an agent typo and someone
else's data is this check.

**Bypassing fabctl — warned during phase 0, denied from phase 1.**
Running `fab` directly is a process failure, not a safety one: the action is legitimate but
no ledger record is written. Phase 0 *is* raw `fab` exploration, so it warns. Flip MODE once
fabctl covers the verbs in daily use — that switch is the moment the audit trail becomes
complete, so make it deliberately.

fabctl invokes `fab` as a Python subprocess, which never passes through the Bash tool, so
this hook only ever sees calls an agent typed directly. That is exactly what it should see.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import deny, guard, project_dir, read_event, warn  # noqa: E402

MODE = "warn"   # "warn" during phase 0 | "deny" from phase 1

#: Subcommands that are setup or diagnostics, not Fabric operations. Always permitted.
ALWAYS_ALLOWED = ("auth", "config", "--version", "-v", "--help", "-h")

#: `fab` invoked as a word, not as part of another name (fabctl, fabric-cli, prefab...).
_FAB_CALL = re.compile(r"(?:^|[\s;&|(`]|&&|\|\|)fab(?=\s)", re.MULTILINE)

_GUID = re.compile(
    r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b"
)

#: A workspace id appears right after `workspaces/` in a REST path, or as a `-W`/`--workspace`
#: argument. Matching only these avoids treating an item or capacity id as a workspace.
_WORKSPACE_REF = re.compile(
    r"workspaces/([0-9a-fA-F-]{36})|--workspace[= ]([0-9a-fA-F-]{36})|-W[= ]([0-9a-fA-F-]{36})"
)


def extract_command(event: dict) -> str:
    return (event.get("tool_input") or {}).get("command") or ""


def offending_invocations(command: str) -> list[str]:
    """Raw `fab ...` invocations that are not in the setup allowlist."""
    found = []
    for match in _FAB_CALL.finditer(command):
        tail = command[match.end():].strip()
        first = tail.split()[0] if tail.split() else ""
        if first.startswith(ALWAYS_ALLOWED):
            continue
        found.append(("fab " + tail.splitlines()[0]).strip())
    return found


def referenced_workspaces(command: str) -> set[str]:
    """Workspace ids the command names, lowercased."""
    ids = set()
    for groups in _WORKSPACE_REF.findall(command):
        for value in groups:
            if value:
                ids.add(value.lower())
    return ids


def load_scope(root: Path) -> tuple[dict[str, str] | None, str]:
    """Return ({workspace_id: display_name}, profile_name) from the lock, or (None, name).

    Reads YAML directly rather than importing fabctl: this runs before every Bash call and
    must stay cheap.
    """
    profile_name = os.environ.get("FABCTL_PROFILE", "poc")
    path = root / "profiles" / f"{profile_name}.lock.yaml"
    if not path.exists():
        return None, profile_name
    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable lock means no scope, not free rein
        return None, profile_name
    return {
        str(entry["id"]).lower(): f"{key} = {entry.get('display_name', '?')}"
        for key, entry in (raw.get("workspaces") or {}).items()
        if entry.get("id")
    }, profile_name


def check_scope(command: str, root: Path) -> None:
    """Refuse any command naming a workspace this repository has not pinned."""
    referenced = referenced_workspaces(command)
    if not referenced:
        return

    scope, profile_name = load_scope(root)

    if scope is None:
        deny(
            f"this command names a workspace, but no workspace lock exists, so nothing is "
            f"in scope yet.\n"
            f"  referenced: {', '.join(sorted(referenced))}\n"
            f"\n"
            f"The signed-in account can reach every workspace in the tenant, including other "
            f"projects'. Until the lock pins which ones belong to this repository, no "
            f"workspace-targeted command is permitted.\n"
            f"  -> fabctl profile lock --profile {profile_name}"
        )

    out_of_scope = referenced - set(scope)
    if out_of_scope:
        allowed = "\n".join(f"    {name}  [{wid}]" for wid, name in sorted(scope.items()))
        deny(
            f"workspace out of scope: {', '.join(sorted(out_of_scope))}\n"
            f"  This repository may only touch:\n{allowed}\n"
            f"\n"
            f"Everything else in this tenant belongs to another project. Widening scope is a "
            f"reviewable change: edit profiles/{profile_name}.yaml, re-run "
            f"`fabctl profile lock`, and open a PR."
        )


def main() -> None:
    event = read_event()
    if event.get("tool_name") not in {"Bash", "PowerShell"}:
        sys.exit(0)

    command = extract_command(event)
    root = project_dir(event)

    # Scope first, and unconditionally. FABCTL_ALLOW_RAW_FAB waives the fabctl-bypass
    # warning for deliberate exploration; it does not waive the boundary.
    check_scope(command, root)

    if os.environ.get("FABCTL_ALLOW_RAW_FAB") == "1":
        sys.exit(0)

    offenders = offending_invocations(command)
    if not offenders:
        sys.exit(0)

    detail = "\n".join(f"  {o}" for o in offenders[:3])
    message = (
        f"raw `fab` call bypasses fabctl, so nothing will be written to ledger/:\n"
        f"{detail}\n"
        f"Prefer the equivalent fabctl verb (tools/fabctl/verbs.py). "
        f"For deliberate one-off exploration, set FABCTL_ALLOW_RAW_FAB=1."
    )

    if MODE == "deny":
        deny(message)
    warn(message)


if __name__ == "__main__":
    guard(main)
