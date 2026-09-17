"""GitHub adapter, via the `gh` CLI.

`gh` is used rather than the REST API so that authentication is whatever the developer
already has, with no token for the framework to store.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base import PullRequest, VcsAdapter, VcsUnavailable


class GitHubAdapter(VcsAdapter):
    name = "github"

    def _gh(self, *args: str) -> str:
        if not shutil.which("gh"):
            raise VcsUnavailable(
                "the GitHub CLI is not installed.\n"
                "  winget install GitHub.cli   (then: gh auth login)"
            )
        proc = subprocess.run(
            ["gh", *args, "--repo", self._repo()],
            capture_output=True, text=True, timeout=60, cwd=self.root,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            if "auth" in stderr.lower():
                raise VcsUnavailable(f"gh is not authenticated: {stderr}\n  -> gh auth login")
            raise VcsUnavailable(f"gh {' '.join(args)} failed: {stderr}")
        return proc.stdout.strip()

    def _repo(self) -> str:
        url = self.git("remote", "get-url", "origin")
        slug = url.removesuffix(".git").removeprefix("git@github.com:")
        return slug.replace("https://github.com/", "")

    @staticmethod
    def _to_pr(data: dict) -> PullRequest:
        reviews = data.get("reviews") or []
        approvers = tuple(
            (r.get("author") or {}).get("login", "?")
            for r in reviews if r.get("state") == "APPROVED"
        )
        return PullRequest(
            number=str(data.get("number", "")),
            url=data.get("url", ""),
            state=("merged" if data.get("mergedAt") else (data.get("state") or "").lower()),
            approvals=len(approvers),
            approvers=approvers,
        )

    _FIELDS = "number,url,state,mergedAt,reviews"

    def create_pr(self, *, head: str, base: str, title: str, body: str) -> PullRequest:
        self._gh("pr", "create", "--head", head, "--base", base,
                 "--title", title, "--body", body)
        pr = self.pr_for_branch(head)
        if pr is None:
            raise VcsUnavailable(f"created a PR for {head} but could not read it back")
        return pr

    def get_pr(self, identifier: str) -> PullRequest | None:
        try:
            return self._to_pr(json.loads(self._gh("pr", "view", identifier,
                                                   "--json", self._FIELDS)))
        except VcsUnavailable:
            return None

    def pr_for_branch(self, branch: str) -> PullRequest | None:
        raw = self._gh("pr", "list", "--head", branch, "--state", "all",
                       "--json", self._FIELDS, "--limit", "1")
        items = json.loads(raw or "[]")
        return self._to_pr(items[0]) if items else None
