# MS-Fabric-SuperAgents

A framework for agents to author, review, sync, document and audit Microsoft Fabric
artifacts — with every action recoverable from git alone.

**Status: foundations built and tested, Fabric write path not yet wired.**
82 tests passing · ~3,700 lines of Python · nothing committed to a Fabric workspace yet.

Start here: **[AGENTS.md](AGENTS.md)** · design: **[doc/framework/](doc/framework/fabric-agent-framework.md)**

---

## The invariant

> Nothing mutates Fabric except through `fabctl`.
> Nothing mutates the repo except through `git`.
> Every `fabctl` invocation appends exactly one ledger record.
>
> Therefore **repo history + ledger = a complete account of every agent action.**

Enforced by hooks and verb classes, not by asking agents nicely.

---

## What works today

| Capability | State |
|---|---|
| **`fabctl preflight`** — auth, profile, scope, hooks, MCP parity, pre-commit, ledger | working |
| **`fabctl scope`** — which workspaces this repo may touch, and no others | working |
| **`fabctl profile lock`** — resolve declared workspaces to pinned IDs, live against Fabric | working |
| **`fabctl verbs`** — 28 verbs in 6 classes, with what each may touch | working |
| **`fabctl ledger verify \| query`** — hash-chained JSONL, tamper detection | working |
| **`fabctl normalise`** — deterministic canonicalisation, pre-commit + CI | working |
| **Workspace scope enforcement** — Bash guard refuses any unpinned workspace, fails closed | working |
| **Per-turn boundary injection** — ~113 tokens, survives compaction | working |
| **`fabric-gatekeeper`** subagent — judgement over the deterministic checks | active |
| **`fabctl sync push/pull`**, `item import`, `dml plan/apply` | **stubs** — see below |

### The five gates

Each answers a different question, each is code:

```
[1] tenant      wrong tenant?                → refuse
[2] workspace   not in the lock?             → refuse   (fails closed: no lock = nothing in scope)
[3] verb class  unregistered?                → refuse
[4] write path  DEFINITION on git-connected? → refuse
[5] blast radius over the cap?               → refuse
```

Details: [gatekeeping.md](doc/framework/gatekeeping.md). Why workspaces are pinned:
[ADR 0002](doc/adr/0002-scope-fabctl-to-pinned-workspaces.md).

### Decisions made

- **Core MCP: not used at all** — `fab` covers everything it does and more, including git
  integration and job execution, which Core MCP does not expose.
- **Local MCP: knowledge only**, `--namespace docs --read-only` — 1 tool, ~340 tokens/turn,
  measured. The documented default exposes zero tools.
- **No Microsoft Graph MCP** — Fabric returns UPNs natively; object IDs are looked up by hand
  at grant time and recorded in the PR ([ADR 0001](doc/adr/0001-no-microsoft-graph-mcp.md)).
- **Most automation is not an agent** — auditors and sync jobs are scripts
  ([ADR 0003](doc/adr/0003-agent-decomposition-and-hosting.md)).

---

## Roadmap

Phases are ordered by dependency, not priority. **Only phase 5 needs the service principal.**

### Phase 0 — unblock the lock *(next, ~1 hour)*

`Retail Analytics Prod` and the scratch workspace don't exist, so the lock can't complete —
and until it does, every workspace-targeted command is denied. Either create them or trim the
profile to `dev` only.

Then settle the two register items that can invalidate design:

- **V3** — does Fabric's `directoryName` leave sibling directories alone? *Highest blast
  radius; if wrong the repo layout changes.*
- **V1** — does `fab export` match what `commitToGit` writes? *The normaliser calibrates
  against it.*

### Phase 1 — the write path

`fabctl sync push/pull/connect` and `item import`. The guards and classification are built and
tested; only the `fab` invocation is missing. Needs **V2** (does `fab api` poll long-running
operations?) settled first — guessing wrong means the ledger records successes that didn't
happen.

Then: connect a scratch workspace to git and run the repo → Fabric loop end to end.

### Phase 2 — notebooks and config

Normaliser calibration against Fabric's real serialization. Variable Library as the config
mechanism. The sentinel-region pattern that lets agents regenerate mapping code without
destroying hand-written logic.

### Phase 3 — contracts

The parameterised data architecture: entity and mapping YAML (schema written), `mapping apply`,
deterministic xlsx rendering. This is where the framework starts paying for itself — a
human-reviewed mapping becomes lakehouse reality through one audited path.

### Phase 4 — verification and DML

`data-reconcile` (contract, cross-stage, cross-environment). `dml plan/apply` with the
plan → approve → apply cycle, and `security/*.sql` for row- and column-level security —
the largest unbuilt piece of the access story ([access-control.md](doc/framework/access-control.md)).

Activate the `fabric-dml` and `drift-reconciler` drafts.

### Phase 5 — unattended *(needs the service principal)*

Lakehouse ledger mirror. Activity-log reconciliation — proving nothing was missed, not just
hoping. CI on merge; scheduled jobs on Azure Container Apps. This is also when the ledger
becomes tamper-*resistant* rather than merely tamper-evident, because records get written by
an identity the local agent doesn't control.

The admin ask is in [section 8 of the framework doc](doc/framework/fabric-agent-framework.md).

---

## Known limitations

Stated plainly, because a framework that oversells its guarantees is worse than one that
doesn't have them:

- **The ledger is tamper-evident, not tamper-proof.** Hash chaining plus an append-only CI
  check makes quiet tampering impossible and deliberate tampering visible. It does not stop
  someone who can force-push.
- **Scope pinning is a guardrail, not a security boundary.** The signed-in account is a
  tenant admin; `fabctl` declines to use that reach, but the session still has it. Real
  containment needs the service principal.
- **The scratch workspace is a governance hole by construction** — that is its purpose.
  "Audited" describes the path to dev and above, never the whole system.
- **9 register items remain open.** Three were resolved against the live tenant; the rest are
  documented assumptions, not verified behaviour.

---

## Getting started

```bash
pip install ms-fabric-cli pre-commit
fab auth login          # PowerShell or cmd — fails under Git Bash
pre-commit install
export PYTHONPATH=tools
python -m fabctl preflight
```

`preflight` names every blocker and the command that clears it.
