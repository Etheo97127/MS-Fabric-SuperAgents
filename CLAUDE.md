# CLAUDE.md

Read [AGENTS.md](AGENTS.md). It is the single entry point for every agent working in this
repository, and it is the same file Copilot reads.

Before starting any task: `python -m fabctl preflight`

Before ending any response that changed files: commit them on your `agent/<slug>` branch
with the trailers AGENTS.md specifies. Never leave a turn with an uncommitted tree.
