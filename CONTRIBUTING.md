# Contributing to Swanlake

Thanks for considering a contribution. This document covers licensing, the Developer Certificate of Origin sign-off, the Contributor License Agreement policy, and commit conventions.

## Developer Certificate of Origin

All commits must include a `Signed-off-by:` line. This is the Developer Certificate of Origin (DCO) version 1.1, the same mechanism used by the Linux kernel, Docker, GitLab, and many other OSS projects. It is not a Contributor License Agreement (CLA); it is a lightweight attestation that you have the right to submit your contribution under Swanlake's license.

The DCO says, in short:

> By submitting this contribution, I certify that (a) it is my original work or I have the right to submit it under the project's license, (b) I am aware that the contribution will be publicly distributed, and (c) I submit it voluntarily under the project's Apache 2.0 license.

Full text: https://developercertificate.org/

To sign off, append this line to your commit message:

```
Signed-off-by: Your Name <your.email@example.com>
```

Most git clients support `git commit -s` which adds this line automatically based on your `user.name` and `user.email`. Sign-off with a pseudonym is acceptable as long as the email is one you control.

## Contributor License Agreement policy

Swanlake currently accepts contributions under DCO sign-off alone. There is no CLA for small, incremental contributions.

A lightweight CLA may be required for:
- Contributions that exceed a few hundred lines of substantive change
- Contributions from employees submitting on behalf of an organization
- Contributions that touch the spec layer (`*/SPEC.md`) rather than reference implementations

If a CLA becomes necessary, the maintainer will request it on the relevant pull request. The CLA will be the Fiduciary License Agreement or equivalent — you retain copyright to your contribution; you grant the maintainer the right to relicense the project if ever necessary (for example, to dual-license under Apache 2.0 + a commercial license for a hosted tier). This is the pattern used by MongoDB, GitLab, and Canonical among others.

Contributions that refuse both DCO and CLA cannot be merged. Not negotiable.

## Commit conventions

Use Conventional Commits format: `<type>(<scope>): <summary>`.

Types:
- `feat` — new functionality
- `fix` — bug fix
- `docs` — documentation only
- `refactor` — code change that neither fixes a bug nor adds a feature
- `test` — test additions or corrections
- `chore` — build, release, or repo-meta changes
- `sec` — security fix. Use for anything addressing a vulnerability, with a description that does not prematurely disclose exploitation detail

Scopes for this repo:
- `beacon` — Defense Beacon package
- `zones` — Trust Zones package
- `purity` — Reflex Purity package
- `docs` — docs/ root
- `root` — repo-level files (README, LICENSE, etc.)

Example:
```
feat(beacon): add per-surface rotation timestamp to state file

Signed-off-by: Your Name <your.email@example.com>
```

## No AI-generation fingerprints

Commits, PR descriptions, code comments, and documentation must not carry AI-generation markers. No `Co-Authored-By: Claude`, no `🤖 Generated with ...` footers, no `AI-generated` labels. This rule applies regardless of how the text was drafted; the expectation is that a human reviewed and takes responsibility for every line of a contribution.

## Security reporting

Do NOT open a public issue for a security vulnerability. Email the maintainer (handle available on their GitHub profile) or use a private-report channel if one is set up. Allow a reasonable disclosure window before publishing details.

For general security-adjacent design questions (threat model, primitive semantics), a public issue is fine.

## Scope discipline

Swanlake is intentionally small. See `NON-GOALS.md` for categories of contributions that will not be accepted. Good-faith proposals to extend into those categories will be politely redirected to an existing project. Contributions that fit an existing primitive's spec are the fast path.

## Testing

Reference implementations are stdlib-only wherever possible. New reference impls should:
- Include a test suite (shell or pytest) that exercises the happy path + at least one failure mode
- Avoid new dependencies unless the case is argued in the PR description
- Work on Linux and macOS at minimum; WSL2 is supported; native Windows is best-effort

## Release cadence

Semantic versioning. Pre-1.0, breaking changes may ship in minor releases with a migration note. After 1.0, breaking changes require a major bump.

## Questions

Open a discussion thread. Small questions welcome; no form required.
