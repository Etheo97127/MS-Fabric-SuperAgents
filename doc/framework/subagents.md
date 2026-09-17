# Subagents — which components need one, and why

Two separate reasons to reach for a subagent, often conflated:

1. **Context durability** — a subagent's instructions sit at position 0 of a short, fresh
   context that never grows long enough to be compacted. A rule stated once in a main
   session that has since been compacted twice is not reliably still there.
2. **Context economy** — a subagent's working context is discarded when it finishes. Only
   its final report reaches the caller, so a task that reads a great deal costs the main
   session only the conclusion.

Both are real. Neither is a substitute for putting a safeguard in code.

---

## The rule that comes first

> **If a safeguard can be code, make it code. A subagent's system prompt is still a prompt.**

A subagent makes a prompt-based rule *more durable*. It does not make it *enforced*. An
instruction saying "never DELETE without a WHERE clause" can be ignored by a model having a
bad turn; a `PreToolUse` hook that refuses the call cannot.

So before designing any subagent, the question is which of the framework's safeguards are
already code, and which genuinely cannot be:

| Safeguard | Where it lives | Subagent needed? |
|---|---|---|
| Only these workspaces are reachable | `workspace.Lock.assert_in_scope` | no — code |
| DEFINITION writes refused on git-connected workspaces | `verbs.check` | no — code |
| DML requires plan → approve → apply | `VerbClass.DML` + `dry_run_default` | no — code |
| No MCP mutations | `PreToolUse` deny hook | no — code |
| At most N items per run | `RunBudget` | no — code |
| Ledger records every action | `fabctl` chokepoint | no — code |
| *"Keep the 2019 partition, we still report on it"* | **nowhere** | **yes** |
| *"This mapping change looks wrong — stop and ask"* | **nowhere** | **yes** |
| *"Prefer a soft delete here; this table feeds finance"* | **nowhere** | **yes** |

The pattern in the bottom three: they are **session-specific or judgement-bearing**. They
cannot be pre-encoded because they depend on what this particular change is about, or on
something the user said an hour ago. Those are exactly what compaction erodes, and exactly
what a subagent's fresh context protects.

**So the subagent's job is to protect the judgement layer, not to replace the hard rules.**
Anything stated in a subagent prompt that *could* have been a hook is a bug in the design.

---

## Which components get a subagent

### `fabric-dml` — yes, strongest case

The most dangerous verb class meets the longest-lived constraints. A DML task needs to hold
schema, row counts, the plan output, and whatever the user said about this table — and it
runs at the end of a session, when the main context is most likely to have been compacted.

What its protected context carries that code cannot:

- the specific caution the user expressed about *this* table, this session
- what the plan's row count means in context (is 40,000 rows expected, or alarming?)
- the instruction to stop and report rather than proceed when the plan surprises it

Hard rules stay in code: `dml apply` is `VerbClass.DML`, defaults to dry-run, and a `DELETE`
or `UPDATE` without a `WHERE` is refused before the subagent's judgement is consulted at all.

### `fabric-reader` — yes, on economy grounds

Exploratory reading produces enormous output: schema dumps, row samples, ACL listings,
`git status` against a workspace. None of it belongs in the main session, and almost all of
it is scaffolding for one sentence of conclusion.

### `drift-reconciler` — yes, judgement over a large diff

When someone edits in the Fabric portal, the repo and workspace disagree. The diff can be
large, the decision (keep theirs / keep ours / merge) is genuine judgement, and the raw diff
has no business occupying the main context afterwards.

### Workspace auditor — **no subagent for the audit itself**

Per [ADR 0003](../adr/0003-agent-decomposition-and-hosting.md) the audit is a script: compare
ACLs against the approved set, report differences. No judgement, so no model.

A subagent may *wrap* it to summarise a long report — but that is `fabric-reader` doing what
it already does, not a separate auditor agent.

---

## The read pattern: discard from context, persist to disk

The instinct to throw away read results is right, with one correction that matters for this
framework: **discard them from *context*, not from *existence*.**

A subagent that reads 400 ACL entries and reports "3 unexpected principals" has just made
the other 397 unavailable to anyone. For audit work that is the wrong trade — the evidence
is the point.

So the pattern is:

```
1. fabctl <read> --out out/<run-id>/<step>.json     full output to disk, not to context
2. read only what answers the question                 grep, jq, head — never the whole file
3. report the conclusion plus the artefact path        caller gets the finding and the receipt
4. context is discarded when the subagent ends         automatic
```

The caller receives a short answer *and* a path. Nothing is lost, and the main context grows
by a sentence rather than a megabyte. This needs `--out` on the read verbs, which is not yet
implemented — noted in the register.

Two further rules for read subagents:

- **Never `cat` a file you only need three fields from.** Reading a 5,000-line JSON into
  context to answer "how many rows" wastes the mechanism entirely.
- **Report the path even on success.** An audit finding without a link to its evidence is an
  assertion, and this framework's whole claim is that assertions are checkable.

---

## Tool sets are part of the safeguard

A subagent's tool list is enforced by the harness, not by its prompt — which makes it the one
part of a subagent that *is* code.

| Subagent | Tools | Why |
|---|---|---|
| `fabric-reader` | `Bash`, `Read`, `Grep`, `Glob` | no `Edit`/`Write`: a reader that can write is a reader that can cause what it was meant to observe |
| `drift-reconciler` | `Bash`, `Read`, `Grep`, `Glob` | proposes; the main session applies |
| `fabric-dml` | `Bash`, `Read`, `Grep` | no `Write`: it must go through `fabctl`, which is where the ledger is |

Giving a read-only agent write tools "just in case" defeats the only hard guarantee a
subagent has.

---

## Do we need an orchestrator?

**No — not while a human is in the session.**

The main Claude Code session already *is* the orchestrator: it holds the user's intent,
decides what to delegate, and applies results. Inserting a separate orchestrator agent
between the user and the workers adds a summarisation hop — the user's intent reaches the
worker through two layers of paraphrase instead of one — for no capability gained.

An orchestrator earns its place in exactly one situation: **unattended runs, where nobody is
there to decide what happens next.** That is phase 7, and it needs the service principal
first.

And even then, the right shape is probably not an agent:

```bash
fabctl run nightly.playbook.yaml
```

A playbook is an ordered list of `fabctl` steps with conditions. Deterministic, testable,
auditable, no tokens. Reach for a model only where a step needs judgement — and then the
playbook calls a subagent for that step, rather than a model driving the whole run.

The honest test for whether you need an orchestrator agent: *can you write the sequence
down?* Nightly audit → reconcile → report is a sequence. "Figure out what maintenance this
workspace needs" is not — but nobody has asked for that, and it is a much larger commitment
than it first appears.

---

## What this does not fix

- **Compaction can still occur inside a long subagent run.** A subagent is a short context,
  not an unbounded one. Keep tasks narrow; that is also why the read pattern above pushes
  bulk to disk.
- **A subagent prompt is still a prompt.** Everything in the table at the top that is marked
  "code" must stay code. If a future safeguard is added only to a subagent prompt, it is one
  bad turn from being ignored.
- **Summarisation is lossy by construction.** The `--out` artefact is the mitigation, not the
  summary's accuracy.
