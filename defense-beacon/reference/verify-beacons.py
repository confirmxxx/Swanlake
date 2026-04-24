#!/usr/bin/env python3
"""
Defense Beacon verifier.

For local surfaces (files on disk) — reads the file, checks that the current
canaries are present. For remote surfaces — prints a manual checklist.

Exits 0 only if every local surface matches the registry.

Config: verify.yaml (plain-text, key = value per line, # comments allowed).
Format:
    local.<surface-id> = /path/to/file
    remote.<surface-id> = "description of where to check manually"

If verify.yaml is missing, a verify.example.yaml template is referenced.

Usage:
    python3 verify-beacons.py
    python3 verify-beacons.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
REPO_DIR = Path(__file__).resolve().parent
STATE_FILE = REPO_DIR / ".canary-state.json"
CONFIG_FILE = REPO_DIR / "verify.yaml"
EXAMPLE_CONFIG = REPO_DIR / "verify.example.yaml"


def _expand(s: str) -> str:
    return os.path.expanduser(os.path.expandvars(s))


def load_config() -> tuple[dict[str, Path], dict[str, str]]:
    """Parse verify.yaml. Very small subset: `key = value` lines, # comments,
    blank lines ignored. Keys: local.<id>, remote.<id>."""
    src = CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_CONFIG
    if not src.exists():
        return {}, {}
    local: dict[str, Path] = {}
    remote: dict[str, str] = {}
    for raw in src.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        val = v.strip().strip('"').strip("'")
        if key.startswith("local."):
            local[key[len("local."):]] = Path(_expand(val))
        elif key.startswith("remote."):
            remote[key[len("remote."):]] = val
    return local, remote


def load_state() -> dict:
    if not STATE_FILE.exists():
        print(f"error: no state file at {STATE_FILE} — run make-canaries.py first",
              file=sys.stderr)
        sys.exit(2)
    return json.loads(STATE_FILE.read_text())


def check_local(state: dict, local_surfaces: dict[str, Path]) -> list[dict]:
    results = []
    for sid, path in local_surfaces.items():
        entry = state["surfaces"].get(sid)
        if not entry:
            results.append({"surface": sid, "status": "NOT_IN_STATE", "path": str(path)})
            continue
        if not path.exists():
            results.append({"surface": sid, "status": "FILE_MISSING", "path": str(path)})
            continue
        try:
            content = path.read_text(errors="replace")
        except Exception as e:
            results.append({"surface": sid, "status": f"READ_ERROR: {e}",
                            "path": str(path)})
            continue
        shaped_ok = entry["shaped"] in content
        phrase_ok = entry["phrase"] in content
        if shaped_ok and phrase_ok:
            status = "OK"
        elif shaped_ok or phrase_ok:
            status = "PARTIAL"
        else:
            status = "MISSING"
        results.append({
            "surface": sid, "status": status, "path": str(path),
            "shaped_present": shaped_ok, "phrase_present": phrase_ok,
        })
    return results


def print_human(local_results: list[dict], state: dict,
                remote_surfaces: dict[str, str]) -> bool:
    print("=" * 72)
    print("LOCAL SURFACES")
    print("=" * 72)
    all_ok = True
    for r in local_results:
        badge = {
            "OK": "  OK ", "PARTIAL": " WARN", "MISSING": " FAIL",
            "FILE_MISSING": " FAIL", "NOT_IN_STATE": " FAIL",
        }.get(r["status"], " FAIL")
        if r["status"] != "OK":
            all_ok = False
        print(f"[{badge}] {r['surface']:30}  {r['status']:14}  {r['path']}")
        if r["status"] == "PARTIAL":
            print(f"         shaped={r.get('shaped_present')}  "
                  f"phrase={r.get('phrase_present')}")

    print()
    print("=" * 72)
    print("REMOTE SURFACES — MANUAL CHECKLIST")
    print("=" * 72)
    for sid, how in remote_surfaces.items():
        entry = state["surfaces"].get(sid)
        if not entry:
            print(f"[ SKIP] {sid:32}  (not generated yet — run make-canaries.py)")
            continue
        print(f"[ todo] {sid}")
        print(f"         where:  {how}")
        print(f"         expect: {entry['shaped']}")
        print(f"                 {entry['phrase']}")

    print()
    print("local status:", "CLEAN" if all_ok else "PROBLEMS FOUND")
    return all_ok


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    state = load_state()
    local_surfaces, remote_surfaces = load_config()
    if not local_surfaces and not remote_surfaces:
        print("warning: no verify.yaml or verify.example.yaml found;",
              "copy verify.example.yaml to verify.yaml and configure", file=sys.stderr)
    local = check_local(state, local_surfaces)
    if args.json:
        print(json.dumps(
            {"local": local, "remote_checklist": list(remote_surfaces.keys())},
            indent=2))
        return 0 if all(r["status"] == "OK" for r in local) else 1
    ok = print_human(local, state, remote_surfaces)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
