"""The VCS-agnostic surface.

Git itself is portable, so branching, committing, trailers and tags use plain `git` and
live nowhere near this package. Only pull requests need a host API, so only pull requests
are abstracted here.

Keeping the abstraction this narrow is the point. A wider one would tempt callers to
route ordinary git operations through an adapter, and the framework would quietly become
platform-coupled again.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PullRequest:
    number: str
    url: str
    state: str          # open | merged | closed
    approvals: int = 0
    approvers: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        return self.approvals > 0


class VcsUnavailable(Exception):
    """The host CLI is missing or unauthenticated. Message carries the fix."""


class VcsAdapter(ABC):
    """Pull-request operations. Everything else is plain git."""

    name: str

    def __init__(self, root: Path):
        self.root = root

    @abstractmethod
    def create_pr(self, *, head: str, base: str, title: str, body: str) -> PullRequest: ...

    @abstractmethod
    def get_pr(self, identifier: str) -> PullRequest | None: ...

    @abstractmethod
    def pr_for_branch(self, branch: str) -> PullRequest | None: ...

    # -- shared git plumbing, identical on every host ------------------------

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True, timeout=60,
        )
        if check and proc.returncode != 0:
            raise VcsUnavailable(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def current_branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD")

    def head_commit(self) -> str:
        return self.git("rev-parse", "HEAD")

    def is_dirty(self) -> bool:
        return bool(self.git("status", "--porcelain"))

    def commit_with_trailers(self, message: str, trailers: dict[str, str]) -> str:
        """Commit with trailers appended.

        Trailers are what make git itself queryable for agent activity — for example
        `git log --grep='Agent-Run-Id'` — and they work identically on every host, which
        is why attribution lives here rather than in any host API.
        """
        body = message.rstrip() + "\n\n" + "\n".join(
            f"{k}: {v}" for k, v in trailers.items() if v
        )
        self.git("commit", "-m", body)
        return self.head_commit()

    def tag(self, name: str, message: str) -> None:
        """Annotated tag. Mapping versions are addressed by tag, so it must not be light."""
        self.git("tag", "-a", name, "-m", message)


def get_adapter(vcs: str, root: Path) -> VcsAdapter:
    if vcs == "github":
        from .github import GitHubAdapter
        return GitHubAdapter(root)
    if vcs == "azuredevops":
        from .azuredevops import AzureDevOpsAdapter
        return AzureDevOpsAdapter(root)
    raise ValueError(f"unknown vcs {vcs!r}; expected 'github' or 'azuredevops'")
