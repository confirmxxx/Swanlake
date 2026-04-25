"""Preflight: assert no production-credential env vars are present.

White Cells personas dispatch into the fixture sandbox only. If the
process inherits a production token through env, a compromised persona
could leak it. Preflight is a fail-closed gate before any persona runs.

Match is substring against the env var **name**, case-insensitive. The
value is never echoed or logged.
"""
from __future__ import annotations

import os
import sys
from typing import Iterable

# Substrings; matched case-insensitively against env var names.
_CREDENTIAL_NAME_SUBSTRINGS = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "NOTION_TOKEN",
    "NOTION_API_KEY",
    "SUPABASE_ACCESS_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
    "VERCEL_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "STRIPE_SECRET_KEY",
    "STRIPE_PUBLISHABLE_KEY",
    "TG_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
)


def _matches(name: str, substrings: Iterable[str]) -> str | None:
    upper = name.upper()
    for s in substrings:
        if s in upper:
            return s
    return None


def detect_credentials(env: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Return list of (env_var_name, matched_substring) for any present
    credential env var with a non-empty value. Value is never returned."""
    env = os.environ if env is None else env
    hits: list[tuple[str, str]] = []
    for name, value in env.items():
        if not value:
            continue
        matched = _matches(name, _CREDENTIAL_NAME_SUBSTRINGS)
        if matched:
            hits.append((name, matched))
    return hits


def assert_clean_env(env: dict[str, str] | None = None) -> None:
    """Raise SystemExit(2) if any credential env var is set."""
    hits = detect_credentials(env)
    if not hits:
        return
    msg = [
        "BLOCKED: production-credential env vars present in supervisor process.",
        "White Cells must run in an env stripped of all production tokens.",
        "Detected (names only; values redacted):",
    ]
    for name, sub in hits:
        msg.append(f"  - {name}  (matched substring {sub})")
    msg.append("")
    msg.append("Unset these vars and re-run, or run as the dedicated whitecells")
    msg.append("Linux user once that user is provisioned (operator handoff).")
    print("\n".join(msg), file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    assert_clean_env()
    print("preflight: ok (no credential env vars detected)")
