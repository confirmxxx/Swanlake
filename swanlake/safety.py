"""Confirmation prompts for destructive subcommands.

Spec section A7: sync / rotate / adapt cc --uninstall require confirmation
unless --yes is passed or SWANLAKE_NONINTERACTIVE=1 is set in the env.

Non-interactive bypass logs `[auto-confirmed] <prompt>` to stdout so the
operator inspecting timer logs sees exactly which prompt was skipped.
"""
from __future__ import annotations

import os
import sys


NONINTERACTIVE_ENV = "SWANLAKE_NONINTERACTIVE"


def is_noninteractive() -> bool:
    """True iff the env var bypass is active."""
    return os.environ.get(NONINTERACTIVE_ENV) == "1"


def confirm(prompt: str, yes: bool = False) -> bool:
    """Prompt for confirmation. Return True if confirmed.

    Precedence:
      1. `yes=True` (the --yes flag) -> auto-confirm, log to stdout.
      2. SWANLAKE_NONINTERACTIVE=1 -> auto-confirm, log to stdout.
      3. TTY -> input() prompt; True iff response in {"y", "yes"} (case-
         insensitive, whitespace stripped).
      4. No TTY and no bypass -> return False without prompting (the
         caller is expected to surface a USAGE error to the operator).
    """
    if yes or is_noninteractive():
        # Mirror the prompt to stdout so timer / cron logs make the bypass
        # auditable. The audit row separately records noninteractive=true.
        print(f"[auto-confirmed] {prompt}")
        return True

    try:
        is_tty = sys.stdin.isatty()
    except Exception:
        is_tty = False

    if not is_tty:
        # Non-TTY without explicit bypass. Refuse silently; the caller
        # decides how to report the refusal (typically exit 2 USAGE).
        return False

    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        # Ctrl-D / Ctrl-C at the prompt is an explicit "no".
        print()  # newline so the next shell prompt isn't glued to the input
        return False

    return answer in ("y", "yes")
