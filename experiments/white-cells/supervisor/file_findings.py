"""Module shim for `python3 -m white_cells.supervisor.file_findings`.

The actual implementation lives in `auto_triage.py`. This shim exists
so the operator can invoke the documented module path without
remembering the filename.
"""
from supervisor.auto_triage import main


if __name__ == "__main__":
    raise SystemExit(main())
