"""Human-readable table + JSON output helpers.

Stdlib-only. No rich/click/tabulate dependency. Pattern adapted from
reconciler/status.py::run_status which already does fixed-width columns
with f-strings -- generalized here so every subcommand uses one helper.

`--quiet` is honored by the print_* helpers: when quiet is True, stdout
is suppressed but stderr is left alone (callers route errors there).
"""
from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Optional


def print_json(obj: Any, quiet: bool = False, fp=None) -> None:
    """Emit `obj` as a single JSON document on stdout.

    sort_keys=True so machine consumers get stable output across runs.
    Honors quiet by no-op-ing the write.

    `default=str` coerces values that json.dump cannot natively serialise
    (e.g. Path objects, datetime). When sort_keys=True hits a dict that
    mixes str and non-str keys, the comparison would raise TypeError; we
    fall back to a non-sorted dump so a defensive caller still gets
    output rather than an unhandled exception. (E18 in the 2026-04-27
    edge-case audit.)
    """
    if quiet:
        return
    out = fp if fp is not None else sys.stdout
    try:
        json.dump(obj, out, sort_keys=True, indent=2, default=str)
    except TypeError:
        # Mixed-key-type dict somewhere in the tree -- json.dump tries
        # to sort and raises. Re-dump without sort_keys so the operator
        # still sees their data; insertion order is preserved (CPython
        # dict invariant since 3.7).
        json.dump(obj, out, indent=2, default=str)
    out.write("\n")


def print_table(
    rows: Iterable[dict[str, Any]],
    columns: Optional[Iterable[str]] = None,
    quiet: bool = False,
    fp=None,
) -> None:
    """Render `rows` as a column-aligned table on stdout.

    `columns` selects + orders the keys to print; defaults to the keys of
    the first row in insertion order. Missing keys render as empty
    strings. Cell values are stringified via str() before width math.

    No bordering, no truncation -- the operator's terminal handles wrap.
    Two spaces between columns to match the reconciler/status output style.
    """
    rows_list = list(rows)
    if quiet or not rows_list:
        return

    if columns is None:
        columns = list(rows_list[0].keys())
    else:
        columns = list(columns)

    # Stringify everything once so width math and printing share the same
    # representation.
    table: list[list[str]] = []
    for r in rows_list:
        table.append([str(r.get(c, "")) for c in columns])

    # Column widths = max(header, max(cell)) per column.
    widths = [len(c) for c in columns]
    for row in table:
        for i, cell in enumerate(row):
            if len(cell) > widths[i]:
                widths[i] = len(cell)

    out = fp if fp is not None else sys.stdout
    # Header.
    header_cells = [columns[i].ljust(widths[i]) for i in range(len(columns))]
    out.write("  ".join(header_cells).rstrip() + "\n")
    # Underline (matches the reconciler/status style which uses an explicit
    # row of dashes; we render it from the widths so it stays aligned).
    underline_cells = ["-" * widths[i] for i in range(len(columns))]
    out.write("  ".join(underline_cells).rstrip() + "\n")
    # Data.
    for row in table:
        cells = [row[i].ljust(widths[i]) for i in range(len(columns))]
        out.write("  ".join(cells).rstrip() + "\n")


def print_line(text: str, quiet: bool = False, fp=None) -> None:
    """Single-line stdout helper that respects --quiet."""
    if quiet:
        return
    out = fp if fp is not None else sys.stdout
    out.write(text + "\n")


def eprint(text: str) -> None:
    """Always-on stderr helper. Errors must never be silenced by --quiet."""
    sys.stderr.write(text + "\n")
