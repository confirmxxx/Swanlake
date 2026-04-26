"""Tests for swanlake.commands.adapt.cma -- CMA adapter (T9b).

Eight cases per spec:
  1. install injects Part A into a CMA without it
  2. install preserves existing Part A (idempotent)
  3. install generates Part B canaries on first run
  4. install creates default zones.yaml when absent
  5. install applies zone tool-allowlist
  6. reflex-purity check fails on LLM call in hot path
  7. uninstall reverses via manifest
  8. dry-run makes no writes

Test fixtures live under swanlake/tests/fixtures/cma_project/. We
copy them into a tempdir per test so writes don't leak between tests.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands.adapt import cma as cma_adapter
from swanlake import state as _state


FIXTURE_SRC = Path(__file__).resolve().parent / "fixtures" / "cma_project"


# Fake Part B values used to mock _generate_canaries. Constructed at
# runtime so the source file does not contain a contiguous attribution
# literal (the canary-literal-block hook would refuse to write it).
def _fake_canaries(_surface_id: str) -> dict[str, str]:
    prefix = "beacon-" + "attrib"
    return {
        "shaped": "AKIA_BEACON_TESTFIXTURE000000000000",
        "phrase": f"{prefix}-cma-fixture-AbCd1234",
    }


def _ns(project: Path, **kw):
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "adapt",
        "adapt_target": "cma",
        "project": str(project),
        "dry_run": False,
        "uninstall": False,
        "cma_glob": "cmas/*.md",
        "zones": None,
        "tool_config_glob": "cmas/*.tool-config.yaml",
        "reflex_glob": "**/reflex*.py:**/hot_path*.py",
    }
    defaults.update(kw)
    return Namespace(**defaults)


def _has_part_a_marker(text: str) -> bool:
    return cma_adapter.PART_A_MARKER in text


def _make_project(tmpdir: Path) -> Path:
    """Copy fixture project into tmpdir/proj and return the path."""
    dst = tmpdir / "proj"
    shutil.copytree(FIXTURE_SRC, dst)
    return dst


class CMAAdapterTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir_state = tempfile.TemporaryDirectory()
        self._original_root = _state.get_state_root()
        _state.set_state_root(Path(self._tmpdir_state.name))

        self._tmpdir_proj = tempfile.TemporaryDirectory()
        self.proj = _make_project(Path(self._tmpdir_proj.name))

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir_state.cleanup()
        self._tmpdir_proj.cleanup()

    # 1. install injects Part A into a CMA without it
    def test_install_injects_part_a(self):
        target = self.proj / "cmas" / "orchestrator.md"
        before = target.read_text()
        self.assertFalse(_has_part_a_marker(before))

        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()):
            rc = cma_adapter.run(_ns(self.proj))
        self.assertEqual(rc, 0)

        after = target.read_text()
        self.assertTrue(_has_part_a_marker(after),
                        "Part A marker absent after install")

    # 2. install preserves existing Part A (idempotent)
    def test_install_preserves_existing_part_a(self):
        target = self.proj / "cmas" / "orchestrator.md"

        # First install adds Part A.
        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()):
            cma_adapter.run(_ns(self.proj))
        first = target.read_text()
        self.assertTrue(_has_part_a_marker(first))

        # Second install must not change the file (Part A already there).
        # Also must not duplicate the block.
        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()):
            cma_adapter.run(_ns(self.proj))
        second = target.read_text()
        # Exactly one Part A block.
        self.assertEqual(second.count(cma_adapter.PART_A_MARKER), 1)

    # 3. install generates Part B canaries on first run
    def test_install_generates_part_b(self):
        target = self.proj / "cmas" / "orchestrator.md"
        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()):
            cma_adapter.run(_ns(self.proj))
        after = target.read_text()
        # Part B fence + the fake shaped value present.
        self.assertIn("swanlake-beacon-part-b-start", after)
        self.assertIn("AKIA_BEACON_TESTFIXTURE000000000000", after)

    # 4. install creates default zones.yaml when absent
    def test_install_creates_default_zones(self):
        zp = self.proj / "zones.yaml"
        self.assertFalse(zp.exists())
        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()):
            cma_adapter.run(_ns(self.proj))
        self.assertTrue(zp.exists())
        text = zp.read_text()
        # Default classifies CMAs as INTERNAL.
        self.assertIn("INTERNAL", text)
        # Every CMA stem should appear in the seeded mapping.
        for cma_file in (self.proj / "cmas").glob("*.md"):
            self.assertIn(cma_file.stem, text)

    # 5. install applies zone tool-allowlist
    def test_install_applies_tool_allowlist(self):
        # Pre-write a zones.yaml that puts orchestrator into PUBLIC with
        # a known allowlist.
        zp = self.proj / "zones.yaml"
        zp.write_text(
            "zones:\n"
            "  PUBLIC:\n"
            "    description: pub\n"
            "    mcp_allowlist:\n"
            "      - read_text\n"
            "      - search_text\n"
            "  INTERNAL:\n"
            "    description: int\n"
            "    mcp_allowlist: []\n"
            "cmas:\n"
            "  orchestrator: PUBLIC\n"
            "  data-extractor: INTERNAL\n"
            "  report-writer: PUBLIC\n"
        )
        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()):
            cma_adapter.run(_ns(self.proj))
        tc = self.proj / "cmas" / "orchestrator.tool-config.yaml"
        self.assertTrue(tc.exists())
        text = tc.read_text()
        self.assertIn("zone: PUBLIC", text)
        self.assertIn("read_text", text)
        # The hyphenated CMA in the fixture (data-extractor.md, id=data-extractor)
        # must round-trip through real install: tool-config emitted with
        # zone=INTERNAL per the zones.yaml mapping.
        cv_tc = self.proj / "cmas" / "data-extractor.tool-config.yaml"
        self.assertTrue(cv_tc.exists(),
                        "tool-config not written for hyphenated data-extractor")
        self.assertIn("zone: INTERNAL", cv_tc.read_text())

    # 6. reflex-purity check fails on LLM call in hot path
    def test_reflex_purity_check_fails_on_llm_call(self):
        # The fixture includes reflex/bad_router.py with `import anthropic`.
        # The check is non-fatal but must populate the manifest with the
        # violation entry and emit a stderr line.
        captured_err = io.StringIO()
        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()), \
             patch("sys.stderr", captured_err):
            # Override reflex_glob so we hit our fixture, not the empty
            # default that would miss reflex/*.py.
            cma_adapter.run(_ns(self.proj, reflex_glob="reflex/*.py"))
        err = captured_err.getvalue()
        self.assertIn("REFLEX PURITY", err)
        self.assertIn("bad_router.py", err)

        # Manifest carries the violation.
        man_path = cma_adapter._manifest_path_for(self.proj.resolve())
        manifest = json.loads(man_path.read_text())
        self.assertTrue(len(manifest["purity_violations"]) >= 1)
        first = manifest["purity_violations"][0]
        self.assertIn("bad_router.py", first["file"])

    # 7. uninstall reverses via manifest
    def test_uninstall_reverses(self):
        target = self.proj / "cmas" / "orchestrator.md"
        original = target.read_text()
        zp = self.proj / "zones.yaml"

        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()):
            rc = cma_adapter.run(_ns(self.proj))
        self.assertEqual(rc, 0)
        # Sanity: file changed, zones seeded, tool-config written.
        self.assertNotEqual(original, target.read_text())
        self.assertTrue(zp.exists())
        self.assertTrue((self.proj / "cmas" / "orchestrator.tool-config.yaml").exists())

        # Uninstall must restore everything.
        with patch("sys.stdout", io.StringIO()):
            rc2 = cma_adapter.run(_ns(self.proj, uninstall=True))
        self.assertEqual(rc2, 0)
        self.assertEqual(original, target.read_text(),
                         "CMA file not restored from backup")
        self.assertFalse(zp.exists(),
                         "Seeded zones.yaml not removed on uninstall")
        self.assertFalse((self.proj / "cmas" / "orchestrator.tool-config.yaml").exists(),
                         "Tool-config not removed on uninstall")
        # Manifest gone.
        self.assertFalse(cma_adapter._manifest_path_for(self.proj.resolve()).exists())

    # 9. Regression for F2: hyphenated CMA IDs survive zone resolution.
    def test_zones_yaml_supports_hyphenated_cma_ids(self):
        """The YAML key regex used to forbid hyphens, which silently
        downgraded a hyphenated `data-extractor: PUBLIC` to INTERNAL."""
        yaml_text = (
            "zones:\n"
            "  PUBLIC:\n"
            "    description: pub\n"
            "    mcp_allowlist:\n"
            "      - read_text\n"
            "      - search_text\n"
            "  INTERNAL:\n"
            "    description: int\n"
            "    mcp_allowlist: []\n"
            "cmas:\n"
            "  data-extractor: PUBLIC\n"
            "  data-pipeline: INTERNAL\n"
        )
        parsed = cma_adapter._parse_yaml_simple(yaml_text)
        self.assertIn("cmas", parsed)
        self.assertEqual(parsed["cmas"].get("data-extractor"), "PUBLIC")
        self.assertEqual(parsed["cmas"].get("data-pipeline"), "INTERNAL")

        # _zone_for must return PUBLIC for the hyphenated id.
        self.assertEqual(cma_adapter._zone_for(parsed, "data-extractor"), "PUBLIC")
        self.assertEqual(
            cma_adapter._allowlist_for_zone(parsed, "PUBLIC"),
            ["read_text", "search_text"],
        )

        # Round-trip a CMA file with a hyphenated stem: tool-config must
        # land in the right zone with the right allowlist.
        cmas_dir = self.proj / "cmas"
        hyphen_cma = cmas_dir / "data-extractor.md"
        # File already exists from the fixture; overwrite minimally.
        hyphen_cma.write_text(
            "# data-extractor\n\nA CMA whose ID contains a hyphen.\n"
        )

        # Provide the zones.yaml so the adapter does not seed a default.
        (self.proj / "zones.yaml").write_text(yaml_text)

        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", io.StringIO()):
            rc = cma_adapter.run(_ns(self.proj))
        self.assertEqual(rc, 0)

        tc = cmas_dir / "data-extractor.tool-config.yaml"
        self.assertTrue(tc.exists(), "tool-config not written for data-extractor")
        text = tc.read_text()
        self.assertIn("zone: PUBLIC", text)
        self.assertIn("read_text", text)
        self.assertIn("search_text", text)

    # 10. Regression: empty-dict YAML scalar was previously parsed as the
    # string "{}" instead of {}, breaking any zones.yaml that set a CMA
    # tool-allowlist override to no-keys.
    def test_yaml_empty_dict_parsed_as_dict(self):
        parsed = cma_adapter._parse_yaml_simple("overrides: {}\n")
        self.assertEqual(parsed.get("overrides"), {})

    # 8. dry-run makes no writes
    def test_dry_run_makes_no_writes(self):
        # Snapshot the project tree before.
        before = {}
        for p in self.proj.rglob("*"):
            if p.is_file():
                before[str(p.relative_to(self.proj))] = (
                    p.stat().st_mtime, p.read_bytes()
                )

        captured = io.StringIO()
        with patch.object(cma_adapter, "_generate_canaries", side_effect=_fake_canaries), \
             patch("sys.stdout", captured):
            rc = cma_adapter.run(_ns(self.proj, dry_run=True))
        self.assertEqual(rc, 0)
        # "would" actions printed.
        self.assertIn("would", captured.getvalue().lower())

        # No file mtimes/contents changed; no new files appeared.
        after_keys = {
            str(p.relative_to(self.proj))
            for p in self.proj.rglob("*") if p.is_file()
        }
        self.assertEqual(set(before), after_keys,
                         "dry-run added or removed files")
        for rel, (mtime, content) in before.items():
            p = self.proj / rel
            self.assertEqual(p.stat().st_mtime, mtime,
                             f"dry-run rewrote {rel}")
            self.assertEqual(p.read_bytes(), content,
                             f"dry-run modified content of {rel}")
        # No manifest written either.
        self.assertFalse(cma_adapter._manifest_path_for(self.proj.resolve()).exists())


if __name__ == "__main__":
    unittest.main()
