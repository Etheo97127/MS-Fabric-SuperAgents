"""Azure DevOps adapter, via the `az repos` CLI extension.

Not yet exercised: this repo is on GitHub, and the Azure DevOps move waits on a service
principal (Fabric git connections for Azure DevOps do not accept user principals). The
adapter is written now so the migration is a profile change rather than a redesign, but
treat it as unverified until it has actually run.

See doc/framework/verification-register.md V11.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base import PullRequest, VcsAdapter, VcsUnavailable


class AzureDevOpsAdapter(VcsAdapter):
    name = "azuredevops"

    def _az(self, *args: str) -> str:
        if not shutil.which("az"):
            raise VcsUnavailable(
                "the Azure CLI is not installed.\n"
                "  winget install Microsoft.AzureCLI\n"
                "  az extension add --name azure-devops"
            )
        proc = subprocess.run(
            ["az", "repos", *args, "--output", "json"],
            capture_output=True, text=True, timeout=60, cwd=self.root,
        )
        if proc.returncode != 0:
            raise VcsUnavailable(f"az repos {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    @staticmethod
    def _to_pr(data: dict) -> PullRequest:
        approvers = tuple(
            (r.get("displayName") or "?")
            for r in (data.get("reviewers") or []) if (r.get("vote") or 0) > 0
        )
        status = (data.get("status") or "").lower()
        return PullRequest(
            number=str(data.get("pullRequestId", "")),
            url=data.get("url", ""),
            state={"completed": "merged", "abandoned": "closed"}.get(status, status),
            approvals=len(approvers),
            approvers=approvers,
        )

    def create_pr(self, *, head: str, base: str, title: str, body: str) -> PullRequest:
        raw = self._az("pr", "create", "--source-branch", head, "--target-branch", base,
                       "--title", title, "--description", body)
        return self._to_pr(json.loads(raw))

    def get_pr(self, identifier: str) -> PullRequest | None:
        try:
            return self._to_pr(json.loads(self._az("pr", "show", "--id", identifier)))
        except VcsUnavailable:
            return None

    def pr_for_branch(self, branch: str) -> PullRequest | None:
        raw = self._az("pr", "list", "--source-branch", branch, "--status", "all")
        items = json.loads(raw or "[]")
        return self._to_pr(items[0]) if items else None
