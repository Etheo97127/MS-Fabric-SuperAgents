"""`fabctl trail` — read the decision history out of git.

The repository's history is the audit trail: what was decided, when, by which model, and
what it ruled out. That is only useful if it can be read back without anyone knowing the
right `git log --format` incantation, so this renders it.

Two records, deliberately separate, and the distinction matters:

  * **git** records what changed *in the repository* — decisions, code, documents.
  * **the ledger** records what happened *in Fabric* — mutations against a live workspace.

Neither is a superset of the other. A decision to change an approach leaves a commit and no
ledger record; a notebook run leaves a ledger record and no commit. `--with-ledger` shows
them interleaved, which is the only view that answers "what actually happened that day".
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SEP = "\x1e"   # record separator; safe inside commit messages
FIELD = "\x1f"

TRAILER_KEYS = ("Agent-Run-Id", "Agent-Model", "Agent-Surface", "Contract",
                "Ledger-Ref", "Human-Approver")


@dataclass
class Entry:
    sha: str
    date: str
    author: str
    subject: str
    body: str
    trailers: dict[str, str] = field(default_factory=dict)

    @property
    def by_agent(self) -> bool:
        return bool(self.trailers.get("Agent-Run-Id"))

    @property
    def actor(self) -> str:
        if not self.by_agent:
            return f"{self.author} (human)"
        model = self.trailers.get("Agent-Model", "agent")
        surface = self.trailers.get("Agent-Surface", "?")
        return f"{model} via {surface}"

    @property
    def rationale(self) -> list[str]:
        """Body paragraphs with the trailer block stripped.

        The rationale is the point of the entry; the trailers are metadata about it.
        """
        out = []
        for para in self.body.split("\n\n"):
            para = para.strip()
            if not para or any(para.startswith(f"{k}:") for k in TRAILER_KEYS):
                continue
            if para.startswith("Co-Authored-By:"):
                continue
            out.append(para)
        return out


def _log(root: Path, extra: list[str]) -> list[Entry]:
    fmt = FIELD.join(["%H", "%ad", "%an", "%s", "%b"]) + SEP
    proc = subprocess.run(
        ["git", "-C", str(root), "log", f"--format={fmt}", "--date=short", *extra],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    entries = []
    for chunk in proc.stdout.split(SEP):
        if not chunk.strip():
            continue
        parts = chunk.lstrip("\n").split(FIELD)
        if len(parts) < 5:
            continue
        sha, date, author, subject, body = parts[:5]
        trailers = {}
        for line in body.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() in TRAILER_KEYS:
                trailers[key.strip()] = value.strip()
        entries.append(Entry(sha[:8], date, author, subject, body, trailers))
    return entries


def _ledger_by_date(root: Path) -> dict[str, list[dict]]:
    from .ledger import Ledger
    out: dict[str, list[dict]] = {}
    for record in Ledger(root=root / "ledger").read_all():
        day = (record.get("ts") or "")[:10]
        out.setdefault(day, []).append(record)
    return out


def render(root: Path, *, limit: int, agents_only: bool, with_ledger: bool,
           since: str | None, path: str | None) -> int:
    extra: list[str] = [f"-{limit}"]
    if since:
        extra += [f"--since={since}"]
    if path:
        extra += ["--", path]

    entries = _log(root, extra)
    if agents_only:
        entries = [e for e in entries if e.by_agent]

    if not entries:
        print("no matching history")
        return 0

    ledger = _ledger_by_date(root) if with_ledger else {}
    current_day = None

    for entry in entries:
        if entry.date != current_day:
            current_day = entry.date
            print(f"\n{'=' * 78}\n{entry.date}\n{'=' * 78}")
            for record in ledger.get(entry.date, []):
                status = (record.get("result") or {}).get("status", "?")
                print(f"  [fabric] {status:<10} {record.get('action','?'):<34} "
                      f"{record.get('intent','')[:40]}")

        print(f"\n  {entry.sha}  {entry.subject}")
        print(f"            {entry.actor}")
        for para in entry.rationale:
            for line in para.splitlines():
                print(f"            {line}")
            print()
        if contract := entry.trailers.get("Contract"):
            print(f"            contract: {contract}")

    print(f"\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}.")
    if not with_ledger:
        print("Add --with-ledger to interleave what happened in Fabric on the same days.")
    return 0


def summary(root: Path) -> int:
    """Who has been changing this repository, and how much of it was an agent."""
    entries = _log(root, ["-500"])
    if not entries:
        print("no history")
        return 0

    by_actor: dict[str, int] = {}
    for entry in entries:
        by_actor[entry.actor] = by_actor.get(entry.actor, 0) + 1

    agent_commits = sum(1 for e in entries if e.by_agent)
    days = {e.date for e in entries}

    print(f"{len(entries)} commits across {len(days)} day(s)")
    print(f"{agent_commits} by an agent "
          f"({agent_commits * 100 // max(len(entries), 1)}%), "
          f"{len(entries) - agent_commits} by a human\n")
    for actor, count in sorted(by_actor.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  {actor}")

    untraced = [e for e in entries if e.by_agent and not e.rationale]
    if untraced:
        print(f"\n{len(untraced)} agent commit(s) carry no rationale — the trail records "
              f"that they happened but not why:")
        for entry in untraced[:5]:
            print(f"  {entry.sha}  {entry.subject}")
    return 0


def latest_run_id(root: Path) -> str | None:
    for entry in _log(root, ["-20"]):
        if run_id := entry.trailers.get("Agent-Run-Id"):
            return run_id
    return None


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")
