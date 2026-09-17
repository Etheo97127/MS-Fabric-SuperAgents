"""Append-only, hash-chained audit ledger.

One JSON object per line, in ``ledger/YYYY/MM/DD.jsonl``. Each record carries ``prev``,
the digest of the record before it in the same day file, so any edit to history breaks
the chain and ``verify`` reports it.

Chain scope is deliberately one day file. A single chain across all days would need a
mutable HEAD pointer, which every concurrent agent would conflict on in git. Instead the
first record of a day carries ``prev_day``, the digest of the whole previous day file, so
days remain linked without a shared mutable file.

The ledger is tamper-EVIDENT, not tamper-proof: an actor who can write the repo can
rewrite a day file and re-chain it. Detection depends on the remote copy (branch
protection, no force-push) and on reconciliation against Fabric's own activity log.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Fields computed by the ledger itself; callers must not supply them.
_RESERVED = frozenset({"ts", "seq", "prev", "prev_day"})

SCHEMA_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical(record: dict[str, Any]) -> str:
    """Stable serialisation used for hashing and for writing lines.

    Sorted keys and no insignificant whitespace, so the digest depends on content
    alone and never on dict insertion order.
    """
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


@dataclass
class Actor:
    type: str = "agent"          # agent | human | ci
    model: str | None = None
    surface: str | None = None   # claude-code | copilot | pipeline | cli
    principal: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def detect(cls) -> "Actor":
        """Infer the actor from environment, falling back to an honest 'unknown'.

        Never guesses a model name: an unattributed record is better than a wrong one.
        """
        env = os.environ
        surface = None
        if env.get("CLAUDE_PROJECT_DIR") or env.get("CLAUDECODE"):
            surface = "claude-code"
        elif env.get("GITHUB_COPILOT_AGENT") or env.get("COPILOT_AGENT"):
            surface = "copilot"
        elif env.get("CI") or env.get("TF_BUILD") or env.get("GITHUB_ACTIONS"):
            surface = "pipeline"
        return cls(
            type="ci" if surface == "pipeline" else ("agent" if surface else "human"),
            model=env.get("FABCTL_AGENT_MODEL"),
            surface=surface or "cli",
            principal=env.get("FABCTL_PRINCIPAL") or env.get("USER") or env.get("USERNAME"),
        )


@dataclass
class Ledger:
    root: Path
    _run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def run_id(self) -> str:
        return self._run_id

    # ---------- paths ----------

    def day_path(self, when: datetime | None = None) -> Path:
        when = when or _utcnow()
        return self.root / f"{when:%Y}" / f"{when:%m}" / f"{when:%d}.jsonl"

    def _previous_day_path(self, current: Path) -> Path | None:
        """Most recent existing day file strictly before ``current``, if any."""
        days = sorted(p for p in self.root.glob("*/*/*.jsonl"))
        earlier = [p for p in days if p != current and str(p) < str(current)]
        return earlier[-1] if earlier else None

    # ---------- reading ----------

    def read_day(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise LedgerCorrupt(f"{path}:{lineno} is not valid JSON: {exc}") from exc
        return out

    def read_all(self) -> Iterator[dict[str, Any]]:
        for path in sorted(self.root.glob("*/*/*.jsonl")):
            yield from self.read_day(path)

    # ---------- writing ----------

    def append(self, record: dict[str, Any], when: datetime | None = None) -> dict[str, Any]:
        """Append one record, chaining it to the previous. Returns the stored record."""
        clashes = _RESERVED & record.keys()
        if clashes:
            raise ValueError(f"caller must not set ledger-computed fields: {sorted(clashes)}")

        when = when or _utcnow()
        path = self.day_path(when)
        path.parent.mkdir(parents=True, exist_ok=True)

        existing = self.read_day(path)
        stored = dict(record)
        stored["ts"] = when.isoformat().replace("+00:00", "Z")
        stored["seq"] = len(existing)
        stored.setdefault("schema", SCHEMA_VERSION)
        stored.setdefault("run_id", self._run_id)

        if existing:
            stored["prev"] = digest(existing[-1])
        else:
            stored["prev"] = None
            prev_day = self._previous_day_path(path)
            if prev_day is not None:
                stored["prev_day"] = digest_file(prev_day)

        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical(stored) + "\n")
        return stored

    # ---------- verification ----------

    def verify(self) -> list[str]:
        """Walk every day file and return a list of problems. Empty means intact."""
        problems: list[str] = []
        paths = sorted(self.root.glob("*/*/*.jsonl"))

        for path in paths:
            try:
                records = self.read_day(path)
            except LedgerCorrupt as exc:
                problems.append(str(exc))
                continue

            for i, rec in enumerate(records):
                where = f"{path.name}:{i}"

                if rec.get("seq") != i:
                    problems.append(f"{where}: seq is {rec.get('seq')!r}, expected {i}")

                expected_prev = digest(records[i - 1]) if i else None
                if rec.get("prev") != expected_prev:
                    problems.append(
                        f"{where}: broken chain — prev is {rec.get('prev')!r}, "
                        f"expected {expected_prev!r}. A record before this one was altered "
                        f"or removed."
                    )

            if records:
                prev_day = self._previous_day_path(path)
                claimed = records[0].get("prev_day")
                if prev_day is not None and claimed is not None:
                    actual = digest_file(prev_day)
                    if claimed != actual:
                        problems.append(
                            f"{path.name}:0: prev_day does not match {prev_day.name} — "
                            f"that day file was altered after this one was written."
                        )
        return problems


class LedgerCorrupt(Exception):
    """A ledger file could not be parsed at all."""
