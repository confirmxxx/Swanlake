"""Entry point for `python -m swanlake` and the `swanlake` console script."""
from __future__ import annotations

import sys

from swanlake.cli import main


if __name__ == "__main__":
    sys.exit(main())
