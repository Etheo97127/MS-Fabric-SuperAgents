"""fabctl — the audited gateway between this repo and Microsoft Fabric.

Nothing mutates Fabric except through here, because this is the only point at which the
repo's own ledger can record what happened.
"""

__version__ = "0.1.0"
