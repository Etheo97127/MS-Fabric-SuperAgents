"""Run a fabctl command without depending on PYTHONPATH being set.

pre-commit's `language: system` hooks do not accept an `env:` key, so a hook cannot export
PYTHONPATH for the subprocess it runs. Rather than encoding a sys.path hack into each hook's
entry line, they all go through here.

    python tools/ci/run_fabctl.py ledger verify --quiet
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fabctl.cli import main  # noqa: E402

raise SystemExit(main(sys.argv[1:]))
