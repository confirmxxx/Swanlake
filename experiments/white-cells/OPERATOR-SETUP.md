# White Cells — Operator Setup Guide

System-level wiring the supervisor cannot do for itself. Each step
below requires elevated privileges (sudo) or live-system access. Run
each step ONCE per host on which White Cells will run.

This document is the contract for what the operator agreed to do
when accepting Phase 2/3. The PR's subagent did not perform any of
these steps; that is the rule, not an oversight.

## 1. Provision the dedicated `whitecells` Linux user

A persona that escapes its sandbox should run as a user with no
read access to your live tree. Create the user with a restricted
shell and a home dir under `/var/lib/whitecells`:

```bash
sudo useradd \
  --system \
  --create-home \
  --home-dir /var/lib/whitecells \
  --shell /usr/sbin/nologin \
  whitecells

# Ensure no read access to the operator's secrets.
sudo chmod 700 "$HOME/.claude" "$HOME/.ssh" "$HOME/.gnupg"
sudo chmod 700 "$HOME/projects"
sudo setfacl -m u:whitecells:--- "$HOME/.claude"
sudo setfacl -m u:whitecells:--- "$HOME/.ssh"

# Allow whitecells to read ONLY the Swanlake repo + its experiment.
sudo setfacl -R -m u:whitecells:rx "$HOME/projects/Swanlake"
sudo setfacl -R -m u:whitecells:rwx "$HOME/projects/Swanlake/experiments/white-cells/state"
sudo setfacl -R -m u:whitecells:rwx "$HOME/projects/Swanlake/experiments/white-cells/findings"
sudo setfacl -R -m u:whitecells:rwx "$HOME/projects/Swanlake/experiments/white-cells/fixtures/sandbox_targets"
```

Verification:

```bash
sudo -u whitecells -s -- ls "$HOME/.claude"     # expect: Permission denied
sudo -u whitecells -s -- ls "$HOME/projects/Swanlake"  # expect: listing
```

## 2. Configure nftables egress allowlist

White Cells must not reach the live internet. Default-deny outbound,
allow ONLY GitHub Issues API for the Swanlake repo:

```bash
sudo install -d /etc/nftables.d
sudo tee /etc/nftables.d/whitecells-egress.nft > /dev/null <<'NFT'
table inet whitecells-egress {
    set gh_api_v4 {
        type ipv4_addr
        flags interval
        # GitHub API IPv4 ranges (refresh quarterly; source: api.github.com/meta).
        elements = { 140.82.112.0/20, 192.30.252.0/22 }
    }
    set gh_api_v6 {
        type ipv6_addr
        flags interval
        elements = { 2a0a:a440::/29, 2606:50c0::/32 }
    }

    chain output {
        type filter hook output priority 0; policy drop;

        # Loopback always permitted — the fixture sandbox binds 127.0.0.1.
        oifname "lo" accept

        # Only the whitecells UID is constrained; everyone else is unaffected.
        meta skuid != "whitecells" accept

        ip daddr @gh_api_v4 tcp dport 443 accept
        ip6 daddr @gh_api_v6 tcp dport 443 accept

        # Drop everything else for whitecells.
        log prefix "whitecells-egress-drop: " counter drop
    }
}
NFT

sudo nft -f /etc/nftables.d/whitecells-egress.nft
sudo systemctl reload nftables
```

Verification:

```bash
# Outbound HTTPS to non-GitHub host MUST fail for the whitecells user.
sudo -u whitecells -- curl -sS -m 5 https://example.invalid/ \
  && echo "FAIL: egress not blocked" \
  || echo "OK: blocked"

# GitHub API MUST succeed (HTTP 401 is fine — proves egress reaches GitHub).
sudo -u whitecells -- curl -sS -m 10 https://api.github.com/repos/confirmxxx/Swanlake \
  | head -3
```

## 3. (Optional) Install DeepTeam

Research-Poisoner upgrades from its Phase 1 stub to DeepTeam-backed
probes when DeepTeam is installed. If the operator skips this step,
the persona logs `deepteam not installed; using Phase 1 stub` once
and continues without breaking anything.

```bash
sudo -u whitecells -- python3 -m pip install --user \
  -r "$HOME/projects/Swanlake/experiments/white-cells/requirements.txt"

# Verified version (April 2026): deepteam 0.1.x
sudo -u whitecells -- python3 -c "import deepteam; print(deepteam.__version__)"
```

DeepTeam is Apache 2.0 (https://github.com/confident-ai/deepteam).
Pinned to `>=0.1,<0.2` in `requirements.txt` — major bumps may
rename `deepteam.vulnerabilities` and break the import probe.

## 4. Wire the Claude Routine

Saturday cadence so White Cells findings inform the Sunday
`security-watchdog` posture refresh:

```
/schedule create \
  name="white-cells-saturday-pass" \
  cron="0 9 * * 6" \
  prompt="As the whitecells user, run python3 ${HOME}/projects/Swanlake/experiments/white-cells/supervisor/orchestrator.py run --all. Then python3 -m white_cells.supervisor.file_findings --dry-run and report the top 5 HIGH-severity findings to me without filing yet."
```

Do NOT add `--commit` to the routine prompt. The mechanical
kill-criterion gate exists so findings only graduate to GitHub
issues after operator review.

## 5. Wire the systemd timer for closure-rate metric rollup

PR #6 (`tools/loop-closure-metric.py`) already ships the rollup;
just wire its systemd timer to run weekly. Reference unit lives at
`tools/loop-closure-metric.py --service-mode=timer-friendly`.

```bash
sudo tee /etc/systemd/system/swanlake-loop-closure.service > /dev/null <<'UNIT'
[Unit]
Description=Swanlake loop-closure metric rollup

[Service]
Type=oneshot
User=whitecells
ExecStart=/usr/bin/python3 %h/projects/Swanlake/tools/loop-closure-metric.py
UNIT

sudo tee /etc/systemd/system/swanlake-loop-closure.timer > /dev/null <<'UNIT'
[Unit]
Description=Run swanlake-loop-closure weekly (Sunday 10:00)

[Timer]
OnCalendar=Sun *-*-* 10:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now swanlake-loop-closure.timer
sudo systemctl list-timers swanlake-loop-closure*
```

## 6. Verify the whole stack

After steps 1–5, run the supervisor manually three times and confirm:

```bash
# As whitecells, with credentials stripped.
sudo -u whitecells -- env -i HOME=/var/lib/whitecells PATH=/usr/bin:/bin \
  python3 "$HOME/projects/Swanlake/experiments/white-cells/supervisor/orchestrator.py" \
  run --all

# Expect: every persona files >=1 finding; quarantined/isolation_violation = 0.
# Expect: triage_emitted matches filed.

# Sanity-check nothing leaked.
ls "$HOME/projects/Swanlake/experiments/white-cells/findings/"  # F0001*.json ...
ls "$HOME/projects/Swanlake/experiments/white-cells/state/"     # findings.jsonl ...
```

## 7. Track the kill criterion

```bash
# Weekly: closure ratio must be >= 30% over 28 days, or kill the experiment.
sudo -u whitecells -- python3 \
  "$HOME/projects/Swanlake/experiments/white-cells/supervisor/closure_rate.py" \
  kill-check
```

If exit 1 four weeks running, kill the experiment per `README.md`'s
mechanical kill criterion. No "give it more time."

---

## What the PR did NOT touch

- The operator's live `~/.claude/settings.json` (Hook-Fuzzer reads a
  *snapshot copy* under `experiments/white-cells/fixtures/`).
- The operator's live `~/.claude/.last-watchdog-run` (Beacon-Burner's
  staleness probe ages a fixture marker under
  `fixtures/sandbox_targets/beacon_burner/`).
- The operator's live `~/.claude/agents/` directory (Zone-Climber's
  probes target a tmpdir-backed fixture agents tree).
- The operator's live nftables config or systemd units.
- `deepteam` install (pip install is operator-side, optional).
- The Claude Routine schedule (the operator wires it via `/schedule`).

If any of those WERE touched, this is a bug. File an issue, do not
move forward with the operator handoff.
