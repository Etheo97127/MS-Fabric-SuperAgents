"""Version-control host adapters.

Everything the framework does with git is plain git: branches, commits, trailers, tags.
The single exception is opening and querying pull requests, which has no portable
command. That exception is confined to this package, selected by the `vcs:` key in the
active profile — so moving from GitHub to Azure DevOps changes one config line and
touches no skill.
"""

from .base import PullRequest, VcsAdapter, get_adapter

__all__ = ["PullRequest", "VcsAdapter", "get_adapter"]
