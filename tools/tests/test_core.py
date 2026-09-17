"""Tests for the parts of fabctl that need no Fabric access.

Uses stdlib unittest so it runs with no extra install:

    python -m unittest discover -s tools/tests -t tools

The ledger and the guards are the pieces worth testing hardest — they are what make the
audit claim true, so a silent regression in either is the one failure the framework
cannot detect on its own.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fabctl import config, verbs  # noqa: E402
from fabctl.ledger import Ledger, canonical, digest  # noqa: E402
from render import normalise  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fabctl" / "hooks"))
import deny_mcp_mutation  # noqa: E402
import guard_raw_fab  # noqa: E402

UTC = timezone.utc


class TempRepo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------

class TestLedger(TempRepo):
    def ledger(self) -> Ledger:
        return Ledger(root=self.root / "ledger")

    def test_canonical_is_order_independent(self):
        self.assertEqual(canonical({"b": 1, "a": 2}), canonical({"a": 2, "b": 1}))

    def test_append_assigns_seq_and_chains(self):
        led = self.ledger()
        first = led.append({"action": "a.one"})
        second = led.append({"action": "a.two"})

        self.assertEqual(first["seq"], 0)
        self.assertIsNone(first["prev"])
        self.assertEqual(second["seq"], 1)
        self.assertEqual(second["prev"], digest(first))
        self.assertEqual(led.verify(), [])

    def test_reserved_fields_are_rejected(self):
        led = self.ledger()
        for field in ("ts", "seq", "prev"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    led.append({"action": "x", field: "forged"})

    def test_run_id_is_shared_within_a_run_and_overridable(self):
        led = self.ledger()
        a = led.append({"action": "a"})
        b = led.append({"action": "b"})
        self.assertEqual(a["run_id"], b["run_id"])
        c = led.append({"action": "c", "run_id": "explicit"})
        self.assertEqual(c["run_id"], "explicit")

    def test_tampering_with_a_record_breaks_the_chain(self):
        led = self.ledger()
        led.append({"action": "a.one", "intent": "original"})
        led.append({"action": "a.two"})
        led.append({"action": "a.three"})

        path = led.day_path()
        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["intent"] = "rewritten after the fact"
        lines[0] = canonical(record)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        problems = led.verify()
        self.assertTrue(problems, "editing a record must break the chain")
        self.assertIn("broken chain", problems[0])

    def test_deleting_a_record_is_detected(self):
        led = self.ledger()
        for i in range(3):
            led.append({"action": f"a.{i}"})

        path = led.day_path()
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

        self.assertTrue(led.verify(), "removing a record must be detected")

    def test_days_are_linked_by_prev_day(self):
        led = self.ledger()
        yesterday = datetime.now(UTC) - timedelta(days=1)
        led.append({"action": "old"}, when=yesterday)
        today = led.append({"action": "new"}, when=datetime.now(UTC))

        self.assertIn("prev_day", today)
        self.assertTrue(today["prev_day"].startswith("sha256:"))
        self.assertEqual(led.verify(), [])

    def test_altering_an_earlier_day_is_detected(self):
        led = self.ledger()
        yesterday = datetime.now(UTC) - timedelta(days=1)
        led.append({"action": "old"}, when=yesterday)
        led.append({"action": "new"}, when=datetime.now(UTC))

        old_path = led.day_path(yesterday)
        old_path.write_text(
            canonical({"action": "forged", "ts": "x", "seq": 0, "prev": None}) + "\n",
            encoding="utf-8",
        )
        self.assertTrue(led.verify(), "rewriting an older day must be detected")


# ---------------------------------------------------------------------------
# verb classes and the write-path rule
# ---------------------------------------------------------------------------

class TestVerbs(unittest.TestCase):
    def test_definition_verb_refused_on_connected_workspace(self):
        verb = verbs.lookup("item import")
        with self.assertRaises(verbs.VerbRefused) as ctx:
            verbs.check(verb, workspace="ws_dev", git_connected=True, env_id="dev",
                        scratch_workspace="ws_scratch")
        message = str(ctx.exception)
        self.assertIn("git-connected", message)
        self.assertIn("fabctl sync push", message)
        self.assertIn("ws_scratch", message)

    def test_definition_verb_allowed_on_scratch(self):
        verbs.check(verbs.lookup("item import"), workspace="ws_scratch", git_connected=False)

    def test_execute_allowed_on_connected_workspace(self):
        # Running a notebook changes data, not definitions, so it produces no git drift.
        verbs.check(verbs.lookup("job run"), workspace="ws_dev", git_connected=True)

    def test_sync_allowed_on_connected_workspace(self):
        verbs.check(verbs.lookup("sync push"), workspace="ws_dev", git_connected=True)

    def test_only_read_and_query_are_unaudited(self):
        for name, verb in verbs.REGISTRY.items():
            with self.subTest(verb=name):
                expected = verb.cls not in (verbs.VerbClass.READ, verbs.VerbClass.QUERY)
                self.assertEqual(verbs.is_audited(verb), expected)

    def test_dml_always_requires_a_plan_and_nothing_else_does(self):
        # Auditing a DELETE after it ran is a post-mortem, not a control.
        for name, verb in verbs.REGISTRY.items():
            with self.subTest(verb=name):
                self.assertEqual(
                    verbs.requires_plan(verb), verb.cls is verbs.VerbClass.DML
                )

    def test_every_dml_verb_requires_apply(self):
        # Dry-run by default is what makes the plan step unskippable.
        for name, verb in verbs.REGISTRY.items():
            if verb.cls is verbs.VerbClass.DML:
                with self.subTest(verb=name):
                    self.assertTrue(verb.dry_run_default, f"{name} must default to dry-run")

    def test_dml_plan_itself_mutates_nothing(self):
        # The plan step counts affected rows, so it must be classified as read-only or the
        # cycle would need a plan to make a plan.
        self.assertIs(verbs.lookup("dml plan").cls, verbs.VerbClass.QUERY)
        self.assertFalse(verbs.is_audited(verbs.lookup("dml plan")))

    def test_unregistered_verb_is_refused(self):
        with self.assertRaises(verbs.VerbRefused):
            verbs.lookup("item nuke")

    def test_apply_required_for_mutations_in_gated_environments(self):
        job = verbs.lookup("job run")
        self.assertFalse(verbs.requires_apply(job, env_gate="none"))
        self.assertTrue(verbs.requires_apply(job, env_gate="pull_request"))
        # Destructive verbs require --apply even where there is no gate.
        self.assertTrue(verbs.requires_apply(verbs.lookup("item delete"), env_gate="none"))
        self.assertFalse(verbs.requires_apply(verbs.lookup("ls"), env_gate="pull_request"))


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------

class TestDenyMcpMutation(unittest.TestCase):
    def test_mutating_tools_are_caught(self):
        for tool in (
            "mcp__fabric-core__create_workspace",
            "mcp__fabric-core__update_item_definition",
            "mcp__fabric-core__delete_item",
            "mcp__fabric-local__upload_lakehouse_file",
            "mcp__fabric-local__write_onelake_file",
            "mcp__whatever__run_on_demand_job",
        ):
            with self.subTest(tool=tool):
                self.assertTrue(deny_mcp_mutation.classify(tool)[0], tool)

    def test_reads_pass_through(self):
        for tool in (
            "mcp__fabric-core__list_workspaces",
            "mcp__fabric-core__get_item_definition",
            "mcp__fabric-core__search_catalog",
            "mcp__fabric-local__get_api_spec",
            "mcp__fabric-local__describe_item_schema",
            "mcp__fabric-core__get_knowledge",
        ):
            with self.subTest(tool=tool):
                self.assertFalse(deny_mcp_mutation.classify(tool)[0], tool)

    def test_non_mcp_tools_are_ignored(self):
        for tool in ("Bash", "Edit", "Write", ""):
            self.assertFalse(deny_mcp_mutation.classify(tool)[0])


class TestGuardRawFab(unittest.TestCase):
    def test_bare_fab_calls_are_flagged(self):
        for command in (
            "fab import MyNotebook.notebook -i ./def.json",
            "cd /tmp && fab job run nb_silver",
            "fab api workspaces/123/git/commitToGit -X post",
        ):
            with self.subTest(command=command):
                self.assertTrue(guard_raw_fab.offending_invocations(command), command)

    def test_setup_and_diagnostics_are_allowed(self):
        for command in ("fab auth login", "fab auth status", "fab --version", "fab config ls"):
            with self.subTest(command=command):
                self.assertEqual(guard_raw_fab.offending_invocations(command), [])

    def test_does_not_match_other_words_containing_fab(self):
        for command in (
            "python -m fabctl preflight",
            "pip install ms-fabric-cli",
            "ls fabric/",
            "cat prefab.txt",
        ):
            with self.subTest(command=command):
                self.assertEqual(guard_raw_fab.offending_invocations(command), [], command)


# ---------------------------------------------------------------------------
# normaliser
# ---------------------------------------------------------------------------

class TestNormalise(unittest.TestCase):
    def test_safe_rules(self):
        text, applied = normalise.normalise_text("﻿a = 1  \r\nb = 2\r\n\n\n")
        self.assertEqual(text, "a = 1\nb = 2\n")
        self.assertIn("strip BOM", applied)
        self.assertIn("CRLF -> LF", applied)

    def test_is_idempotent(self):
        once, _ = normalise.normalise_text("x = 1  \r\n\n")
        twice, applied = normalise.normalise_text(once)
        self.assertEqual(once, twice)
        self.assertEqual(applied, [], "a second pass must change nothing")

    def test_json_key_order_is_preserved(self):
        # Deliberate: Fabric's own key order is unknown until calibration, and reordering
        # to match a guess would create the very round-trip diff this prevents.
        source = '{"version":"2.0","config":{"logicalId":"abc"},"metadata":{"type":"Notebook"}}'
        result, _ = normalise.normalise_json(source)
        self.assertLess(result.index("version"), result.index("config"))
        self.assertLess(result.index("config"), result.index("metadata"))

    def test_malformed_json_is_left_alone(self):
        result, _ = normalise.normalise_json('{"broken": ')
        self.assertIn("broken", result)

    def test_empty_file_stays_empty(self):
        self.assertEqual(normalise.normalise_text("")[0], "")
        self.assertEqual(normalise.normalise_text("\n\n\n")[0], "")

    def test_calibration_rules_are_not_silently_active(self):
        self.assertTrue(
            normalise.CALIBRATION_PENDING,
            "if this is empty, calibration is done and the rules should be real code",
        )


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

class TestPreflightAuth(unittest.TestCase):
    """`fab auth status` exits 0 whether or not you are signed in.

    An earlier version treated exit 0 plus any output as success, so preflight reported
    'ok' while the body said 'Logged In: False'. A preflight that lies is worse than none.
    """

    SIGNED_OUT = "\n".join([
        "Not logged in to app.fabric.microsoft.com",
        "Logged In: False",
        "Account: N/A",
        "Tenant ID: N/A",
    ])
    SIGNED_IN = "\n".join([
        "Logged In: True",
        "Account: haotian.qu@deeeplabs.com",
        "Tenant ID: 1234abcd-5678-90ef-ghij-klmnopqrstuv",
    ])

    def _check_with(self, stdout: str):
        from fabctl import fabwrap, preflight

        real_run, real_available = fabwrap.run, fabwrap.available
        fabwrap.available = lambda: True
        fabwrap.run = lambda *a, **k: fabwrap.FabResult(
            argv=["fab", "auth", "status"], returncode=0,
            stdout=stdout, stderr="", duration_ms=1,
        )
        try:
            return preflight.check_auth()
        finally:
            fabwrap.run, fabwrap.available = real_run, real_available

    def test_signed_out_is_blocked_despite_exit_zero(self):
        from fabctl import preflight
        check = self._check_with(self.SIGNED_OUT)
        self.assertEqual(check.state, preflight.BLOCKED)
        self.assertIn("fab auth login", check.fix)

    def test_signed_in_is_ok_and_names_the_account(self):
        from fabctl import preflight
        check = self._check_with(self.SIGNED_IN)
        self.assertEqual(check.state, preflight.OK)
        self.assertIn("haotian.qu@deeeplabs.com", check.detail)

    def test_unparseable_output_is_unknown_not_ok(self):
        from fabctl import preflight
        self.assertEqual(self._check_with("something unexpected").state, preflight.UNKNOWN)


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------

class TestProfiles(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]

    def test_shipped_profiles_load(self):
        for name in ("poc", "project", "enterprise"):
            with self.subTest(profile=name):
                profile = config.load(name, root=self.ROOT)
                self.assertTrue(profile.environments)
                self.assertIn(profile.vcs, config.VALID_VCS)

    def test_lowest_environment_is_ungated(self):
        # The framework's autonomy rule: direct to the lowest environment, PR above it.
        for name in ("poc", "project"):
            with self.subTest(profile=name):
                self.assertEqual(config.load(name, root=self.ROOT).lowest.gate, "none")

    def test_production_is_always_gated(self):
        for name in ("poc", "project", "enterprise"):
            with self.subTest(profile=name):
                prod = config.load(name, root=self.ROOT).environments[-1]
                self.assertEqual(prod.gate, "pull_request", f"{name}: prod must be gated")

    def test_scratch_may_not_be_git_connected(self):
        bad = self.ROOT / "profiles" / "_test_bad.yaml"
        bad.write_text(
            "name: bad\nvcs: github\n"
            "scratch: {workspace: w, git_connected: true}\n"
            "environments: [{id: dev, workspace: w, branch: dev, value_set: dev}]\n",
            encoding="utf-8",
        )
        self.addCleanup(bad.unlink)
        with self.assertRaises(config.ConfigError):
            config.load("_test_bad", root=self.ROOT)

    def test_unknown_environment_names_the_valid_ones(self):
        profile = config.load("project", root=self.ROOT)
        with self.assertRaises(config.ConfigError) as ctx:
            profile.env("staging")
        self.assertIn("dev", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
