# Copilot instructions

Read [AGENTS.md](../AGENTS.md). It is the single entry point for every agent working in this
repository, and it is the same file Claude Code reads.

Before starting any task: `python -m fabctl preflight`

Hooks in `.claude/settings.json` apply to you too — VS Code reads that file natively. Enable
them with `chat.useCustomAgentHooks: true`. They deny MCP mutations and record anything that
gets through; a refusal message always names the correct path.
