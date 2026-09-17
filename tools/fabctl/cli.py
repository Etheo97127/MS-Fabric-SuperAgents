"""fabctl command line.

Every mutating path through this module does the same four things in the same order:

    1. classify the verb                 (tools/fabctl/verbs.py)
    2. check it against the write-path rule, using Fabric's own git-connection state
    3. perform the action through `fab`
    4. append exactly one ledger record, whether it succeeded or failed

Step 4 happens on failure too. A ledger that only records successes is a sales brochure,
not an audit trail.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, fabwrap, preflight, trail, verbs, workspace
from .ledger import Actor, Ledger


class Refused(Exception):
    """A guard stopped the command. Exit cleanly with guidance, not a traceback."""


def _ledger(root: Path) -> Ledger:
    return Ledger(root=root / "ledger")


def _git_connected(workspace_id: str) -> bool | None:
    """Ask Fabric whether this workspace is bound to git. None means we could not tell.

    Never inferred from the local profile: a stale profile is precisely how a direct
    write slips into a connected workspace.
    """
    try:
        result = fabwrap.api(f"workspaces/{workspace_id}/git/connection", check=False)
    except fabwrap.FabNotInstalled:
        return None
    if not result.ok:
        # 404 is the documented shape for "not connected"; anything else is unknown.
        if "404" in result.stderr or "NotFound" in result.stderr:
            return False
        return None
    parsed = result.parsed if isinstance(result.parsed, dict) else {}
    state = (parsed.get("gitConnectionState") or "").lower()
    if state:
        return state != "notconnected"
    return bool(parsed.get("gitProviderDetails"))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_preflight(args: argparse.Namespace) -> int:
    return preflight.run(args.profile, brief=args.brief)


def cmd_normalise(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(config.repo_root() / "tools"))
    from render.normalise import main as normalise_main  # noqa: PLC0415
    argv = [str(p) for p in (args.paths or [config.repo_root()])]
    if args.check:
        argv.append("--check")
    return normalise_main(argv)


def cmd_ledger_verify(args: argparse.Namespace) -> int:
    ledger = _ledger(config.repo_root())
    problems = ledger.verify()
    if not problems:
        count = sum(1 for _ in ledger.read_all())
        if not args.quiet:
            print(f"ledger intact: {count} record(s), hash chain verified")
        return 0
    print(f"LEDGER CHAIN BROKEN — {len(problems)} problem(s):", file=sys.stderr)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    print(
        "\nA broken chain means a record was altered or removed after it was written.\n"
        "Compare against the remote copy before doing anything else:\n"
        "    git log --oneline -- ledger/",
        file=sys.stderr,
    )
    return 1


def cmd_ledger_query(args: argparse.Namespace) -> int:
    ledger = _ledger(config.repo_root())
    records = list(ledger.read_all())
    if args.run_id:
        records = [r for r in records if r.get("run_id") == args.run_id]
    if args.action:
        records = [r for r in records if args.action in (r.get("action") or "")]
    if args.failed:
        records = [r for r in records if (r.get("result") or {}).get("status") != "succeeded"]

    if args.json:
        json.dump(records, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if not records:
        print("no matching records")
        return 0
    for r in records:
        actor = r.get("actor") or {}
        status = (r.get("result") or {}).get("status", "?")
        print(f"{r.get('ts','?'):<25} {status:<10} {r.get('action','?'):<34} "
              f"{actor.get('surface','?')}  {r.get('intent','')[:60]}")
    print(f"\n{len(records)} record(s)")
    print("For SQL over the full ledger:\n"
          "    duckdb -c \"SELECT * FROM read_json_auto('ledger/**/*.jsonl')\"")
    return 0


def cmd_sync_status(args: argparse.Namespace) -> int:
    root = config.repo_root()
    profile = config.load(args.profile, root=root)
    env = profile.env(args.env)
    print(f"{env.workspace}  (env {env.id}, branch {env.branch}, value set {env.value_set})")
    print("\nThis verb needs a resolved workspace id and a live Fabric call.")
    print("Workspace id resolution is pending verification V6; see")
    print("doc/framework/verification-register.md")
    return 0


def cmd_show_verbs(args: argparse.Namespace) -> int:
    by_class: dict[str, list[verbs.Verb]] = {}
    for verb in verbs.REGISTRY.values():
        by_class.setdefault(verb.cls.value, []).append(verb)

    for cls in ("READ", "QUERY", "EXECUTE", "DML", "DEFINITION", "SYNC"):
        items = by_class.get(cls, [])
        audited = "not audited" if cls in ("READ", "QUERY") else "audited"
        connected = {
            "READ": "allowed anywhere",
            "QUERY": "allowed anywhere; read-only data access",
            "EXECUTE": "allowed anywhere; runs something already committed and reviewed",
            "DML": "allowed anywhere, but ONLY via plan -> human approval -> apply",
            "DEFINITION": "REFUSED on git-connected workspaces",
            "SYNC": "the only door definitions come through",
        }[cls]
        print(f"\n{cls}  -- {audited}; {connected}")
        for verb in sorted(items, key=lambda v: v.name):
            flag = "  [--apply required]" if verb.dry_run_default else ""
            print(f"  {verb.name:<20} {verb.summary}{flag}")
    return 0


def cmd_scope(args: argparse.Namespace) -> int:
    """Show exactly which workspaces this repository may touch, and nothing else."""
    root = config.repo_root()
    profile = config.load(args.profile, root=root)
    try:
        lock = workspace.load(root, profile)
    except workspace.LockError as exc:
        print(f"no scope is defined yet.\n\n{exc}", file=sys.stderr)
        return 4

    print(f"profile {profile.name}   tenant {lock.tenant_id or '(not pinned)'}")
    print(f"locked {lock.generated_at} by {lock.generated_by}\n")
    print("This repository may touch these workspaces and no others:\n")

    gate_of = {e.id: e.gate for e in profile.environments}
    for key in sorted(lock.workspaces):
        w = lock.workspaces[key]
        if key == workspace.SCRATCH_KEY:
            note = "direct definition writes allowed; not git-connected; unaudited by design"
        else:
            gate = gate_of.get(key, "?")
            note = f"git-connected; definitions via sync only; gate={gate}"
        print(f"  {key:<10} {w.display_name}")
        print(f"  {'':<10} {w.id}")
        print(f"  {'':<10} {note}\n")

    print("Anything not listed above is refused before any call reaches Fabric.")
    print("To widen it: edit the profile, run `fabctl profile lock`, open a PR.")
    return 0


def cmd_profile_lock(args: argparse.Namespace) -> int:
    """Pin the workspaces this repo may touch. Run once, commit the result."""
    root = config.repo_root()
    profile = config.load(args.profile, root=root)

    previous = None
    try:
        previous = workspace.load(root, profile)
    except workspace.LockError:
        pass
    if previous and not args.refresh:
        print(
            f"already locked: {workspace.lock_path(root, profile.name)}\n"
            f"Re-resolving would silently follow a rename, which is the thing the lock "
            f"exists to prevent.\nPass --refresh to re-resolve deliberately, then review "
            f"the diff before committing.",
            file=sys.stderr,
        )
        return 3

    lock = workspace.resolve(profile, previous=previous)
    path = workspace.save(root, lock)

    print(f"pinned {len(lock.workspaces)} workspace(s) -> {path.relative_to(root)}\n")
    for key in sorted(lock.workspaces):
        w = lock.workspaces[key]
        print(f"  {key:<10} {w.display_name:<30} {w.id}")
    print(f"\ntenant {lock.tenant_id or '(not reported)'}")
    print("\nCommit this file. It is the allowlist: any workspace not listed is refused "
          "before a call reaches Fabric.")
    return 0


def cmd_trail(args: argparse.Namespace) -> int:
    """Read the decision history out of git."""
    root = config.repo_root()
    if args.summary:
        return trail.summary(root)
    return trail.render(
        root,
        limit=args.limit,
        agents_only=args.agents_only,
        with_ledger=args.with_ledger,
        since=args.since,
        path=args.path,
    )


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fabctl",
        description="Audited gateway between this repo and Microsoft Fabric.",
        epilog="Every mutation goes through here, because this is the only place the "
               "ledger can record it.",
    )
    parser.add_argument("--profile", help="topology profile (default: $FABCTL_PROFILE or poc)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("preflight", help="report what can be done right now")
    p.add_argument("--brief", action="store_true", help="one line, never blocks")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("normalise", help="canonicalise repo files")
    p.add_argument("paths", nargs="*", type=Path)
    p.add_argument("--check", action="store_true", help="report only, exit non-zero if dirty")
    p.set_defaults(func=cmd_normalise)

    p = sub.add_parser("verbs", help="show the verb registry and what each class may touch")
    p.set_defaults(func=cmd_show_verbs)

    p = sub.add_parser("scope", help="show which workspaces this repo may touch")
    p.set_defaults(func=cmd_scope)

    p = sub.add_parser("trail", help="the decision history: what changed, and why")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--agents-only", action="store_true",
                   help="only commits carrying an Agent-Run-Id trailer")
    p.add_argument("--with-ledger", action="store_true",
                   help="interleave what happened in Fabric on the same days")
    p.add_argument("--since", help="e.g. '2 weeks ago'")
    p.add_argument("--path", help="limit to a path")
    p.add_argument("--summary", action="store_true",
                   help="who changed this repo, and how much of it was an agent")
    p.set_defaults(func=cmd_trail)

    profile_p = sub.add_parser("profile", help="topology profile")
    profile_sub = profile_p.add_subparsers(dest="profile_command", required=True)
    p = profile_sub.add_parser("lock", help="pin declared workspaces to their ids")
    p.add_argument("--refresh", action="store_true",
                   help="re-resolve names after an intentional rename")
    p.set_defaults(func=cmd_profile_lock)

    ledger_p = sub.add_parser("ledger", help="audit ledger")
    ledger_sub = ledger_p.add_subparsers(dest="ledger_command", required=True)

    p = ledger_sub.add_parser("verify", help="walk the hash chain")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_ledger_verify)

    p = ledger_sub.add_parser("query", help="filter ledger records")
    p.add_argument("--run-id")
    p.add_argument("--action")
    p.add_argument("--failed", action="store_true", help="only non-succeeded records")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ledger_query)

    sync_p = sub.add_parser("sync", help="git <-> Fabric")
    sync_sub = sync_p.add_subparsers(dest="sync_command", required=True)
    p = sync_sub.add_parser("status", help="Fabric's git status for an environment")
    p.add_argument("--env", required=True)
    p.set_defaults(func=cmd_sync_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (Refused, verbs.VerbRefused, workspace.OutOfScope,
            workspace.BlastRadiusExceeded) as exc:
        print(f"\nrefused: {exc}\n", file=sys.stderr)
        return 3
    except (config.ConfigError, workspace.LockError, workspace.Unresolved) as exc:
        print(f"\nconfiguration error: {exc}\n", file=sys.stderr)
        return 4
    except fabwrap.FabNotInstalled as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 5
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
