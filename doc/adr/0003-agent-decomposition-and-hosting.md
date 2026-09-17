# ADR 0003 — Agent decomposition and where each piece runs

Date: 2026-09-17
Status: accepted

## Context

Three automations were proposed as separate agents: a **git manager**, a **workspace
auditor**, and a **data query / DML agent**. The question was how to implement them and
whether they belong in Claude, in the cloud, or in Azure.

## Decision 1 — Most of this is not an agent, and should not be

The sharpest thing to say first: **an LLM is only warranted where judgement is genuinely
required.** Everywhere else it costs tokens, introduces non-determinism, and — worst for
this framework — becomes another thing that needs auditing.

Applying that test to the three proposals:

| Proposed | What it actually is | Verdict |
|---|---|---|
| Git manager | sync on merge, tag versions, poll an LRO | **~95% deterministic.** A CI job. The only judgement is reconciling portal drift |
| Workspace auditor | compare ACLs, diff ledger against activity log, flag unknown principals | **100% deterministic.** A scheduled script. An LLM adds only prose |
| Data query / DML | intent to SQL, inspect schema, iterate, mutate data | **Genuinely agentic** — and the most dangerous of the three |

So the shape is a ladder, not three peers:

**Tier 0 — deterministic, no model in the loop.** `fabctl` subcommands. Anything expressible
as "compare these two things and report differences" lives here: `fabctl audit access`,
`fabctl verify contract|stage|env`, `fabctl ledger reconcile`, `fabctl sync push`. They are
testable, cheap, run headless, and produce the same answer twice.

**Tier 1 — model for judgement, `fabctl` for action.** The model decides *what* to do and
`fabctl` does it, so every effect is still audited. Portal-drift reconciliation, authoring a
contract from an observed schema, explaining why an audit finding matters. These are Claude
Code subagents with a read-only tool set plus `fabctl`.

**Tier 2 — genuinely agentic, hardest guardrails.** Data query and DML. See decision 3.

A useful rule when the boundary is unclear: **if you can write the assertion, write the
script.** "No principal outside the approved list holds Contributor" is an assertion. "Is
this ACL drift concerning?" is judgement.

## Decision 2 — Hosting is gated on the service principal, not on preference

Nothing runs unattended anywhere without a non-interactive identity. Every candidate host —
CI, Azure Functions, Container Apps, scheduled Fabric notebooks — needs one. `fab auth login`
is interactive and its token is per-user and local.

So the sequencing is forced rather than chosen:

| Phase | Host | What runs there |
|---|---|---|
| **Now** (no SP) | Claude Code, local, human-triggered | everything; Tier 0 as commands a person runs |
| **With SP** | **Azure DevOps Pipelines / GitHub Actions** | Tier 0 on merge: sync, verify, contract-drift, ledger chain |
| **With SP** | **Azure Container Apps Jobs** (cron) | Tier 0 on a schedule: access audit, activity-log reconciliation, ledger mirror, scratch reaper |
| **Later** | Claude Agent SDK, headless, same container | Tier 1 where a scheduled job needs judgement |

**Why Container Apps Jobs rather than Functions:** the workload is a Python CLI that runs for
a minute and exits. A job is exactly that shape — a container, a cron expression, a managed
identity, and no function runtime to model the work around. Functions suit event-driven HTTP;
this is neither.

**Why not Fabric notebooks for the auditor:** auditing Fabric access from inside Fabric means
the auditor's own access is part of what it audits, and a workspace that loses the auditor's
permissions also loses the report that would have told you. Data reconciliation is fine in a
notebook — it needs Spark and it is auditing *data*, not *access*. Access auditing belongs
outside.

**Why not a hosted "cloud agent" for Tier 0 at all:** these jobs are a few hundred lines of
Python against a CLI. Putting a model in front of that would add cost and non-determinism to
work that is already correct.

## Decision 3 — The DML agent gets its own verb class and a plan/approve/apply cycle

This is the one proposal that genuinely needs a model, and it is also the one that can cause
unrecoverable harm. `EXECUTE` as currently defined ("changes data, not definitions") is too
coarse: "run this reviewed notebook" and "run this DELETE I just wrote" are not the same risk
and must not share a class.

Split it:

| Class | Example | Gate |
|---|---|---|
| `QUERY` | `SELECT`, schema inspection, row counts | none — read-only, unaudited like other reads |
| `EXECUTE` | run a committed, reviewed notebook or pipeline | audited, allowed anywhere |
| `DML` | `INSERT` / `UPDATE` / `DELETE` / `MERGE`, `security/*.sql` | audited; **plan → human approve → apply**, always |

The cycle for `DML`:

1. **Generate** — the model writes the statement.
2. **Plan** — `fabctl dml plan` runs the *predicate* as a `SELECT COUNT(*)` and reports how
   many rows would be affected, against which table, in which environment. No mutation.
3. **Approve** — a human sees the statement and the row count together. Above dev this is a
   PR; in dev it is an interactive confirmation.
4. **Apply** — with `--apply`, recording statement, plan count, actual count and the Delta
   version before and after.
5. **Recover** — the pre-change Delta version in the ledger makes `RESTORE TABLE ... VERSION
   AS OF` possible. Delta time travel is the safety net, and recording the version is what
   makes it usable under pressure.

Refuse outright: any `DELETE` or `UPDATE` without a `WHERE`, and any DML against prod that
did not come from a merged PR. Both are cheap to detect and neither has a legitimate agent
use.

Layer 4 access control (`security/*.sql` — RLS, CLS, GRANT/DENY) is `DML` class for the same
reason: a wrong predicate changes who can see what, silently, in either direction. See
[access-control.md](../framework/access-control.md).

## Consequences

- `verbs.py` gains `QUERY` and `DML` classes. `EXECUTE` narrows to "run something already
  reviewed and committed", which is a more honest description of what it always meant.
- Tier 0 work is written as `fabctl` subcommands from the start, never as agent prompts, so
  the same code serves the local human today and the scheduled job later. Nothing is rewritten
  when the SP arrives — only where it is invoked from changes.
- Scheduled jobs need the ledger writable from CI. That is also what finally makes the ledger
  tamper-*resistant* rather than merely tamper-evident: records written by an identity the
  local agent does not control.
- Subagents get a deliberately narrow tool set. A workspace auditor with write tools is a
  workspace auditor that can cause the thing it is meant to detect.

## Alternatives considered

| Option | Why not |
|---|---|
| Three long-running agents, one per concern | Long-running agents need a model in the loop continuously for work that is mostly assertions. Cost and non-determinism for no gain |
| Put everything in Fabric notebooks | Access auditing from inside the thing being audited; also drags Spark into jobs that are HTTP calls |
| Azure Functions | Wrong shape for a CLI that runs and exits; Container Apps Jobs models it directly |
| Let the DML agent execute directly, audit after | Auditing a `DELETE` after it ran is a post-mortem, not a control. The plan step is the entire value |
| Keep DML inside `EXECUTE` | Conflates a reviewed notebook with a freshly generated mutation. The gate has to differ because the risk differs |
