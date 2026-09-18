# Verification register

Everything the framework assumes but has not observed, and everything deliberately left
unbuilt because building it would mean guessing.

**How to use this.** Before relying on any behaviour listed here, check its status. When you
establish the real answer, update the row, record the evidence, and — if it changes the
design — amend `fabric-agent-framework.md` and say so in the Resolution column. This file
is how the framework stops being a set of plausible assumptions.

Status values: `open` · `in progress` · `resolved` · `invalidated` (assumption was wrong and
the design changed).

Last updated: 2026-09-17. `fab` 1.7.0 installed and signed in; V6 and V8 are resolved
against the live tenant, V9 is resolved from the API reference.

---

## A. Assumptions to verify

Each needs a real Fabric call. None can be settled from documentation — that is precisely
why they are here.

### V1 — Does `fab export` match what `commitToGit` writes?
**Confidence 70% · Priority 1 · Blocks: normaliser calibration, sentinel drift check**

The normaliser plan treats `fab export` as an oracle for Fabric's canonical serialization.
`export` calls Get Item Definition; `commitToGit` uses Fabric's git serializer. They are
probably the same pipeline, but "probably" is carrying the entire Layer 2/3 design.

```bash
# in a scratch workspace, on a throwaway branch
fab export <notebook> -o ./oracle-export/
fabctl sync pull --env scratch          # commitToGit
diff -r ./oracle-export/ ./fabric/<notebook>.Notebook/
```

*If they match:* calibrate the normaliser against `fab export`, which is fast and needs no
git round-trip. Populate `CALIBRATION_PENDING` in `tools/render/normalise.py` with real
rules.
*If they differ:* calibration must use a real `commitToGit` round-trip on a scratch branch.
Slower, still works, but the CI drift check becomes a scheduled job rather than a
per-commit one.

---

### V2 — Does `fab api` poll long-running operations, or return the raw 202?
**Confidence 60% · Priority 1 · Blocks: every sync verb's ledger `result`**

If `fab api` polls internally and we also poll, we waste calls but stay correct. If it does
*not* poll and we assume it does, every sync appears to succeed instantly and silently
isn't finished — and the ledger records a success that did not happen. That is the worst
failure mode in the system: a trustworthy-looking false record.

```bash
fab api workspaces/<id>/git/updateFromGit -X post -i body.json --show_headers
# Look for: HTTP status, x-ms-operation-id, Retry-After, and whether the call blocks
```

Quarantined in `fabwrap.looks_like_lro()` and `fabwrap.poll()`. Written to be correct
either way, but untested against reality.

---

### V3 — Does Fabric's `directoryName` leave sibling directories untouched?
**Confidence 85% · Priority 1 · Blocks: the entire repo layout**

The layout puts `contracts/`, `ledger/`, `tools/` and `doc/` alongside `fabric/` in one
repo, relying on Fabric managing only the directory named at connect time. Highest blast
radius of anything here, and the cheapest to test.

```bash
# connect a scratch workspace with directoryName = /fabric
echo "canary" > ledger/CANARY.txt && git commit && git push
fabctl sync push --env scratch
fabctl sync pull --env scratch
test -f ledger/CANARY.txt    # must still exist, unmodified
```

*If wrong:* framework files move to a separate repo from `fabric/`, and atomic PRs spanning
code and contracts are lost. Redesign, not a patch.

---

### V4 — Does a git-connected workspace flag or block a direct definition write?
**Confidence 80% · Priority 2 · Affects: whether our guard is load-bearing**

The design assumes Fabric *flags* it as an uncommitted change and enforces the rule in
`fabctl`. Safe either way; if Fabric actually blocks, our guard is belt-and-braces.

```bash
fab import <item> -i ./def.json     # against a git-connected scratch workspace
fab api workspaces/<id>/git/status  # does the change appear as uncommitted?
```

---

### V5 — Do VS Code Copilot hooks fire on MCP tool calls?
**Confidence 70% · Priority 2 · Affects: Copilot-side audit completeness**

Explicitly undocumented. Claude Code is unaffected.

Test: configure the deny hook, enable `chat.useCustomAgentHooks`, and attempt an MCP
mutation from Copilot.

*If they do not fire:* wrap the stdio Local MCP server in a JSON-RPC passthrough proxy that
logs every `tools/call`. Host-independent, roughly 80 lines, and it also covers V5 for any
future host.

---

### V6 — Workspace ID resolution — **RESOLVED 2026-09-17**
**Was 85% · Implemented in `workspace.resolve()`**

`fab api workspaces` works and `fabctl profile lock` now uses it. Verified live: it
resolved `Retail Analytics Dev` to `e11a0c22-b7f4-4a2e-8700-08aeadcdc513` and correctly
refused to proceed on the two profile workspaces that do not exist.

**Response envelopes — the trap this exposed.** `fab` wraps API responses, and the shape
depends on the output format. Neither is documented; both were found by running it.

*text mode:*
```json
{"status_code": 200, "text": {"value": [...]}}
```
*json mode (`--output_format json`, which fabwrap always passes):*
```json
{"timestamp": "...", "status": "Success", "command": "api",
 "result": {"data": [{"status_code": 200, "text": {"value": [...]}}]}}
```

Three consequences, all now handled in `fabwrap._unwrap()`:

- A `-q` JMESPath must address the envelope (`text.value[]`, not `value[]`). Querying the
  wrong path prints `None` rather than erroring — wrong, and quietly so.
- **The HTTP status is in the body, not the exit code.** `fab api` was observed exiting
  non-zero on a 200. `FabResult.ok` now trusts `status_code`, then `status`, then the exit
  code — in that order. Had this gone unnoticed, the ledger would have recorded failures as
  successes and vice versa.
- `result.data` is a list even for a single call.

*Still open:* whether `fab` has a native git command group better than `fab api`
(`fab --help` was not conclusive). Low impact — it affects only how verbose `fabctl sync`
is.

---

### V7 — Spark Job Definition git layout
**Confidence 85% · Priority 3 · Affects: decision D4**

D4 (notebooks by default) rests partly on SJD splitting its payload across a definition
file and a separately referenced main file. Microsoft's source-format doc enumerates
notebooks, reports and semantic models but not SJD. If SJD turns out to be plain-file
friendly, D4 deserves revisiting rather than being carried on an unverified premise.

---

### V8 — Local MCP scoping — **RESOLVED 2026-09-17, and the first answer was wrong**

Initially resolved from `--help` text, which said `--mode namespace` was the default. Then
measured by actually handshaking with the server and calling `tools/list`. The measurement
contradicted the documentation, so the config changed again.

| args | tools | approx tokens/turn |
|---|---:|---:|
| `--namespace docs --read-only` | **1** | **~340**  <- in use |
| `--mode all --read-only` | 26 | ~4,741 |
| `--mode all` | 48 | includes create / run / delete |
| `--mode namespace` (the documented default) | **0** | exposes nothing |
| `--read-only` alone | **0** | exposes nothing |
| no flags | **0** | exposes nothing |

Three things this settled:

1. **The documented default exposes no tools at all.** A config that looked correct and
   passed a connection check would have contributed nothing, silently. Only `tools/list`
   reveals it — which is why "the server connects" is not evidence that it works.
2. **`--read-only` genuinely filters**, 48 down to 26: `core_create-item`,
   `datafactory_create-*` and `run-pipeline` all disappear. It is enforcement at the server,
   not a hint.
3. **`--namespace docs` is a 14x saving over `--mode all --read-only`** for exactly the
   capability wanted. The single `docs` tool routes to all six documentation tools
   (`api-examples`, `best-practices`, `item-api-spec`, `item-definitions`,
   `list-item-types`, `platform-api-spec`). Nothing else is loaded, so the deny hook has
   nothing to catch from this server — the restriction is structural rather than policed.

Probe script: `scratchpad/probe_mcp.py` (handshake + `tools/list` over stdio). Worth keeping
the technique: every future MCP config claim should be measured this way, not read.

---

### V9b — Does reading workspace access need Microsoft Graph? — **RESOLVED 2026-09-17**
**No.** Fabric's [List Workspace Role
Assignments](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/list-workspace-role-assignments)
returns `principal.displayName` *and* `principal.userDetails.userPrincipalName`, so reading
who has access needs no directory lookup at all. Only *granting* needs an object id
(`fab acl set -I <objectId>`), and `fab api` cannot reach Graph — its audiences are
fabric/storage/azure/powerbi. Decision recorded in
[ADR 0001](../adr/0001-no-microsoft-graph-mcp.md): use a reviewed
`profiles/identities.yaml` rather than adding a third MCP server.

---

### V9 — Variable Library: runtime-only resolution?
**Confidence 85% · Priority 3 · Affects: `mapping-apply` code generation**

Assumed values resolve only at notebook runtime via `notebookutils`. If they were
resolvable at generation time, a generator *could* bake environment values into code — which
would be worse, defeating the point of value sets. This assumption failing is a trap to
avoid, not a capability to gain. Verify so nobody "optimises" into it later.

---

### V10 — `fab` token lifetime and refresh in long sessions
**Confidence unknown · Priority 3 · Blocks: phase 7 unattended runs**

Determines whether a long autonomous run hits a mid-session re-auth that an unattended
agent cannot perform. Test by leaving a session idle and re-invoking after several hours.

---

### V11 — Azure DevOps adapter has never run
**Confidence n/a · Priority 4 · Blocks: the ADO migration**

`tools/fabctl/vcs/azuredevops.py` is written but unexercised — this repo is on GitHub and
the move waits on a service principal. Treat every line of it as unverified. Also unverified:
that `az repos pr` output fields match what `_to_pr` expects.

---

### V12 — .claude/ is inert unless the repo IS the harness project root — **CONFIRMED 2026-09-18**
**Found the hard way: every hook was dead for a full session**

Claude Code was opened at `3d.Engineering Harness`, the *parent* of this repo. `.claude/`
here was therefore an ordinary subfolder, and the harness never read it. For an entire
session:

- the MCP deny hook never fired
- the workspace scope guard never fired — a raw `fab` call ran with no guard output at all
- the context injection never ran, so the boundary was never re-stated
- the clean-tree Stop hook never ran
- `fabric-gatekeeper` was not registered as a subagent

None of it announced itself. The only visible symptom was the subagent being missing, and
that only surfaced because somebody tried to use it.

**Why preflight said everything was fine.** `check_hooks` looked for hook filenames inside
`.claude/settings.json`. That is a check on *configuration*, and it passes whether or not the
harness ever loads the file. It reported "deny, scope guard, backstop and injection wired"
throughout a session in which nothing was wired to anything.

This is the same mistake as the MCP `--mode namespace` config in V8, made a second time:
treating "it is set up correctly" as evidence of "it works". Both were caught only by going
and looking at the effect.

**Fix.** Two checks, both effect-based:

- `check_hooks_live` — the injection hook writes a heartbeat to `.git/fabctl-hook-heartbeat`
  every time it runs. preflight reports how long ago a hook actually fired. No heartbeat
  means no hook has ever run in this clone, whatever settings.json says.
- `check_project_root` — compares `CLAUDE_PROJECT_DIR` against the repo root when the harness
  provides it.

**Operational rule that follows:** open Claude Code with the repository itself as the project
root, never its parent. Subagents and hooks are read at session start, so adding either
mid-session requires a restart before they take effect.

---

## B. Deliberately not built

Left out because building them now would mean encoding a guess, or because they depend on
access we do not have. Each names its unblocker.

| Component | Why deferred | Unblocked by |
|---|---|---|
| Normaliser content rules (`CALIBRATION_PENDING`) | Must be derived from observed Fabric output, never inferred. Guessing key order creates the exact round-trip diff the normaliser exists to prevent. | V1 |
| `fabctl sync push/pull/connect` | Needs workspace ID resolution and settled LRO semantics. A sync that reports success without confirming it is worse than no sync verb. | V2, V6 |
| `fabctl item import/create/delete` | Same. The guard logic and verb classes are built and tested; only the `fab` invocation is missing. | V6 |
| `mapping-apply` generator | Needs the notebook sentinel format confirmed against a real round-trip before it can safely rewrite a region. | V1 |
| `xlsx-render` | Deterministic-bytes work (fixed zip timestamps, pinned openpyxl, fixed workbook properties) is self-contained and buildable now, but pointless before mappings exist to render. | phase 4 |
| `data-reconcile` | Needs a live lakehouse with data in it. | a populated dev workspace |
| Lakehouse ledger mirror | Needs notebook execution and a target lakehouse. | service principal, phase 7 |
| Activity-log reconciliation | [Get Activity Events](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/get-activity-events) needs Fabric admin or an SP. 28-day retention, one day per request, 200 req/hour. | service principal |
| `promote` | Depends on sync and on PR adapters being exercised. | V6, V11 |
| CI workflow file | Host-specific (GitHub Actions vs ADO Pipelines). Writing both now duplicates work that a profile switch should make unnecessary. | repo host decision |
| `.github/instructions/*` shims | Generated by `fabctl skills sync`, which needs the skills to exist first. | phase 2–3 |
| `--out` on read verbs | The subagent read pattern (bulk to disk, conclusion to context) depends on it. Redirection works meanwhile, but `--out` should also record the artefact path in the run context so a finding always carries its receipt. | phase 2 |
| `fabctl run <playbook>` | The deterministic orchestrator for unattended runs. Pointless before there is an unattended runner to drive it. | service principal, phase 7 |
| Skill bodies (11 of 14) | `preflight`, `git-workflow` and `normalise` are effectively encoded in code and AGENTS.md. The rest describe verbs that do not exist yet; writing them now documents fiction. | phases 2–6 |

---

## C. Known limitations (not defects — accepted trade-offs)

1. **The ledger is tamper-evident, not tamper-proof.** Hash chaining plus the append-only CI
   check makes quiet tampering impossible and deliberate tampering visible. It does not stop
   an actor who can force-push. Non-repudiation needs the ledger written by an identity the
   agent does not control.

2. **The scratch workspace is a governance hole by construction.** It exists so unreviewed,
   unaudited direct writes are possible. "Audited" therefore describes the path to dev and
   above, never the whole system. The TTL reaper and the export→normalise→PR promotion rule
   are what stop it becoming a shadow production environment.

3. **`guard_raw_fab` starts in warn mode.** Phase 0 *is* raw `fab` exploration — this register
   gets settled by running `fab` by hand. Flip `MODE` to `"deny"` in
   `tools/fabctl/hooks/guard_raw_fab.py` when phase 1 lands. That switch is the moment the
   audit trail starts being complete; make it deliberately.

4. **Hook config lives in the repo the agent can write.** Put `.claude/settings.json` under
   CODEOWNERS with human review required, and have CI assert its digest.

5. **Copilot's `PostToolUse` may not carry `tool_response`.** Backstop records from that
   surface may capture intent without outcome. Recorded honestly in the record rather than
   guessed at.

6. **Fourteen skills is a lot to keep coherent.** The risk is their interactions — changing
   the verb classes touches sync, mapping-apply, promote and the hooks at once. If phases 1–3
   prove the model, consider merging the thinner skills before building 4–7.
