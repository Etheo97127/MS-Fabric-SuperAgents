# AGENTS.md — how to work in this repository

Read this before doing anything. It is the same file for every agent: Claude Code reads it
directly, VS Code Copilot reads it via `.github/copilot-instructions.md`, and both execute
the same hooks from `.claude/settings.json`.

Full design: [doc/framework/fabric-agent-framework.md](doc/framework/fabric-agent-framework.md).
What stops you touching the wrong thing: [doc/framework/gatekeeping.md](doc/framework/gatekeeping.md).
When to delegate to a subagent: [doc/framework/subagents.md](doc/framework/subagents.md).
Open questions: [doc/framework/verification-register.md](doc/framework/verification-register.md).

---

## The invariant

> Nothing mutates Fabric except through `fabctl`.
> Nothing mutates the repo except through `git`.
> Every `fabctl` invocation appends exactly one ledger record.
>
> Therefore **repo history + ledger = a complete account of every agent action.**

This is enforced by hooks, not by your good intentions. A `PreToolUse` hook denies MCP
mutations outright and a `PostToolUse` hook records anything that gets through. If you find
yourself wanting to work around a refusal, the refusal is almost certainly right — read its
message, which names the correct path.

---

## Start every task with preflight

```bash
python -m fabctl preflight     # what you can do right now
python -m fabctl scope         # which workspaces this repo may touch, and no others
```

It reports what you can actually do right now and what is blocked, with the command to
unblock each one. Do not start work it says you cannot finish: an agent that gets halfway
through a definition change and then hits an auth wall leaves Fabric and the repo
disagreeing, and someone else has to untangle it.

---

## The write path — the rule that matters most

**A git-connected workspace has exactly one writer, and that writer is git.**

Writing a definition straight into a git-connected workspace does not fail loudly. Fabric
records it as an uncommitted change, and the next `updateFromGit` either conflicts with it
or silently reverts it — so the damage surfaces later, somewhere else, to someone else.

| Where you are | Direct definition writes | How to change an item |
|---|---|---|
| Scratch workspace | allowed — that is what it is for | `fabctl item import` |
| dev, test, prod | **refused** | edit under `fabric/`, commit, `fabctl sync push` |

Running a notebook (`fabctl job run`) is allowed everywhere: it changes *data*, not
definitions, and produces no git diff. That distinction is the whole reason verbs are
classified.

```bash
python -m fabctl verbs     # every verb, its class, and what it may touch
```

**Creating an item that does not exist yet** goes repo-first: hand-author `.platform` and
the definition files under `fabric/`, commit, then sync. Use the Local MCP server's
per-item-type JSON schemas to get the definition right rather than guessing.

---

## Delegate these three

| Situation | Subagent | Why not do it inline |
|---|---|---|
| Any question needing more than a few lines of Fabric output | `fabric-reader` | its context is discarded; you keep the conclusion, not the schema dump |
| Any data mutation — INSERT/UPDATE/DELETE/MERGE, or `security/*.sql` | `fabric-dml` | its constraints sit at position 0 of a context that is never compacted |
| Repo and workspace disagree | `drift-reconciler` | the diff is large and the decision is judgement |

All three are read-only by tool set. They investigate and propose; **you** apply. That is
enforced by the harness, not by their prompts.

The rule behind this: *if a safeguard can be code, make it code.* A subagent prompt is more
durable than a compacted main context, but it is still a prompt. Hard rules live in
`verbs.py`, `workspace.py` and the hooks. Subagents protect the judgement layer — the
session-specific constraints that cannot be pre-encoded, like "keep the 2019 partition,
finance still reports on it".

---

## MCP

- **Core MCP: not used.** Not for writes, not for reads, not for traversal. Everything it
  does, `fab` does, and `fab` does more. It is not in `.mcp.json` and no skill references it.
- **Local MCP (`fabric-local`): knowledge only.** Use it for offline API specs, item JSON
  schemas and best practices while authoring — that is how you write a correct `.platform`
  and item definition instead of guessing. Its write tools are denied by hook.

Reads go through `fab` like everything else, because a read that informed a write is
evidence for that write and belongs in the same run context.

Why, in full: [doc/exploration/mcp-servers.md](doc/exploration/mcp-servers.md).

---

## Running `fab` on Windows

`fab` works from any shell **except** for interactive sign-in. `fab auth login` needs a real
Windows console and fails under Git Bash / MinTTY with:

```
[UnexpectedError] Found xterm-256color, while expecting a Windows console.
```

So: run `fab auth login` from **PowerShell or cmd**, once. Everything after that —
including every `fabctl` command and every `fab` call `fabctl` makes internally — works from
any shell, because the cached token is read from disk rather than negotiated interactively.

```powershell
fab auth login      # PowerShell, once
fab auth status     # confirms; works anywhere
```

---

## Git

### Commit your own work every response

**Never leave changed files uncommitted at the end of a turn.** An uncommitted working tree
is work with no trail: it cannot be reviewed, attributed, reverted, or distinguished from
anyone else's edits. The ledger records what happened *in Fabric*; git records what happened
*in the repo*, and both are needed for the invariant to hold.

So, at the end of any response that changed files:

```bash
git add -A
git commit -m "<type>(<scope>): <what changed>

<why, when it is not obvious from the diff>

Agent-Run-Id: <uuid>
Agent-Model: claude-opus-5
Agent-Surface: claude-code
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**Work on your own branch, never directly on the trunk.** Name it `agent/<slug>` for framework
work, or `feature/<slug>` when the change is a normal feature. Branch off the trunk once at
the start of a piece of work and stay on it; do not create a branch per response.

**Open a pull request when the work is coherent enough to review** — a working capability, a
resolved question, a self-contained fix. Not every response, and not only at the very end: a
PR carrying forty commits is a PR nobody reads.

Trunk is **`master`** in this repository. The environment chain below says `main` because that
is the convention the framework documents; they should be reconciled before the environment
branches are created.

Rules that matter more than they look:

- **Never commit a broken tree.** Run the tests first. The pre-commit hook normalises files,
  verifies the ledger chain and runs the suite, so a commit that passes is a commit that
  works — but only if you let the hook run. Do not use `--no-verify`.
- **Say why, not what.** The diff shows what changed. The message explains the decision,
  especially when a later reader might otherwise think it was arbitrary.
- **One logical change per commit.** "Fix guard and update docs and add profile" is three
  commits, and splitting them is what makes `git log` worth reading.
- **Commit the failure too.** If a response ended with something not working, commit it with a
  message saying so. A trail that only shows successes is not a trail.

### Branch model for environments

Branches: `feature/<slug>` → `dev` → `test` → `main`. Each environment branch is bound to
exactly one workspace; the mapping lives in `profiles/<name>.yaml`, never in code.

Commit directly to the lowest environment. Everything above it takes a pull request.

Every agent commit carries trailers — this is what makes git itself queryable:

```
feat(silver): apply customers mapping v1.1

Agent-Run-Id: 018f2a...
Agent-Model: claude-opus-5
Agent-Surface: claude-code
Contract: contracts/mappings/silver/customers.map.yaml@1.1
Ledger-Ref: ledger/2026/09/16.jsonl#018f2a...
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

All git operations are plain `git`. Only pull-request creation is host-specific, and that
is confined to `tools/fabctl/vcs/` behind the `vcs:` key in the profile.

---

## Formatting is code, not instruction

Never hand-format a notebook or a `.platform` file to match a style described in prose.
Run the normaliser:

```bash
python -m fabctl normalise          # fix in place
python -m fabctl normalise --check  # report only; CI uses this
```

It is a deterministic Python transformation — bytes in, bytes out. The pre-commit hook runs
it if you forget, and CI fails if the result differs. Anything you could paraphrase, you
will eventually paraphrase wrong.

Its rules are currently conservative on purpose: line endings, BOM, trailing whitespace,
final newline, JSON indentation. Content-affecting rules wait on calibration against
Fabric's own serializer — see V1 in the verification register.

---

## What never goes in git

Service principal secrets, connection strings, PATs, SAS tokens, data extracts, `.xlsx`
files, `.ipynb` files. Config belongs in a Variable Library item; secrets belong in Key
Vault and are referenced. `gitleaks` runs on every commit.

---

## Layout

| Path | What it holds |
|---|---|
| `fabric/` | **Fabric owns this.** Its git integration syncs exactly this directory. |
| `contracts/` | Entity and mapping YAML — the parameterised data architecture |
| `ledger/` | Append-only, hash-chained audit records |
| `profiles/` | Topology: workspaces, branches, value sets, gates |
| `tools/fabctl/` | The audited gateway |
| `tools/render/` | Deterministic generators (normaliser, xlsx, docs) |
| `doc/` | Design, ADRs, architecture, generated dictionary |
| `out/` | Generated artefacts. Git-ignored. Never commit from here. |

Only `fabric/` is visible to Fabric. Nothing else in this repo reaches a workspace.

---

## When you are unsure

Check the verification register before assuming behaviour. It lists what is documented but
not yet observed, with confidence levels. If you discover the real answer to one of those
entries while working, update it — that file is how this framework stops being a guess.
