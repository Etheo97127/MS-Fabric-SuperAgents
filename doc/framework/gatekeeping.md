# Gatekeeping — what stops an agent touching the wrong thing

Five independent gates. Each answers a different question, and each is code rather than
instruction, so none depends on an agent choosing to comply.

```
  request
     |
  [1] which tenant?      lock.assert_tenant()      wrong tenant -> refuse
     |
  [2] which workspace?   lock.assert_in_scope()    not pinned    -> refuse
     |
  [3] what kind of act?  verbs.lookup()            unclassified  -> refuse
     |
  [4] may it go there?   verbs.check()             DEFINITION on git-connected -> refuse
     |
  [5] how much?         RunBudget.charge()         over blast radius -> refuse
     |
  perform, then append exactly one ledger record (success or failure)
```

---

## The gap this closes

`fab` authenticates as a person, and that person can usually see every workspace in the
tenant. Gates 3 and 4 decide *how* a workspace may change, and the profile names the
workspaces the project uses — but until gate 2 existed, nothing stopped an agent targeting
a workspace the profile never mentioned. The effective blast radius was "everything the
signed-in human can reach", which is not a boundary.

This is not hypothetical here. The signed-in identity on this machine can see:

```
FUAM-test-accent                     296df62e-...
Microsoft Fabric Capacity Metrics    0e76f975-...
Retail Analytics Dev                 e11a0c22-...      <- ours
dim-store-test                       083d3330-...
iq-ontology-shortcut-test            81f66cc1-...
```

One of those belongs to this project. A typo, a hallucinated name, or an over-broad glob
could have reached any of the others.

---

## Gate 1 — tenant

`lock.assert_tenant(current)` refuses when the signed-in tenant differs from the one
recorded in the lock.

Guards the case where somebody is signed into a client or personal tenant and runs a command
meant for the work tenant. Without it every other gate still "passes", because the pinned
IDs simply do not exist there, and the resulting error looks like a typo rather than what it
is. An unknown tenant is treated as unknown, not as wrong — `fab auth status` does not
always report one, and blocking all work on absent evidence is its own failure.

## Gate 2 — workspace scope

`fabctl profile lock` resolves each workspace named in the profile to an ID **once** and
writes `profiles/<name>.lock.yaml`, which is committed. Every later operation resolves
through the recorded ID. `lock.assert_in_scope(id)` refuses anything not pinned.

Three failure modes this closes, in increasing order of quietness:

1. A typo or hallucinated name resolving to somebody else's workspace.
2. Two workspaces sharing a display name, so name resolution picks one by ordering luck.
   `resolve()` treats ambiguity as fatal rather than guessing.
3. A workspace renamed, or deleted and recreated, so a name that once meant dev now means
   something else. `assert_in_scope` also compares the live display name against the pinned
   one and refuses on mismatch — the ID matching is not enough, because IDs get reused.

Widening scope is deliberately awkward: edit the profile, re-run the lock, and open a PR.
The diff *is* the approval record.

`fabctl profile lock` refuses to create missing workspaces. Choosing which workspaces a repo
may touch is a human decision, and a lock that silently created its own targets would record
a decision nobody made.

```bash
fabctl scope     # what this repo may touch, and nothing else
```

## Gate 3 — verb classification

Every verb is registered in `tools/fabctl/verbs.py` with one of four classes. An
unregistered verb is refused outright rather than defaulting to permissive, so a new verb
cannot reach Fabric unclassified by omission.

## Gate 4 — the write-path rule

`DEFINITION`-class verbs are refused against a git-connected workspace, because a direct
definition write there becomes an uncommitted change that the next `updateFromGit` conflicts
with or reverts — damage that surfaces later and elsewhere. Connection state comes from
Fabric, never from the profile: a stale profile is exactly how drift gets in.

`EXECUTE` is allowed on connected workspaces, deliberately. Running a notebook changes data,
not definitions, produces no git diff, and is what you need immediately after a sync.

## Gate 5 — blast radius

`RunBudget` caps how many distinct items one run may mutate (`blast_radius`, default 10).
Per run rather than per command, because the damage comes from repetition. Touching the same
item repeatedly costs one.

---

## What these gates do *not* cover

Named plainly, because a gate list that implies completeness is worse than none:

- **The scratch workspace is outside gates 4 and 5 by design.** It exists so unreviewed
  direct writes are possible. "Audited" describes the path to dev and above, never the whole
  system. The TTL reaper and the export → normalise → PR promotion rule are what stop it
  becoming a shadow production environment.
- **Reads are not audited.** A read that informed an action is captured in that action's run
  context; free-standing read records would bury the ledger. Reads *are* still scope-checked,
  because a read is how an agent would discover a target it should not touch.
- **Gates protect against error, not a determined operator.** Everything here lives in a repo
  the agent can write. `.claude/settings.json` and `profiles/*.lock.yaml` belong in CODEOWNERS
  with human review required, and CI should assert their digests. Real non-repudiation needs
  an identity the agent does not control.
- **Item-level permission inside a workspace is Fabric's, not ours.** If an identity has
  Contributor on a workspace, these gates do not further restrict which items it may change
  beyond the blast radius cap.
