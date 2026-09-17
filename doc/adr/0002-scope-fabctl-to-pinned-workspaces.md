# ADR 0002 — Scope fabctl to a pinned set of workspaces

Date: 2026-09-17
Status: accepted

## Context

`fab auth login` authenticates a *person*, not a project. There is no way to scope a Fabric
CLI session to particular workspaces — the token carries whatever access the identity has,
and every `fab` command can reach all of it.

On this machine that is not abstract. The signed-in identity
(`accentfabricadmin@accentconsult.onmicrosoft.com`) can see:

```
FUAM-test-accent                     296df62e-ebc7-43be-bd41-3005385809e8
Microsoft Fabric Capacity Metrics    0e76f975-c610-48b2-90bb-6824e4cd2023
Retail Analytics Dev                 e11a0c22-b7f4-4a2e-8700-08aeadcdc513   <- ours
dim-store-test                       083d3330-4ef2-4502-b512-f20808d1c751
iq-ontology-shortcut-test            81f66cc1-2be9-4996-948e-711c2f91f307
```

One belongs to this project. Before this decision, the framework's verb classes governed
*how* a workspace could change but nothing governed *which* workspaces were reachable, so
the effective blast radius of any agent error was every workspace above.

Three ways that goes wrong, in increasing order of quietness:

1. A typo or hallucinated name resolving to somebody else's workspace.
2. Two workspaces sharing a display name — name resolution picks one by ordering luck.
3. A workspace renamed, or deleted and recreated. A name that meant `dev` last week now
   means something else, and nothing about a name lookup notices.

## Decision

**`fabctl` acts only on workspaces pinned by ID in a committed lock file.**

`fabctl profile lock` resolves each workspace named in the active profile to its ID once and
writes `profiles/<name>.lock.yaml`. Every subsequent operation resolves through the recorded
ID. Any workspace not in the lock is refused before a call reaches Fabric.

Four properties follow, and each is deliberate:

- **Resolution happens once, not per operation.** Name lookup at operation time is what makes
  a rename dangerous. Pinning removes the lookup from the hot path entirely.
- **The lock is committed.** Changing what this repo may touch is a reviewable diff, and that
  diff is the approval record.
- **Renames are refused, not followed.** `assert_in_scope` compares the live display name
  against the pinned one. Matching IDs is not enough, because Fabric reuses IDs; a name that
  no longer matches means either a rename or a reused ID, and both need a human.
- **`fabctl profile lock` will not create missing workspaces.** Choosing which workspaces a
  repository may touch is a human decision. A lock that created its own targets would record
  a decision nobody made.

Ambiguity is fatal rather than resolved. Two workspaces sharing a display name is exactly the
situation that makes name-based targeting unsafe, so `resolve()` refuses and asks a human to
pin an ID.

A tenant ID is recorded alongside, and a session in a different tenant is refused. Without
that check every other guard still "passes" — the pinned IDs simply do not exist in the other
tenant, and the resulting error reads like a typo rather than like being in the wrong place.

## What this is not

**It is not a security boundary, and should never be described as one.** The `fab` session
retains full access to every workspace the identity can reach; the framework simply declines
to use it. Anyone can run `fab` directly and bypass all of this — the `guard_raw_fab` hook
warns, but a warning is not a wall, and the lock file lives in a repo the agent can write.

It is a guardrail against *error*: typos, hallucinated names, over-broad globs, stale
assumptions, renames nobody announced. Those are the realistic failure modes of an autonomous
agent, and they are the ones this stops.

Real containment needs a narrower identity, not a smarter tool: a service principal granted
Contributor on exactly the project workspaces and nothing else. When the SP arrives, the lock
becomes defence in depth rather than the only line — the SP cannot reach `dim-store-test` at
all, and the lock stops it targeting the wrong project workspace.

## Consequences

- A fresh clone cannot touch Fabric until someone runs `fabctl profile lock`. `preflight`
  reports this as a blocker rather than a warning, because "no allowlist" is not a degraded
  state — it is an unbounded one.
- Adding an environment to a profile requires re-locking. `workspace.load` refuses a lock
  missing a declared environment, so a new environment cannot silently arrive ungated.
- A deliberate rename costs a `--refresh` and a reviewed diff. That friction is the feature.
- `fabctl scope` prints the current allowlist, so "what can this repo touch" is one command
  rather than an inference across three files.

## Alternatives considered

| Option | Why not |
|---|---|
| Resolve names at operation time, validate against the profile | Still follows renames, still ambiguous on duplicate names — the two quietest failure modes survive |
| Store IDs inline in `profiles/*.yaml` | Conflates the topology a human authors with the IDs a tool resolves; the lock's `generated_by` / `generated_at` provenance is lost |
| Rely on Fabric permissions alone | Correct long-term, but needs the service principal, which is still pending. Does nothing today |
| Warn instead of refuse | A warning an agent can proceed past is not a gate. The point is that the wrong workspace is unreachable, not discouraged |
