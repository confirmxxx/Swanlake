"""Single source of truth for swanlake CLI exit codes.

The CLI follows the spec section A9 mapping: clean=0, drift=1, alarm=2.
USAGE shares 2 with ALARM (argparse convention). NOT_IMPLEMENTED=3 is
reserved for stub adapters (sdk in v0.2, full bench until v0.2.x).
"""
from __future__ import annotations

# Posture / health exit codes.
CLEAN = 0
DRIFT = 1
ALARM = 2

# Argparse / user-error exit code (intentionally collides with ALARM; argparse
# uses 2 by convention and the spec accepts the overlap because both signal
# "do not proceed" to a calling shell).
USAGE = 2

# Reserved for stub subcommands -- e.g. `swanlake adapt sdk` until a real
# adopter exists. Distinguishable from ALARM so callers can tell "feature
# missing" from "alarm fired".
NOT_IMPLEMENTED = 3
