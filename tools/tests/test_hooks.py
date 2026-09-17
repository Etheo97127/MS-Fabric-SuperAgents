"""Tests for the Bash guard and the per-turn context injection.

The guard is currently the only thing standing between an agent typo and another project's
workspace: the signed-in account is a tenant admin, there is no service principal, and so
there is no identity-level containment at all. That makes these tests load-bearing rather
than nice to have.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "tools" / "fabctl" / "hooks"

sys.path.insert(0, str(HOOKS))
import guard_raw_fab  # noqa: E402
import inject_context  # noqa: E402

OURS = "e11a0c22-b7f4-4a2e-8700-08aeadcdc513"        # Retail Analytics Dev
THEIRS = "296df62e-ebc7-43be-bd41-3005385809e8"      # FUAM-test-accent - another project


def run_hook(script: str, event: dict, cwd: Path, env: dict | None = None) -> dict:
    """Invoke a hook as the harness would and return its parsed decision (or {})."""
    import os
    proc = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps(event), capture_output=True, text=True, timeout=30,
        cwd=str(cwd), env={**os.environ, **(env or {})},
    )
    if not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def decision(result: dict) -> str | None:
    return (result.get("hookSpecificOutput") or {}).get("permissionDecision")


class LockedRepo(unittest.TestCase):
    """A temp repo with a lock pinning exactly one workspace."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / ".git").mkdir()
        (self.root / "profiles").mkdir()
        (self.root / "profiles" / "poc.lock.yaml").write_text(
            "profile: poc\n"
            "tenant_id: 51f70e33-0000-0000-0000-000000000000\n"
            "workspaces:\n"
            "  dev:\n"
            "    display_name: Retail Analytics Dev\n"
            f"    id: {OURS}\n",
            encoding="utf-8",
        )

    def bash(self, command: str) -> dict:
        return run_hook(
            "guard_raw_fab.py",
            {"tool_name": "Bash", "tool_input": {"command": command},
             "hook_event_name": "PreToolUse"},
            cwd=self.root,
        )


class TestWorkspaceIdExtraction(unittest.TestCase):
    def test_rest_path_form(self):
        self.assertEqual(
            guard_raw_fab.referenced_workspaces(f"fab api workspaces/{OURS}/items"),
            {OURS},
        )

    def test_flag_forms(self):
        for command in (f"fabctl ls --workspace {OURS}", f"fabctl ls -W {OURS}"):
            with self.subTest(command=command):
                self.assertEqual(guard_raw_fab.referenced_workspaces(command), {OURS})

    def test_multiple_workspaces_all_extracted(self):
        command = f"fab api workspaces/{OURS}/items && fab api workspaces/{THEIRS}/items"
        self.assertEqual(guard_raw_fab.referenced_workspaces(command), {OURS, THEIRS})

    def test_non_workspace_guids_are_ignored(self):
        # An item id is not a workspace id; treating every GUID as a workspace would refuse
        # legitimate commands and train people to bypass the guard.
        command = f"fab api workspaces/{OURS}/items/5295ac9b-f5d8-4150-857a-207fac35689c"
        self.assertEqual(guard_raw_fab.referenced_workspaces(command), {OURS})

    def test_case_insensitive(self):
        self.assertEqual(
            guard_raw_fab.referenced_workspaces(f"fab api workspaces/{OURS.upper()}/items"),
            {OURS},
        )


class TestScopeEnforcement(LockedRepo):
    def test_pinned_workspace_is_allowed(self):
        self.assertIsNone(decision(self.bash(f"fabctl ls --workspace {OURS}")))

    def test_other_projects_workspace_is_denied(self):
        result = self.bash(f"fab api workspaces/{THEIRS}/items -X delete")
        self.assertEqual(decision(result), "deny")
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("out of scope", reason)
        self.assertIn("Retail Analytics Dev", reason)   # says what IS allowed
        self.assertIn("open a PR", reason)              # and how to widen it properly

    def test_a_mix_of_in_and_out_of_scope_is_denied(self):
        # One legitimate target does not license the other.
        self.assertEqual(
            decision(self.bash(f"fab api workspaces/{OURS}/items; "
                               f"fab api workspaces/{THEIRS}/items")),
            "deny",
        )

    def test_read_only_call_to_another_workspace_is_still_denied(self):
        # Scope is a boundary, not a mutation guard: a read is how you discover a target
        # you should not be touching.
        self.assertEqual(decision(self.bash(f"fab api workspaces/{THEIRS}")), "deny")

    def test_escape_hatch_does_not_waive_scope(self):
        result = run_hook(
            "guard_raw_fab.py",
            {"tool_name": "Bash", "tool_input": {"command": f"fab api workspaces/{THEIRS}"},
             "hook_event_name": "PreToolUse"},
            cwd=self.root, env={"FABCTL_ALLOW_RAW_FAB": "1"},
        )
        self.assertEqual(decision(result), "deny",
                         "FABCTL_ALLOW_RAW_FAB waives the bypass warning, not the boundary")

    def test_commands_naming_no_workspace_are_unaffected(self):
        self.assertIsNone(decision(self.bash("fab auth status")))
        self.assertIsNone(decision(self.bash("git status")))


class TestNoLockMeansNothingInScope(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / ".git").mkdir()
        (self.root / "profiles").mkdir()

    def test_absent_lock_denies_rather_than_permits(self):
        # Fail closed. An absent lock is the state a fresh clone is in, and defaulting to
        # "allow everything" there is the worst possible default given a tenant-admin token.
        result = run_hook(
            "guard_raw_fab.py",
            {"tool_name": "Bash", "tool_input": {"command": f"fab api workspaces/{OURS}"},
             "hook_event_name": "PreToolUse"},
            cwd=self.root,
        )
        self.assertEqual(decision(result), "deny")
        self.assertIn("fabctl profile lock",
                      result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_unreadable_lock_is_treated_as_absent(self):
        (self.root / "profiles" / "poc.lock.yaml").write_text("{{{ not yaml", encoding="utf-8")
        result = run_hook(
            "guard_raw_fab.py",
            {"tool_name": "Bash", "tool_input": {"command": f"fab api workspaces/{OURS}"},
             "hook_event_name": "PreToolUse"},
            cwd=self.root,
        )
        self.assertEqual(decision(result), "deny", "a corrupt lock must not mean free rein")


class TestContextInjection(LockedRepo):
    def block(self) -> str:
        result = run_hook(
            "inject_context.py",
            {"hook_event_name": "UserPromptSubmit", "cwd": str(self.root)},
            cwd=self.root,
        )
        return (result.get("hookSpecificOutput") or {}).get("additionalContext", "")

    def test_names_the_workspaces_in_scope(self):
        block = self.block()
        self.assertIn("Retail Analytics Dev", block)
        self.assertIn(OURS, block)

    def test_stays_small_enough_to_pay_every_turn(self):
        # Injected on every prompt. A verbose version would cost more over a session than
        # the MCP server the framework dropped for exactly this reason.
        self.assertLess(len(self.block()), 900, "injection must stay compact")

    def test_is_ascii(self):
        self.block().encode("ascii")  # raises if not

    def test_never_blocks(self):
        # Injection keeps the boundary visible; the guard is what enforces it. A reminder
        # that could refuse would be a confusing place to put enforcement.
        result = run_hook(
            "inject_context.py",
            {"hook_event_name": "UserPromptSubmit", "cwd": str(self.root)},
            cwd=self.root,
        )
        self.assertIsNone(decision(result))

    def test_survives_a_junk_event(self):
        self.assertIsInstance(inject_context.build_block(self.root), str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
