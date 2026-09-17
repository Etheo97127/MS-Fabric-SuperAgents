"""Tests for workspace gatekeeping.

This is the boundary between "the workspaces this repo owns" and "every workspace the
signed-in human can reach". Without it, the verb classes decide how a workspace may be
changed but nothing decides *which* workspaces are in scope at all.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fabctl import config, workspace  # noqa: E402

DEV_ID = "11111111-2222-3333-4444-555555555555"
PROD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SCRATCH_ID = "99999999-8888-7777-6666-555555555555"
FOREIGN_ID = "deadbeef-dead-beef-dead-beefdeadbeef"
TENANT = "0f0f0f0f-1e1e-2d2d-3c3c-4b4b4b4b4b4b"


def make_lock() -> workspace.Lock:
    return workspace.Lock(
        profile="poc",
        tenant_id=TENANT,
        workspaces={
            "scratch": workspace.PinnedWorkspace("scratch", "Retail Scratch Haotian", SCRATCH_ID),
            "dev": workspace.PinnedWorkspace("dev", "Retail Analytics Dev", DEV_ID),
            "prod": workspace.PinnedWorkspace("prod", "Retail Analytics Prod", PROD_ID),
        },
        generated_at="2026-09-17T00:00:00Z",
        generated_by="haotian.qu@deeeplabs.com",
    )


class TestScope(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = make_lock()

    def test_pinned_workspace_is_in_scope(self):
        pinned = self.lock.assert_in_scope(DEV_ID)
        self.assertEqual(pinned.key, "dev")

    def test_id_case_is_ignored(self):
        self.lock.assert_in_scope(DEV_ID.upper())

    def test_unpinned_workspace_is_refused_and_lists_what_is_allowed(self):
        with self.assertRaises(workspace.OutOfScope) as ctx:
            self.lock.assert_in_scope(FOREIGN_ID, display_name="Someone Else Prod")
        message = str(ctx.exception)
        self.assertIn("not in scope", message)
        self.assertIn("Retail Analytics Dev", message)      # tells you what IS allowed
        self.assertIn("fabctl profile lock", message)       # and how to widen it properly

    def test_renamed_workspace_is_refused_even_though_the_id_matches(self):
        # The id is pinned, but Fabric reports a different name: either a rename or a
        # reused id. Both need a human, and neither should proceed silently.
        with self.assertRaises(workspace.OutOfScope) as ctx:
            self.lock.assert_in_scope(DEV_ID, display_name="Retail Analytics DEV (old)")
        self.assertIn("pinned as", str(ctx.exception))

    def test_matching_name_passes(self):
        self.lock.assert_in_scope(DEV_ID, display_name="Retail Analytics Dev")

    def test_wrong_tenant_is_refused(self):
        with self.assertRaises(workspace.OutOfScope) as ctx:
            self.lock.assert_tenant("ffffffff-ffff-ffff-ffff-ffffffffffff")
        self.assertIn("locked to", str(ctx.exception))

    def test_right_tenant_passes(self):
        self.lock.assert_tenant(TENANT.upper())

    def test_unknown_tenant_does_not_block(self):
        # `fab auth status` may not report a tenant. Unknown is not the same as wrong, and
        # guessing "wrong" here would block all work for no evidence.
        self.lock.assert_tenant(None)

    def test_unknown_env_key_names_the_known_ones(self):
        with self.assertRaises(workspace.OutOfScope) as ctx:
            self.lock.by_key("staging")
        self.assertIn("dev", str(ctx.exception))

    def test_non_guid_id_is_rejected_at_construction(self):
        with self.assertRaises(workspace.LockError):
            workspace.PinnedWorkspace("dev", "Dev", "not-a-guid")


class TestLockFile(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / "profiles").mkdir()
        (self.root / ".git").mkdir()
        (self.root / "profiles" / "poc.yaml").write_text(
            "name: poc\nvcs: github\n"
            "scratch: {workspace: Retail Scratch Haotian, git_connected: false}\n"
            "environments:\n"
            "  - {id: dev, workspace: Retail Analytics Dev, branch: dev, value_set: dev}\n"
            "  - {id: prod, workspace: Retail Analytics Prod, branch: main, value_set: prod,"
            " gate: pull_request}\n",
            encoding="utf-8",
        )
        self.profile = config.load("poc", root=self.root)

    def test_round_trips(self):
        workspace.save(self.root, make_lock())
        loaded = workspace.load(self.root, self.profile)
        self.assertEqual(loaded.tenant_id, TENANT)
        self.assertEqual(loaded.by_key("dev").id, DEV_ID)
        self.assertEqual(loaded.allowed_ids, {DEV_ID, PROD_ID, SCRATCH_ID})

    def test_missing_lock_refuses_with_the_command_to_fix_it(self):
        with self.assertRaises(workspace.LockError) as ctx:
            workspace.load(self.root, self.profile)
        self.assertIn("fabctl profile lock", str(ctx.exception))

    def test_profile_gaining_an_environment_forces_a_relock(self):
        # Otherwise the new environment would have no pinned id and therefore no gate.
        lock = make_lock()
        del lock.workspaces["prod"]
        workspace.save(self.root, lock)
        with self.assertRaises(workspace.LockError) as ctx:
            workspace.load(self.root, self.profile)
        self.assertIn("prod", str(ctx.exception))

    def test_lock_for_a_different_profile_is_refused(self):
        lock = make_lock()
        workspace.save(self.root, lock)
        path = workspace.lock_path(self.root, "poc")
        path.write_text(path.read_text(encoding="utf-8").replace(
            "profile: poc", "profile: enterprise"), encoding="utf-8")
        with self.assertRaises(workspace.LockError):
            workspace.load(self.root, self.profile)

    def test_dump_is_stable(self):
        lock = make_lock()
        self.assertEqual(workspace.dump(lock), workspace.dump(lock))


class TestRunBudget(unittest.TestCase):
    def test_allows_up_to_the_limit(self):
        budget = workspace.RunBudget(3)
        for i in range(3):
            budget.charge(f"item-{i}")
        self.assertEqual(budget.touched, 3)

    def test_refuses_beyond_the_limit_and_says_what_was_touched(self):
        budget = workspace.RunBudget(2)
        budget.charge("nb_one")
        budget.charge("nb_two")
        with self.assertRaises(workspace.BlastRadiusExceeded) as ctx:
            budget.charge("nb_three")
        message = str(ctx.exception)
        self.assertIn("nb_one", message)
        self.assertIn("nb_three", message)

    def test_touching_the_same_item_twice_costs_one(self):
        budget = workspace.RunBudget(2)
        for _ in range(5):
            budget.charge("nb_one")
        budget.charge("nb_two")
        self.assertEqual(budget.touched, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
