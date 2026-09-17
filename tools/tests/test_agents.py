"""Tests for subagent definitions.

A subagent's tool list is the one part of it the harness enforces; everything else in the
file is a prompt, and a prompt can be ignored on a bad turn. So the tool list is worth a
test, and "no write tools" is worth asserting rather than remembering.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / ".claude" / "agents"

#: Designed but not loaded. Held to the same rules as active agents, because activating one
#: is a `mv` and nothing would re-check it on the way in.
DRAFTS = ROOT / "doc" / "framework" / "draft-agents"

#: Tools that let a subagent change the repository directly. Subagents investigate and
#: propose; the caller applies. A reader that can write is a reader that can cause the
#: thing it was asked to observe.
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def parse(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        raise AssertionError(f"{path.name}: no YAML frontmatter")
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields, text[match.end():]


def agent_files() -> list[Path]:
    """Active agents plus drafts. Drafts are one `mv` from being live."""
    drafts = [p for p in sorted(DRAFTS.glob("*.md")) if p.stem != "README"]
    return sorted(AGENTS.glob("*.md")) + drafts


def find_agent(name: str) -> Path | None:
    for base in (AGENTS, DRAFTS):
        candidate = base / f"{name}.md"
        if candidate.exists():
            return candidate
    return None


class TestSubagentDefinitions(unittest.TestCase):
    def test_at_least_one_agent_is_active(self):
        active = sorted(AGENTS.glob("*.md"))
        self.assertTrue(active, "no subagent is loaded from .claude/agents/")

    def test_gatekeeper_is_the_active_one(self):
        # One agent for now, deliberately. The rest are drafts until there is work for them.
        self.assertTrue(
            (AGENTS / "fabric-gatekeeper.md").exists(),
            "fabric-gatekeeper is the single active agent",
        )

    def test_required_frontmatter_fields(self):
        for path in agent_files():
            with self.subTest(agent=path.stem):
                fields, _ = parse(path)
                for required in ("name", "description", "tools"):
                    self.assertIn(required, fields, f"{path.name} lacks {required}")

    def test_name_matches_filename(self):
        # The harness addresses an agent by name; a mismatch makes it unreachable.
        for path in agent_files():
            with self.subTest(agent=path.stem):
                fields, _ = parse(path)
                self.assertEqual(fields["name"], path.stem)

    def test_no_subagent_has_write_tools(self):
        for path in agent_files():
            with self.subTest(agent=path.stem):
                tools = {t.strip() for t in parse(path)[0]["tools"].split(",")}
                offenders = tools & WRITE_TOOLS
                self.assertFalse(
                    offenders,
                    f"{path.name} has {sorted(offenders)}. Subagents propose; the caller "
                    f"applies. Granting write tools removes the only guarantee a subagent "
                    f"actually enforces.",
                )

    def test_description_says_when_to_use_it(self):
        # The description is how the main session decides to delegate. A vague one means
        # the agent is either never used or used for the wrong thing.
        for path in agent_files():
            with self.subTest(agent=path.stem):
                description = parse(path)[0]["description"]
                self.assertGreater(
                    len(description), 80,
                    f"{path.name}: description too short to route on",
                )

    def test_dml_agent_states_the_rules_that_cannot_be_code(self):
        """The DML agent exists to hold constraints compaction would erode.

        If these sentences vanish, the agent is just a normal session with extra steps —
        and the framework's claim that judgement is protected stops being true.
        """
        path = find_agent("fabric-dml")
        self.assertIsNotNone(path, "fabric-dml must exist, active or as a draft")
        # Strip markdown emphasis so the assertion is about the rule being stated, not
        # about whether it happens to be written with backticks.
        body = re.sub(r"[`*_]", "", parse(path)[1].lower())

        for phrase, why in [
            ("plan before apply", "the core cycle"),
            ("without a where", "the unbounded-mutation refusal"),
            ("restore table", "the recovery path must be surfaced, not merely recorded"),
            ("stop", "surprising plans must halt rather than be adjusted"),
        ]:
            with self.subTest(rule=phrase):
                self.assertIn(phrase, body, f"fabric-dml must state: {why}")

    def test_readers_are_told_not_to_pull_bulk_into_context(self):
        # The whole economy argument collapses if a read subagent cats large files.
        for name in ("fabric-reader", "drift-reconciler", "fabric-gatekeeper"):
            path = find_agent(name)
            if path is None:
                continue
            with self.subTest(agent=name):
                body = parse(path)[1].lower()
                self.assertTrue(
                    "out/" in body or "--out" in body,
                    f"{name} must describe persisting bulk output to disk",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
