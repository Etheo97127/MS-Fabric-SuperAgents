"""Workspace gatekeeping — which workspaces this repo is allowed to touch at all.

The gap this closes
-------------------
`fab` authenticates as a human who can typically see every workspace in the tenant. The
verb classes decide *how* a workspace may be changed, and the profile names the workspaces
this project uses — but nothing previously stopped an agent from targeting a workspace the
profile never mentioned. The effective blast radius was "everything the signed-in person
can reach", which is not a boundary at all.

Three failure modes this prevents, in increasing order of how quietly they happen:

1. A typo or hallucinated name resolving to somebody else's workspace.
2. Two workspaces sharing a display name, so name-based resolution picks the wrong one.
3. A workspace being deleted and recreated, or renamed, so a name that once meant the dev
   workspace now means something else entirely. Nothing about the name lookup would notice.

The fix is to stop resolving by name at operation time. `fabctl profile lock` resolves each
declared name to a workspace ID once, records it, and every later operation goes through
the recorded ID. The lock file is committed, so changing what this repo may touch is a
reviewable diff rather than a side effect of a rename somebody made in the portal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import Profile

GUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")

SCRATCH_KEY = "scratch"


class OutOfScope(Exception):
    """The requested workspace is not one this repo may touch. The message is the guidance."""


class LockError(Exception):
    """The lock file is missing, malformed, or inconsistent with the profile."""


@dataclass(frozen=True)
class PinnedWorkspace:
    key: str                      # environment id, or "scratch"
    display_name: str
    id: str
    capacity_id: str | None = None

    def __post_init__(self) -> None:
        if not GUID.match(self.id):
            raise LockError(f"{self.key}: workspace id {self.id!r} is not a GUID")


@dataclass(frozen=True)
class Lock:
    profile: str
    tenant_id: str
    workspaces: dict[str, PinnedWorkspace] = field(default_factory=dict)
    generated_at: str = ""
    generated_by: str = ""

    # -- lookups ------------------------------------------------------------

    @property
    def allowed_ids(self) -> set[str]:
        return {w.id.lower() for w in self.workspaces.values()}

    def by_key(self, key: str) -> PinnedWorkspace:
        try:
            return self.workspaces[key]
        except KeyError:
            known = ", ".join(sorted(self.workspaces)) or "none"
            raise OutOfScope(
                f"no workspace pinned for {key!r}. This lock covers: {known}.\n"
                f"If the profile gained an environment, re-run: fabctl profile lock"
            ) from None

    def by_id(self, workspace_id: str) -> PinnedWorkspace | None:
        for w in self.workspaces.values():
            if w.id.lower() == workspace_id.lower():
                return w
        return None

    # -- guards -------------------------------------------------------------

    def assert_in_scope(self, workspace_id: str, *, display_name: str | None = None) -> PinnedWorkspace:
        """The gate. Refuse any workspace this repo has not pinned.

        Called before every operation that names a workspace, mutating or not, so that a
        read against an out-of-scope workspace fails too — a read is how an agent would
        discover a target it should not be touching.
        """
        pinned = self.by_id(workspace_id)
        if pinned is None:
            named = f" ({display_name})" if display_name else ""
            listing = "\n".join(
                f"    {w.key:<10} {w.display_name}  [{w.id}]"
                for w in sorted(self.workspaces.values(), key=lambda w: w.key)
            )
            raise OutOfScope(
                f"workspace {workspace_id}{named} is not in scope for this repository.\n"
                f"  This repo may only touch:\n{listing}\n"
                f"  Widening that set is a reviewable change: edit profiles/{self.profile}.yaml, "
                f"re-run `fabctl profile lock`, and open a PR."
            )
        if display_name is not None and display_name != pinned.display_name:
            raise OutOfScope(
                f"workspace {workspace_id} is pinned as {pinned.display_name!r} but Fabric "
                f"now reports {display_name!r}.\n"
                f"  Either it was renamed, or the id was reused by a different workspace. "
                f"Nothing proceeds until a human confirms which.\n"
                f"  If the rename was intended: fabctl profile lock --refresh, then commit the diff."
            )
        return pinned

    def assert_tenant(self, tenant_id: str | None) -> None:
        """Refuse if the signed-in identity is in a different tenant than the lock.

        Guards the case where someone is signed into a personal or client tenant and runs a
        command expecting the work tenant. Without this, every other guard still passes,
        because the IDs simply do not exist there and the error would look like a typo.
        """
        if not tenant_id or not self.tenant_id:
            return
        if tenant_id.lower() != self.tenant_id.lower():
            raise OutOfScope(
                f"signed in to tenant {tenant_id}, but this repo is locked to "
                f"{self.tenant_id}.\n"
                f"  Switch tenant with `fab auth login --tenant {self.tenant_id}`, or, if the "
                f"repo genuinely moved tenants, re-lock and commit the diff."
            )


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def lock_path(root: Path, profile_name: str) -> Path:
    return root / "profiles" / f"{profile_name}.lock.yaml"


def load(root: Path, profile: Profile) -> Lock:
    path = lock_path(root, profile.name)
    if not path.exists():
        raise LockError(
            f"no workspace lock at {path}.\n"
            f"Nothing may touch Fabric until the workspaces this repo owns are pinned:\n"
            f"    fabctl profile lock --profile {profile.name}\n"
            f"This resolves each workspace named in the profile to its id once, so later "
            f"operations cannot be redirected by a rename."
        )

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("profile") != profile.name:
        raise LockError(
            f"{path} was generated for profile {raw.get('profile')!r}, but the active "
            f"profile is {profile.name!r}. Re-run: fabctl profile lock"
        )

    workspaces = {
        key: PinnedWorkspace(
            key=key,
            display_name=entry["display_name"],
            id=entry["id"],
            capacity_id=entry.get("capacity_id"),
        )
        for key, entry in (raw.get("workspaces") or {}).items()
    }

    lock = Lock(
        profile=raw["profile"],
        tenant_id=raw.get("tenant_id", ""),
        workspaces=workspaces,
        generated_at=raw.get("generated_at", ""),
        generated_by=raw.get("generated_by", ""),
    )

    # A profile that gained an environment must be re-locked, or that environment would
    # silently have no gate at all.
    declared = {e.id for e in profile.environments}
    if profile.scratch:
        declared.add(SCRATCH_KEY)
    missing = declared - set(workspaces)
    if missing:
        raise LockError(
            f"{path} has no entry for: {', '.join(sorted(missing))}.\n"
            f"The profile declares them but they were never pinned. Re-run: fabctl profile lock"
        )
    return lock


def dump(lock: Lock) -> str:
    """Serialise for commit. Ordered and stable so the diff shows only real changes."""
    body = {
        "_comment": (
            "GENERATED by `fabctl profile lock`. Commit this. It is the allowlist of "
            "workspaces this repository may touch; a workspace absent from it is refused. "
            "Regenerate with --refresh after an intentional rename, and review the diff."
        ),
        "profile": lock.profile,
        "tenant_id": lock.tenant_id,
        "generated_at": lock.generated_at,
        "generated_by": lock.generated_by,
        "workspaces": {
            key: {
                "display_name": w.display_name,
                "id": w.id,
                **({"capacity_id": w.capacity_id} if w.capacity_id else {}),
            }
            for key, w in sorted(lock.workspaces.items())
        },
    }
    return yaml.safe_dump(body, sort_keys=False, default_flow_style=False, allow_unicode=True)


def save(root: Path, lock: Lock) -> Path:
    path = lock_path(root, lock.profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(lock), encoding="utf-8", newline="\n")
    return path


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# blast radius
# ---------------------------------------------------------------------------

class BlastRadiusExceeded(Exception):
    """One run tried to touch more items than the profile permits."""


class RunBudget:
    """Cap on how many distinct items a single run may mutate.

    Cheap insurance against a loop that mistakes "update one notebook" for "update every
    notebook". The cap is per run, not per command, because the damage comes from repetition.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self._touched: set[str] = set()

    @property
    def touched(self) -> int:
        return len(self._touched)

    def charge(self, item_ref: str) -> None:
        if item_ref in self._touched:
            return  # touching the same item twice is one item's worth of blast radius
        if len(self._touched) >= self.limit:
            raise BlastRadiusExceeded(
                f"this run has already modified {self.limit} items, which is the cap in "
                f"this profile.\n"
                f"  Already touched: {', '.join(sorted(self._touched))}\n"
                f"  Refusing {item_ref}.\n"
                f"  If this many changes really are intended, a human should raise "
                f"`blast_radius` in the profile for this change and review why so many "
                f"items move at once."
            )
        self._touched.add(item_ref)


# ---------------------------------------------------------------------------
# resolution — turning declared names into pinned ids, once
# ---------------------------------------------------------------------------

class Unresolved(Exception):
    """One or more declared workspaces do not exist, or are ambiguous."""


def _tenant_id() -> str:
    """Read the signed-in tenant from `fab auth status`."""
    from . import fabwrap
    result = fabwrap.run(["auth", "status"], json_output=False, check=False, timeout=60)
    for line in f"{result.stdout}\n{result.stderr}".splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "tenant id":
            value = value.strip()
            return "" if value in ("", "N/A") else value
    return ""


def _live_workspaces() -> list[dict[str, Any]]:
    from . import fabwrap
    result = fabwrap.api("workspaces")
    value = (result.parsed or {}).get("value", [])
    # Personal workspaces ("My workspace") are never a valid target: they are per-user,
    # cannot be git-connected, and a name match there would be a false positive.
    return [w for w in value if w.get("type") != "Personal"]


def resolve(profile: Profile, *, previous: Lock | None = None) -> Lock:
    """Resolve every workspace the profile declares to a pinned id.

    Ambiguity is fatal rather than resolved by picking the first match. Two workspaces
    sharing a display name is exactly the situation that makes name-based targeting
    dangerous, so it must be settled by a human choosing an id, not by ordering luck.
    """
    live = _live_workspaces()
    by_name: dict[str, list[dict[str, Any]]] = {}
    for w in live:
        by_name.setdefault(w.get("displayName", ""), []).append(w)

    wanted: dict[str, str] = {e.id: e.workspace for e in profile.environments}
    if profile.scratch:
        wanted[SCRATCH_KEY] = profile.scratch.workspace

    pinned: dict[str, PinnedWorkspace] = {}
    missing: list[str] = []
    ambiguous: list[str] = []

    for key, name in wanted.items():
        matches = by_name.get(name, [])
        if not matches:
            missing.append(f"{key:<10} {name}")
            continue
        if len(matches) > 1:
            ids = ", ".join(m["id"] for m in matches)
            ambiguous.append(f"{key:<10} {name}  ->  {ids}")
            continue
        match = matches[0]
        pinned[key] = PinnedWorkspace(
            key=key,
            display_name=match["displayName"],
            id=match["id"],
            capacity_id=match.get("capacityId"),
        )

    if ambiguous:
        raise Unresolved(
            "these names match more than one workspace, so pinning by name is unsafe:\n  "
            + "\n  ".join(ambiguous)
            + "\n\nRename one in Fabric, or pin the id by hand in the lock file. Do not "
              "let the framework guess which one you meant."
        )

    if missing:
        visible = "\n  ".join(
            f"{w['displayName']}  [{w['id']}]" for w in sorted(live, key=lambda w: w["displayName"])
        )
        raise Unresolved(
            "these workspaces are declared in the profile but do not exist (or you cannot "
            "see them):\n  " + "\n  ".join(missing)
            + f"\n\nWorkspaces visible to this identity:\n  {visible}\n\n"
              "Create the missing ones in Fabric, or edit the profile to name workspaces "
              "that exist. fabctl will not create them: choosing which workspaces this "
              "repository may touch is a human decision, and the whole point of the lock "
              "is that it records one."
        )

    return Lock(
        profile=profile.name,
        tenant_id=_tenant_id() or (previous.tenant_id if previous else ""),
        workspaces=pinned,
        generated_at=utcnow_iso(),
        generated_by=_signed_in_account(),
    )


def _signed_in_account() -> str:
    from . import fabwrap
    result = fabwrap.run(["auth", "status"], json_output=False, check=False, timeout=60)
    for line in f"{result.stdout}\n{result.stderr}".splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "account":
            return value.strip()
    return "unknown"
