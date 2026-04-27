"""Tests for swanlake.output -- print_json / print_table / print_line / eprint.

E30 in docs/edge-case-audit-2026-04-27.md: the module had no unit
tests; formatting was exercised only indirectly via command tests, so
silent regressions in column padding or quiet-mode behaviour would go
unnoticed by CI.
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import output


class PrintJsonTest(unittest.TestCase):
    def test_writes_sorted_compact_json(self) -> None:
        buf = io.StringIO()
        output.print_json({"b": 2, "a": 1}, fp=buf)
        text = buf.getvalue()
        # sort_keys -> "a" precedes "b" regardless of insertion order.
        self.assertEqual(text, '{\n  "a": 1,\n  "b": 2\n}\n')

    def test_quiet_suppresses_output(self) -> None:
        buf = io.StringIO()
        output.print_json({"a": 1}, quiet=True, fp=buf)
        self.assertEqual(buf.getvalue(), "")

    def test_default_str_handles_pathlike(self) -> None:
        buf = io.StringIO()
        output.print_json({"path": Path("/tmp/x")}, fp=buf)
        # default=str converts Path to its str() form.
        self.assertIn('"path": "/tmp/x"', buf.getvalue())

    def test_mixed_key_types_does_not_raise(self) -> None:
        """E18: a dict with both str and non-str keys cannot be sorted;
        the helper must fall back rather than crash the caller."""
        buf = io.StringIO()
        # No exception -- output is some valid JSON.
        output.print_json({"a": 1, 1: "b"}, fp=buf)
        text = buf.getvalue()
        # Both keys must appear (insertion order on the fallback path).
        self.assertIn('"a"', text)
        self.assertIn('"1"', text)


class PrintTableTest(unittest.TestCase):
    def test_renders_header_underline_and_rows(self) -> None:
        buf = io.StringIO()
        output.print_table(
            [{"a": "1", "b": "two"}],
            columns=["a", "b"],
            fp=buf,
        )
        text = buf.getvalue()
        # Header, underline (dashes scaled to widest cell per column),
        # data row. Trailing whitespace is stripped per the renderer.
        self.assertEqual(text, "a  b\n-  ---\n1  two\n")

    def test_empty_rows_writes_nothing(self) -> None:
        # Documented contract: no header/underline when rows are empty.
        buf = io.StringIO()
        output.print_table([], columns=["a", "b"], fp=buf)
        self.assertEqual(buf.getvalue(), "")

    def test_quiet_suppresses_table(self) -> None:
        buf = io.StringIO()
        output.print_table(
            [{"a": "1"}], columns=["a"], quiet=True, fp=buf,
        )
        self.assertEqual(buf.getvalue(), "")

    def test_columns_default_to_first_row_keys(self) -> None:
        buf = io.StringIO()
        output.print_table([{"x": "9", "y": "8"}], fp=buf)
        text = buf.getvalue()
        # First-row keys preserved in insertion order.
        first_line = text.splitlines()[0]
        self.assertTrue(first_line.startswith("x"))

    def test_missing_keys_render_empty(self) -> None:
        buf = io.StringIO()
        output.print_table(
            [{"a": "1"}, {"b": "2"}],
            columns=["a", "b"],
            fp=buf,
        )
        lines = buf.getvalue().splitlines()
        # Two data rows; row 1 has no "b", row 2 has no "a".
        self.assertEqual(len(lines), 4)  # header + underline + 2 rows
        # Last data row: empty "a" column then "2".
        last = lines[-1]
        self.assertIn("2", last)

    def test_column_widths_track_widest_cell(self) -> None:
        buf = io.StringIO()
        output.print_table(
            [{"name": "x"}, {"name": "extralong"}],
            columns=["name"],
            fp=buf,
        )
        lines = buf.getvalue().splitlines()
        # Header + underline width must accommodate the widest cell.
        self.assertEqual(len(lines[1]), len("extralong"))

    def test_consumes_iterable_once(self) -> None:
        # Generators are valid input; print_table materialises via list().
        def rows():
            yield {"a": "1"}
            yield {"a": "2"}

        buf = io.StringIO()
        output.print_table(rows(), columns=["a"], fp=buf)
        text = buf.getvalue()
        self.assertIn("1", text)
        self.assertIn("2", text)


class PrintLineTest(unittest.TestCase):
    def test_writes_with_trailing_newline(self) -> None:
        buf = io.StringIO()
        output.print_line("hello", fp=buf)
        self.assertEqual(buf.getvalue(), "hello\n")

    def test_quiet_suppresses(self) -> None:
        buf = io.StringIO()
        output.print_line("x", quiet=True, fp=buf)
        self.assertEqual(buf.getvalue(), "")


class EprintTest(unittest.TestCase):
    def test_writes_to_stderr(self) -> None:
        original = sys.stderr
        sys.stderr = io.StringIO()
        try:
            output.eprint("error!")
            self.assertEqual(sys.stderr.getvalue(), "error!\n")
        finally:
            sys.stderr = original


if __name__ == "__main__":
    unittest.main()
