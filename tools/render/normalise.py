"""Deterministic canonicalisation of repo files. Bytes in, bytes out.

This is code, never an instruction. No skill tells an agent how to format a notebook; the
skill says "run fabctl normalise", the pre-commit hook runs it anyway if the agent forgets,
and CI re-runs it and fails on mismatch. Anything an agent could paraphrase, it will
eventually paraphrase wrong.

Two problems are being solved, and only one of them is solvable today:

1. Agent-to-agent nondeterminism — Claude and Copilot emitting different bytes for the
   same logical content. Fully solvable now, and the rules below do it.

2. Matching Fabric's own serializer, so that a round-trip produces an empty diff. NOT yet
   solvable: the rules have to be *derived* from what Fabric actually emits. Until that
   calibration runs (verification-register V1), this module deliberately applies only
   transformations that are safe regardless of what Fabric does.

The conservative choice is the correct one here. A normaliser that guesses at Fabric's
key ordering and guesses wrong turns every sync into a spurious diff — worse than not
normalising at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BOM = "﻿"


@dataclass(frozen=True)
class Change:
    path: Path
    rule: str

    def __str__(self) -> str:
        return f"{self.path}: {self.rule}"


# ---------------------------------------------------------------------------
# Universally safe rules — active now
#
# Every rule here is idempotent and cannot change how Fabric parses a file. They fix
# exactly the differences that arise from different editors, agents and platforms
# touching the same file.
# ---------------------------------------------------------------------------

def strip_bom(text: str) -> str:
    return text[1:] if text.startswith(BOM) else text


def normalise_eol(text: str) -> str:
    """LF everywhere. Git on Windows otherwise produces diffs that are pure line endings."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_trailing_whitespace(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.split("\n"))


def single_final_newline(text: str) -> str:
    return text.rstrip("\n") + "\n" if text.strip() else ""


SAFE_TEXT_RULES: list[tuple[str, Callable[[str], str]]] = [
    ("strip BOM", strip_bom),
    ("CRLF -> LF", normalise_eol),
    ("strip trailing whitespace", strip_trailing_whitespace),
    ("single final newline", single_final_newline),
]


def normalise_text(text: str) -> tuple[str, list[str]]:
    applied: list[str] = []
    for name, rule in SAFE_TEXT_RULES:
        new = rule(text)
        if new != text:
            applied.append(name)
            text = new
    return text, applied


def normalise_json(text: str) -> tuple[str, list[str]]:
    """Re-emit JSON with stable indentation, PRESERVING key order.

    Key order is deliberately left alone. Fabric writes ``.platform`` and
    ``variables.json`` in an order we have not yet observed, and reordering keys to match
    a guess would create exactly the round-trip diff this module exists to prevent.
    Revisit once V1 is settled.
    """
    text, applied = normalise_text(text)
    if not text.strip():
        return text, applied
    try:
        data = json.loads(text)  # object_pairs_hook default preserves order in dicts
    except json.JSONDecodeError:
        return text, applied      # leave malformed JSON alone; the schema check reports it
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if rendered != text:
        applied.append("reindent JSON (2 spaces, key order preserved)")
    return rendered, applied


# ---------------------------------------------------------------------------
# Calibration-pending rules — NOT active
#
# These are the transformations that would make the repo form byte-identical to Fabric's.
# Each needs evidence from the calibration loop before it can be turned on:
#
#     fabctl sync push --env scratch
#     fab export <item> -o ./oracle/
#     diff ./oracle/ ./fabric/          # each difference becomes one rule here
#
# Do not populate this from documentation or inference. Only from observed output.
# See doc/framework/verification-register.md V1.
# ---------------------------------------------------------------------------

CALIBRATION_PENDING: dict[str, str] = {
    "notebook.cell_marker_form": "exact spelling and spacing of Fabric's cell markers",
    "notebook.metadata_block": "field order and formatting of the notebook header block",
    "notebook.blank_lines_between_cells": "how many blank lines Fabric emits between cells",
    "platform.key_order": "key order Fabric writes in .platform",
    "variable_library.value_set_key_order": "key order in valueSets/*.json",
}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Callable[[str], tuple[str, list[str]]]] = {
    ".py": normalise_text,
    ".json": normalise_json,
    ".yaml": normalise_text,
    ".yml": normalise_text,
    ".md": normalise_text,
}

#: Files Fabric owns that carry no extension.
NAMED_HANDLERS: dict[str, Callable[[str], tuple[str, list[str]]]] = {
    ".platform": normalise_json,
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", "out", ".venv", "venv"}


def handler_for(path: Path):
    return NAMED_HANDLERS.get(path.name) or HANDLERS.get(path.suffix.lower())


def normalise_file(path: Path, *, write: bool = True) -> list[Change]:
    handler = handler_for(path)
    if handler is None:
        return []
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    result, applied = handler(original)
    if not applied or result == original:
        return []
    if write:
        path.write_text(result, encoding="utf-8", newline="")
    return [Change(path, rule) for rule in applied]


def iter_files(roots: list[Path]):
    for root in roots:
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and not any(part in SKIP_DIRS for part in path.parts):
                yield path


def normalise_paths(roots: list[Path], *, write: bool = True) -> list[Change]:
    changes: list[Change] = []
    for path in iter_files(roots):
        changes.extend(normalise_file(path, write=write))
    return changes


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="normalise",
        description="Canonicalise repo files deterministically.",
    )
    parser.add_argument("paths", nargs="*", default=["."], type=Path)
    parser.add_argument("--check", action="store_true",
                        help="report what would change and exit non-zero; write nothing")
    args = parser.parse_args(argv)

    changes = normalise_paths([Path(p) for p in args.paths], write=not args.check)

    for change in changes:
        print(change)

    if args.check and changes:
        print(f"\n{len(changes)} file(s) are not canonical. Run: fabctl normalise")
        return 1
    if changes:
        print(f"\nnormalised {len({c.path for c in changes})} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
