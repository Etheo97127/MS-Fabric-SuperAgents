"""Thin subprocess wrapper around the Microsoft Fabric CLI (``fab``).

fabctl never speaks HTTP. Everything reaches Fabric through ``fab``, which already handles
auth, token refresh and endpoint selection. This module exists only to make those calls
uniform, capture them for the ledger, and keep the one genuinely unverified behaviour —
long-running operation handling — quarantined in a single function.

See doc/framework/verification-register.md: V2 (LRO), V6 (native git commands),
V10 (token lifetime).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

FAB = "fab"
DEFAULT_TIMEOUT = 300


class FabNotInstalled(Exception):
    pass


class FabError(Exception):
    def __init__(self, result: "FabResult"):
        self.result = result
        super().__init__(
            f"`{result.display}` failed with exit {result.returncode}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


@dataclass
class FabResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    parsed: Any = None
    #: Headers, when the call asked for them. `fab api --show_headers` prints them; we
    #: parse out only what LRO polling needs.
    headers: dict[str, str] = field(default_factory=dict)
    #: HTTP status from `fab api`'s response envelope, when there was one.
    http_status: int | None = None
    #: `fab`'s own outcome field in json mode ("Success" / "Failure").
    cli_status: str | None = None

    @property
    def ok(self) -> bool:
        """Success means the HTTP call succeeded, not merely that the process exited 0.

        `fab api` has been observed exiting non-zero on a 200 (a broken pipe suffices), so
        where an HTTP status is available it is the authority.
        """
        if self.http_status is not None:
            return 200 <= self.http_status < 300
        if self.cli_status is not None:
            return self.cli_status.lower() == "success"
        return self.returncode == 0

    @property
    def display(self) -> str:
        """The command as a human would type it — this is what lands in the ledger.

        Never include argument values that could carry a secret; callers pass request
        bodies via file, not inline, for exactly this reason.
        """
        return " ".join(self.argv)


def available() -> bool:
    return shutil.which(FAB) is not None


def run(
    args: list[str],
    *,
    json_output: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    check: bool = True,
) -> FabResult:
    """Invoke ``fab`` and capture everything needed for an audit record."""
    if not available():
        raise FabNotInstalled(
            "`fab` is not on PATH. Install it with:\n"
            "    pip install ms-fabric-cli\n"
            "then authenticate once with:\n"
            "    fab auth login"
        )

    argv = [FAB, *args]
    if json_output and "--output_format" not in args:
        argv += ["--output_format", "json"]

    import time
    started = time.monotonic()
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    result = FabResult(
        argv=argv,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        duration_ms=duration_ms,
    )

    if json_output and result.stdout.strip():
        try:
            result.parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            # `fab` sometimes prefixes human text before the JSON body. Recover the
            # first balanced object rather than discarding the whole response.
            result.parsed = _first_json_object(result.stdout)
        result.parsed = _unwrap(result)

    if check and not result.ok:
        raise FabError(result)
    return result


def _unwrap(result: FabResult) -> Any:
    """Strip `fab`'s response envelopes and surface the HTTP status they carry.

    Verified against fab 1.7.0 by running it; none of this is in the docs. There are two
    envelopes, and which one you get depends on the output format:

    text mode (no --output_format)::

        {"status_code": 200, "text": {"value": [...]}}

    json mode (--output_format json, which this module always passes)::

        {"timestamp": ..., "status": "Success", "command": "api",
         "result": {"data": [{"status_code": 200, "text": {"value": [...]}}]}}

    Three consequences, each a trap:

    * A JMESPath `-q` must address the envelope, e.g. ``text.value[]`` in text mode.
      Querying ``value[]`` matches nothing and prints ``None`` — wrong, and quietly so.
    * The HTTP status is in the body, not the exit code. `fab api` has been observed
      exiting non-zero on a 200, so ``status_code`` is the authority.
    * ``result.data`` is a list even for a single call, presumably to allow batching.

    Callers get the innermost payload and never see any of this.
    """
    parsed = result.parsed
    if not isinstance(parsed, dict):
        return parsed

    # json mode: {"status": ..., "result": {"data": [ ...envelopes... ]}}
    if "result" in parsed and "command" in parsed:
        result.cli_status = parsed.get("status")
        data = (parsed.get("result") or {}).get("data")
        if isinstance(data, list) and data:
            parsed = data[0]
        elif isinstance(data, dict):
            parsed = data
        else:
            return data if data is not None else parsed

    # both modes converge here: {"status_code": ..., "text": ...}
    if isinstance(parsed, dict) and "status_code" in parsed:
        result.http_status = parsed.get("status_code")
        body = parsed.get("text", parsed)
        # A body that is a JSON string rather than an object happens on some endpoints.
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body
        return body
    return parsed


def api(
    endpoint: str,
    *,
    method: str = "get",
    body_file: str | None = None,
    audience: str = "fabric",
    show_headers: bool = False,
    query: str | None = None,
    **kwargs: Any,
) -> FabResult:
    """Call an arbitrary Fabric REST endpoint via ``fab api``.

    Request bodies go through a file, never an inline string: an inline body would be
    visible in the process table and would land verbatim in the ledger's ``command``.
    """
    args = ["api", endpoint, "-X", method, "-A", audience]
    if body_file:
        args += ["-i", body_file]
    if query:
        args += ["-q", query]
    if show_headers:
        args += ["--show_headers"]
    return run(args, **kwargs)


def _first_json_object(text: str) -> Any:
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = None
    return None


# ---------------------------------------------------------------------------
# Long-running operations — UNVERIFIED, see verification-register.md V2
# ---------------------------------------------------------------------------

LRO_STATES_PENDING = {"NotStarted", "Running", "Undetermined"}
LRO_STATES_DONE = {"Succeeded", "Completed"}
LRO_STATES_FAILED = {"Failed", "Cancelled", "Deduped"}


def looks_like_lro(result: FabResult) -> str | None:
    """Return an operation id if this response represents a pending operation.

    It is NOT established whether ``fab api`` returns the raw 202 with
    ``x-ms-operation-id`` or polls to completion itself. This function is written to be
    correct either way: if no operation id can be found, the caller treats the response
    as already complete.

    Until V2 is settled, ``fabctl sync`` reports which branch it took, so a wrong guess
    is visible rather than silent.
    """
    for key in ("x-ms-operation-id", "X-Ms-Operation-Id", "operationId", "operation_id"):
        if (value := result.headers.get(key)):
            return value
    if isinstance(result.parsed, dict):
        for key in ("operationId", "id"):
            value = result.parsed.get(key)
            status = result.parsed.get("status")
            if value and status in LRO_STATES_PENDING:
                return str(value)
    return None


def poll(operation_id: str, *, interval: int = 5, timeout: int = 900) -> dict[str, Any]:
    """Poll an operation to a terminal state.

    Raises on failure so the caller records a truthful ledger result rather than an
    optimistic one.
    """
    import time
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}

    while time.monotonic() < deadline:
        result = api(f"operations/{operation_id}")
        last = result.parsed if isinstance(result.parsed, dict) else {}
        status = last.get("status")
        if status in LRO_STATES_DONE:
            return last
        if status in LRO_STATES_FAILED:
            raise FabError(FabResult(
                argv=result.argv, returncode=1, stdout=result.stdout,
                stderr=f"operation {operation_id} ended as {status}: "
                       f"{last.get('error') or last.get('failureReason') or 'no detail'}",
                duration_ms=result.duration_ms,
            ))
        time.sleep(interval)

    raise TimeoutError(
        f"operation {operation_id} still {last.get('status', 'unknown')!r} after {timeout}s. "
        f"It may still complete; check with: fab api operations/{operation_id}"
    )
