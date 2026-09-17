"""Profile loading — topology is data, not code.

A profile declares the scratch workspace and the environment chain. Switching between a
PoC (dev -> prod) and a full project (dev -> test -> prod) is a one-line change here, and
every verb adapts, because nothing else in fabctl hardcodes a workspace or a branch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

VALID_GATES = {"none", "pull_request"}
VALID_VCS = {"github", "azuredevops"}


class ConfigError(Exception):
    """A profile is missing, malformed, or internally inconsistent."""


def repo_root(start: Path | None = None) -> Path:
    """Walk up to the directory holding .git. Everything else is relative to it."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ConfigError(f"no git repository found at or above {here}")


@dataclass(frozen=True)
class Environment:
    id: str
    workspace: str
    branch: str
    value_set: str
    gate: str = "none"
    require_checks: tuple[str, ...] = ()

    @property
    def gated(self) -> bool:
        return self.gate != "none"


@dataclass(frozen=True)
class Scratch:
    workspace: str
    git_connected: bool = False
    ttl_days: int = 7


@dataclass(frozen=True)
class Profile:
    name: str
    vcs: str
    environments: tuple[Environment, ...]
    scratch: Scratch | None = None
    fabric_dir: str = "fabric"
    blast_radius: int = 10

    def env(self, env_id: str) -> Environment:
        for e in self.environments:
            if e.id == env_id:
                return e
        known = ", ".join(e.id for e in self.environments)
        raise ConfigError(f"unknown environment {env_id!r}. This profile defines: {known}")

    @property
    def lowest(self) -> Environment:
        """The first environment in the chain — the one agents may commit to directly."""
        return self.environments[0]

    def next_after(self, env_id: str) -> Environment | None:
        ids = [e.id for e in self.environments]
        i = ids.index(env_id)
        return self.environments[i + 1] if i + 1 < len(self.environments) else None


def _expand(value: str) -> str:
    """Expand ${USER}/${USERNAME} so per-developer scratch workspaces work unmodified."""
    return os.path.expandvars(value).replace(
        "${USER}", os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    )


def load(profile_name: str | None = None, *, root: Path | None = None) -> Profile:
    root = root or repo_root()
    name = profile_name or os.environ.get("FABCTL_PROFILE") or "poc"
    path = root / "profiles" / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in (root / "profiles").glob("*.yaml"))
        raise ConfigError(
            f"no profile {name!r} at {path}. Available: {', '.join(available) or 'none'}"
        )

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    vcs = raw.get("vcs")
    if vcs not in VALID_VCS:
        raise ConfigError(
            f"{path}: vcs is {vcs!r}, must be one of {sorted(VALID_VCS)}. This is the only "
            f"platform-coupled key in the whole framework."
        )

    envs: list[Environment] = []
    for entry in raw.get("environments") or []:
        missing = {"id", "workspace", "branch", "value_set"} - entry.keys()
        if missing:
            raise ConfigError(f"{path}: environment is missing {sorted(missing)}")
        gate = entry.get("gate", "none")
        if gate not in VALID_GATES:
            raise ConfigError(f"{path}: gate is {gate!r}, must be one of {sorted(VALID_GATES)}")
        envs.append(Environment(
            id=entry["id"],
            workspace=_expand(entry["workspace"]),
            branch=entry["branch"],
            value_set=entry["value_set"],
            gate=gate,
            require_checks=tuple(entry.get("require_checks") or ()),
        ))

    if not envs:
        raise ConfigError(f"{path}: at least one environment is required")

    scratch = None
    if (s := raw.get("scratch")):
        scratch = Scratch(
            workspace=_expand(s["workspace"]),
            git_connected=bool(s.get("git_connected", False)),
            ttl_days=int(s.get("ttl_days", 7)),
        )
        if scratch.git_connected:
            raise ConfigError(
                f"{path}: scratch.git_connected must be false. The scratch workspace exists "
                f"precisely so direct writes are allowed; connecting it to git would make "
                f"every one of those writes a drift defect."
            )

    return Profile(
        name=raw.get("name", name),
        vcs=vcs,
        environments=tuple(envs),
        scratch=scratch,
        fabric_dir=raw.get("fabric_dir", "fabric"),
        blast_radius=int(raw.get("blast_radius", 10)),
    )
