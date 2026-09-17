"""CI/pre-commit guard: ledger day files may be appended to, never rewritten.

The hash chain proves a day file is internally consistent. It does not prove nobody
rewrote a whole file and re-chained it — recomputing the chain is trivial for anyone who
can run this repo's own code. This check closes that gap the only way a repo can: by
comparing the staged file against what is already committed, and rejecting any change
that is not a pure append.

That still is not tamper-proof. An actor who can force-push to the remote defeats it.
Genuine non-repudiation needs the ledger written by an identity the agent does not
control. This makes tampering something you have to do deliberately and visibly rather
than something that can happen quietly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def git(*args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout


def committed_lines(path: str, ref: str = "HEAD") -> list[str] | None:
    """The file's lines as of ``ref``, or None when it does not exist there yet."""
    code, out = git("show", f"{ref}:{path}")
    if code != 0:
        return None
    return out.splitlines()


def staged_lines(path: str) -> list[str]:
    code, out = git("show", f":{path}")
    if code != 0:
        return Path(path).read_text(encoding="utf-8").splitlines()
    return out.splitlines()


def check_file(path: str) -> list[str]:
    old = committed_lines(path)
    if old is None:
        return []  # brand new day file: everything in it is an append

    new = staged_lines(path)

    if len(new) < len(old):
        return [f"{path}: {len(old) - len(new)} record(s) removed — the ledger is append-only"]

    problems = []
    for i, (before, after) in enumerate(zip(old, new)):
        if before != after:
            problems.append(
                f"{path}:{i + 1}: an existing record was modified.\n"
                f"    was: {before[:110]}\n"
                f"    now: {after[:110]}"
            )
    return problems[:5]


def main(argv: list[str]) -> int:
    code, out = git("diff", "--cached", "--name-only", "--", "ledger/")
    paths = [p for p in out.splitlines() if p.endswith(".jsonl")]
    if not paths:
        return 0

    problems: list[str] = []
    for path in paths:
        problems.extend(check_file(path))

    if not problems:
        return 0

    print("LEDGER APPEND-ONLY VIOLATION", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(
        "\nThe audit ledger records what agents did. Rewriting it destroys the only\n"
        "evidence of that. If a record is genuinely wrong, append a correcting record\n"
        "that references it — do not edit history.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
