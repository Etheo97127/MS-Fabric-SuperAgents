"""Capability probe — what can actually be done right now, and what is blocked.

Run before any task. The point is to fail *before* starting work that cannot be finished,
because an agent that gets halfway through a definition change and then hits an auth wall
leaves Fabric and the repo disagreeing.

Every check reports one of: ok, blocked (with the exact command to unblock), or unknown
(we could not determine it, which is different from it being fine).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config, fabwrap, workspace

OK, BLOCKED, UNKNOWN = "ok", "blocked", "unknown"

_MARK = {OK: " ok  ", BLOCKED: "BLOCK", UNKNOWN: "  ?  "}


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""
    fix: str = ""

    def render(self) -> str:
        line = f"  [{_MARK[self.state]}] {self.name:<22} {self.detail}"
        if self.state == BLOCKED and self.fix:
            line += f"\n{' ' * 10}-> {self.fix}"
        return line


def check_fab() -> Check:
    if not fabwrap.available():
        return Check("fab CLI", BLOCKED, "not on PATH", "pip install ms-fabric-cli")
    try:
        result = fabwrap.run(["--version"], json_output=False, check=False, timeout=30)
        version = (result.stdout or result.stderr).strip().splitlines()[0] if result.ok else "?"
        return Check("fab CLI", OK, version)
    except Exception as exc:  # noqa: BLE001
        return Check("fab CLI", UNKNOWN, f"present but did not respond: {exc}")


def check_auth() -> Check:
    """Read `fab auth status` for the actual login state.

    `fab auth status` exits 0 whether or not you are signed in, so a zero exit code proves
    only that the command ran. The state is in the body, and a preflight that reports ok
    while the body says 'Logged In: False' is worse than no preflight at all.
    """
    if not fabwrap.available():
        return Check("fab auth", BLOCKED, "fab not installed", "pip install ms-fabric-cli")
    try:
        result = fabwrap.run(["auth", "status"], json_output=False, check=False, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return Check("fab auth", UNKNOWN, str(exc))

    body = f"{result.stdout}\n{result.stderr}"
    fields = {}
    for line in body.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()

    logged_in = fields.get("logged in", "").lower()
    if logged_in == "true":
        account = fields.get("account") or "signed in"
        tenant = fields.get("tenant id", "")
        detail = f"{account}" + (f" (tenant {tenant[:8]}...)" if tenant and tenant != "N/A" else "")
        return Check("fab auth", OK, detail)
    if logged_in == "false":
        return Check(
            "fab auth", BLOCKED, "not signed in",
            "fab auth login   # run from PowerShell or cmd, not Git Bash",
        )
    return Check("fab auth", UNKNOWN, "could not read login state from `fab auth status`")


def check_git(root: Path) -> Check:
    if not shutil.which("git"):
        return Check("git", BLOCKED, "not on PATH", "install git")
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        state = f"branch {branch}" + (", uncommitted changes" if dirty else ", clean")
        return Check("git", OK, state)
    except Exception as exc:  # noqa: BLE001
        return Check("git", UNKNOWN, str(exc))


def check_hooks(root: Path) -> Check:
    settings = root / ".claude" / "settings.json"
    if not settings.exists():
        return Check("agent hooks", BLOCKED, ".claude/settings.json missing",
                     "restore it from the framework; without it nothing is enforced")
    text = settings.read_text(encoding="utf-8")
    missing = [h for h in ("deny_mcp_mutation", "guard_raw_fab", "ledger_append",
                           "inject_context")
               if h not in text]
    if missing:
        return Check("agent hooks", BLOCKED, f"not wired: {', '.join(missing)}",
                     "add the missing hook entries to .claude/settings.json")
    return Check("agent hooks", OK, "configured (see `hooks firing` for whether they run)")


def check_hooks_live(root: Path) -> Check:
    """Are the hooks actually FIRING, not merely configured?

    This exists because the previous check looked for hook filenames in settings.json and
    reported "wired" for an entire session in which no hook ran even once. The cause was
    that Claude Code was rooted at the PARENT directory, so .claude/ here was an ordinary
    subfolder the harness never read.

    Configuration is not effect. The heartbeat is written by the UserPromptSubmit hook, so
    its presence is evidence the harness is running these files.
    """
    beat = root / ".git" / "fabctl-hook-heartbeat"
    fix = (
        "open Claude Code with this directory as the project root "
        "(not its parent), then restart the session so agents and hooks are re-read"
    )
    if not beat.exists():
        return Check("hooks firing", BLOCKED, "no hook has ever run in this clone", fix)

    from datetime import datetime, timezone
    try:
        last = datetime.fromisoformat(beat.read_text(encoding="utf-8").strip())
        age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except (OSError, ValueError):
        return Check("hooks firing", UNKNOWN, "heartbeat unreadable")

    if age_h > 24:
        return Check("hooks firing", BLOCKED, f"last fired {age_h:.0f}h ago", fix)
    return Check("hooks firing", OK, f"last fired {age_h * 60:.0f} min ago")


def check_project_root(root: Path) -> Check:
    """Does the harness consider THIS directory the project root?

    .claude/ is only honoured at the project root. Nested inside another folder it is
    inert — settings, hooks and subagents are all silently ignored, and nothing announces
    it. That is exactly how a session can run for hours believing it is guarded.
    """
    import os
    declared = os.environ.get("CLAUDE_PROJECT_DIR")
    if not declared:
        return Check(
            "project root", UNKNOWN,
            "CLAUDE_PROJECT_DIR unset - cannot confirm .claude/ is being read",
            "check `hooks firing` below; that is the check that settles it",
        )
    if Path(declared).resolve() != root.resolve():
        return Check(
            "project root", BLOCKED,
            f"harness root is {declared}, repo is {root}",
            "open Claude Code with the repo itself as the project root; .claude/ is "
            "ignored when nested",
        )
    return Check("project root", OK, "repo is the harness project root")


def check_mcp_parity(root: Path) -> Check:
    """Do Claude Code and Copilot get the same MCP server, with the same scoping?

    The two hosts disagree on schema — `.mcp.json` uses `mcpServers`, `.vscode/mcp.json`
    uses `servers` — so the args are necessarily duplicated. Duplicated config drifts, and
    drift here is not cosmetic: the args are what restrict the server to documentation
    tools. A stale copy on one surface hands that agent a mutation path the other does not
    have, and the framework's "same rules on both surfaces" claim quietly stops being true.
    """
    import json

    claude_path, vscode_path = root / ".mcp.json", root / ".vscode" / "mcp.json"
    if not claude_path.exists():
        return Check("mcp parity", BLOCKED, ".mcp.json missing",
                     "restore it; see doc/exploration/mcp-servers.md")
    if not vscode_path.exists():
        return Check("mcp parity", UNKNOWN, ".vscode/mcp.json absent (Copilot unconfigured)")

    try:
        claude = json.loads(claude_path.read_text(encoding="utf-8")).get("mcpServers", {})
        vscode = json.loads(vscode_path.read_text(encoding="utf-8")).get("servers", {})
    except json.JSONDecodeError as exc:
        return Check("mcp parity", BLOCKED, f"unparseable MCP config: {exc}")

    names = set(claude) | set(vscode)
    for name in sorted(names):
        a, b = claude.get(name), vscode.get(name)
        if a is None or b is None:
            missing = ".vscode/mcp.json" if b is None else ".mcp.json"
            return Check("mcp parity", BLOCKED, f"{name!r} only in one config",
                         f"add {name!r} to {missing} with identical args")
        if a.get("args") != b.get("args") or a.get("command") != b.get("command"):
            return Check("mcp parity", BLOCKED, f"{name!r} args differ between hosts",
                         "make the args identical; scoping flags are what keep the "
                         "server read-only")
    return Check("mcp parity", OK, f"{len(names)} server(s) identical on both hosts")


def check_precommit(root: Path) -> Check:
    if not (root / ".pre-commit-config.yaml").exists():
        return Check("pre-commit", BLOCKED, "config missing", "restore .pre-commit-config.yaml")
    if not (root / ".git" / "hooks" / "pre-commit").exists():
        return Check("pre-commit", BLOCKED, "hook not installed in this clone",
                     "pip install pre-commit && pre-commit install")
    return Check("pre-commit", OK, "installed")


def check_ledger(root: Path) -> Check:
    from .ledger import Ledger
    ledger = Ledger(root=root / "ledger")
    try:
        problems = ledger.verify()
    except Exception as exc:  # noqa: BLE001
        return Check("ledger", UNKNOWN, str(exc))
    if problems:
        return Check("ledger", BLOCKED, f"{len(problems)} chain problem(s): {problems[0]}",
                     "fabctl ledger verify   # for the full report")
    count = sum(1 for _ in ledger.read_all())
    return Check("ledger", OK, f"chain intact, {count} record(s)")


def check_scope(profile, root: Path) -> Check:
    """Is the set of workspaces this repo may touch actually pinned?

    Without a lock there is no allowlist, and `fab` can reach every workspace the signed-in
    person can. Reporting that as merely 'not configured' would understate it.
    """
    if profile is None:
        return Check("workspace scope", UNKNOWN, "profile did not load")
    try:
        lock = workspace.load(root, profile)
    except workspace.LockError as exc:
        return Check("workspace scope", BLOCKED, str(exc).splitlines()[0],
                     f"fabctl profile lock --profile {profile.name}")
    names = ", ".join(sorted(lock.workspaces))
    return Check("workspace scope", OK, f"{len(lock.workspaces)} pinned ({names})")


def check_profile(profile_name: str | None, root: Path) -> tuple[Check, config.Profile | None]:
    try:
        profile = config.load(profile_name, root=root)
    except config.ConfigError as exc:
        return Check("profile", BLOCKED, str(exc), "set FABCTL_PROFILE or pass --profile"), None
    chain = " -> ".join(e.id for e in profile.environments)
    scratch = f", scratch: {profile.scratch.workspace}" if profile.scratch else ", no scratch"
    return Check("profile", OK, f"{profile.name} [{chain}]{scratch}"), profile


def run(profile_name: str | None = None, *, brief: bool = False) -> int:
    root = config.repo_root()
    profile_check, profile = check_profile(profile_name, root)

    checks = [
        check_fab(),
        check_auth(),
        check_git(root),
        profile_check,
        check_scope(profile, root),
        check_project_root(root),
        check_hooks(root),
        check_hooks_live(root),
        check_mcp_parity(root),
        check_precommit(root),
        check_ledger(root),
    ]

    blocked = [c for c in checks if c.state == BLOCKED]

    if brief:
        summary = ", ".join(
            f"{c.name}={c.state}" for c in checks if c.state != OK
        ) or "all ok"
        print(f"[fabctl preflight] {summary}")
        return 0  # brief mode informs, never blocks the session

    print("fabctl preflight")
    for check in checks:
        print(check.render())

    if profile is not None and profile.scratch:
        print(
            f"\n  note: {profile.scratch.workspace} is the only workspace where direct "
            f"definition writes are permitted.\n"
            f"        Everything above it goes through git. This is enforced in "
            f"tools/fabctl/verbs.py, not by convention."
        )

    if blocked:
        print(f"\n{len(blocked)} blocker(s). Resolve them before starting work.")
        return 1
    print("\nready.")
    return 0
