"""Verb classes and the write-path rule.

Every fabctl verb belongs to exactly one class, and the class decides two things
mechanically — with no agent discretion:

  * whether the action is written to the ledger, and
  * whether it may target a git-connected workspace.

The rule this encodes:

    A git-connected workspace has exactly one writer, and that writer is git.

Writing a definition directly into a git-connected workspace does not fail loudly. Fabric
records it as an uncommitted change and the next updateFromGit either conflicts or reverts
it, so the damage surfaces later and somewhere else. Refusing here is the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VerbClass(str, Enum):
    READ = "READ"              # no side effect; not audited
    QUERY = "QUERY"            # read-only data access; not audited
    EXECUTE = "EXECUTE"        # runs something already reviewed and committed; audited
    DML = "DML"                # mutates data directly; audited; plan -> approve -> apply
    DEFINITION = "DEFINITION"  # changes an item definition; audited; git-connected = refused
    SYNC = "SYNC"              # the only door definitions come through; audited


@dataclass(frozen=True)
class Verb:
    name: str
    cls: VerbClass
    summary: str
    #: Above this environment's gate, --apply is required rather than assumed.
    dry_run_default: bool = False


#: The single source of truth. Adding a verb without adding it here is a hard error,
#: which is deliberate — an unclassified verb would slip past the gate silently.
REGISTRY: dict[str, Verb] = {v.name: v for v in [
    # --- READ ---------------------------------------------------------------
    Verb("preflight",      VerbClass.READ, "report auth, profile, git and gate state"),
    Verb("ls",             VerbClass.READ, "list workspaces, items or folders"),
    Verb("get",            VerbClass.READ, "fetch one item or its definition"),
    Verb("sync status",    VerbClass.READ, "show Fabric's git status for a workspace"),
    Verb("verify contract", VerbClass.READ, "compare a live table against its contract"),
    Verb("verify stage",   VerbClass.READ, "reconcile bronze/silver/gold coherence"),
    Verb("verify env",     VerbClass.READ, "diff one table across two environments"),
    Verb("ledger verify",  VerbClass.READ, "walk the hash chain and report breaks"),
    Verb("ledger query",   VerbClass.READ, "query the ledger"),
    Verb("trail",          VerbClass.READ, "read the decision history out of git"),
    Verb("mapping plan",   VerbClass.READ, "dry-run a mapping: schema delta and code diff"),
    Verb("normalise",      VerbClass.READ, "canonicalise repo files in place"),

    # --- QUERY --------------------------------------------------------------
    # Read-only data access. Unaudited for the same reason READ is: a query that informed
    # an action is captured in that action's run context, and logging every SELECT would
    # bury the records that matter.
    Verb("query",          VerbClass.QUERY, "run a read-only SQL or Spark query"),
    Verb("table describe", VerbClass.QUERY, "inspect a table's schema and statistics"),

    # --- EXECUTE ------------------------------------------------------------
    # Runs something that is already committed and reviewed. Allowed on git-connected
    # workspaces: it mutates data, produces no git diff, and is what you need right after
    # a sync. The review already happened, in the PR that landed the notebook.
    Verb("job run",        VerbClass.EXECUTE, "run a committed notebook, pipeline or job"),
    Verb("onelake put",    VerbClass.EXECUTE, "upload a data file to OneLake"),

    # --- DML ----------------------------------------------------------------
    # Mutates data directly, from a statement that may have been generated moments ago
    # rather than reviewed in a PR. Never applied without a plan a human has seen: the
    # plan reports how many rows the predicate matches, which is the only thing that
    # distinguishes an intended DELETE from a catastrophic one before it runs.
    Verb("dml plan",       VerbClass.QUERY, "count rows a statement would affect; mutates nothing"),
    Verb("dml apply",      VerbClass.DML, "execute a planned DML statement", dry_run_default=True),
    Verb("security apply", VerbClass.DML, "apply security/*.sql (RLS, CLS, GRANT/DENY)",
         dry_run_default=True),
    Verb("table write",    VerbClass.DML, "write to a lakehouse table", dry_run_default=True),

    # --- DEFINITION ---------------------------------------------------------
    Verb("item import",    VerbClass.DEFINITION, "write an item definition into a workspace",
         dry_run_default=True),
    Verb("item create",    VerbClass.DEFINITION, "create an item", dry_run_default=True),
    Verb("item delete",    VerbClass.DEFINITION, "delete an item", dry_run_default=True),
    Verb("item rename",    VerbClass.DEFINITION, "rename an item", dry_run_default=True),
    Verb("workspace create", VerbClass.DEFINITION, "create a workspace", dry_run_default=True),

    # --- SYNC ---------------------------------------------------------------
    Verb("sync connect",   VerbClass.SYNC, "bind a workspace to a git branch",
         dry_run_default=True),
    Verb("sync push",      VerbClass.SYNC, "updateFromGit: repo -> workspace"),
    Verb("sync pull",      VerbClass.SYNC, "commitToGit: workspace -> repo (exceptional)",
         dry_run_default=True),
    Verb("promote",        VerbClass.SYNC, "open a promotion PR between environments",
         dry_run_default=True),
]}


class VerbRefused(Exception):
    """A verb was refused by the write-path rule. The message is the guidance."""


def lookup(name: str) -> Verb:
    try:
        return REGISTRY[name]
    except KeyError:
        raise VerbRefused(
            f"'{name}' is not a registered fabctl verb. Every verb must be declared in "
            f"tools/fabctl/verbs.py with a class, so that nothing reaches Fabric "
            f"unclassified."
        ) from None


#: Classes with no side effect. Not audited: a read or query that informed an action is
#: captured in that action's run context, which is more useful than a free-standing record,
#: and it keeps the ledger to a size a human will actually review.
UNAUDITED = frozenset({VerbClass.READ, VerbClass.QUERY})


def is_audited(verb: Verb) -> bool:
    return verb.cls not in UNAUDITED


def requires_plan(verb: Verb) -> bool:
    """DML is never applied without a plan a human has seen.

    Auditing a DELETE after it ran is a post-mortem, not a control.
    """
    return verb.cls is VerbClass.DML


def check(
    verb: Verb,
    *,
    workspace: str,
    git_connected: bool,
    env_id: str | None = None,
    scratch_workspace: str | None = None,
) -> None:
    """Raise VerbRefused if this verb may not touch this workspace.

    ``git_connected`` must come from Fabric (GET /workspaces/{id}/git/connection), never
    from a local guess — a stale profile is exactly how drift gets introduced.
    """
    if verb.cls is not VerbClass.DEFINITION:
        return
    if not git_connected:
        return

    hint_scratch = (
        f"\n  or iterate in {scratch_workspace}, then: fabctl promote --from scratch"
        if scratch_workspace
        else ""
    )
    env_flag = f" --env {env_id}" if env_id else ""
    raise VerbRefused(
        f"{workspace} is git-connected, so '{verb.name}' is refused.\n"
        f"A direct definition write there becomes an uncommitted change in Fabric that "
        f"the next sync will conflict with or revert.\n"
        f"  -> edit the file under fabric/\n"
        f"  -> fabctl commit && fabctl sync push{env_flag}"
        f"{hint_scratch}"
    )


def requires_apply(verb: Verb, *, env_gate: str) -> bool:
    """True when the caller must pass --apply rather than relying on the default.

    Dry-run is the default for destructive verbs anywhere, and for every mutating verb
    outside an ungated environment.
    """
    if verb.cls is VerbClass.READ:
        return False
    if verb.dry_run_default:
        return True
    return env_gate != "none"
