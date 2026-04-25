"""Phase 2/3 sandbox-target fixtures.

Each subpackage holds *planted-vulnerability* fixtures that the new
personas (Beacon-Burner, Zone-Climber, Reflex-Smuggler, Hook-Fuzzer)
attack. Personas NEVER touch the operator's live tree — they read
these fixtures, write to a tmpdir copy, and never escape that copy.

The supervisor's persona-isolation guard enforces this at runtime.
"""
