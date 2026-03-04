# Changelog

All notable changes to this project are documented in this file.

Format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
with date-based entries because this repository does not currently publish
semantic version tags.

## [Unreleased]

### Current State

- Single-instance AWS EC2 personal cloud lab operated via `edc`, with Tailscale-only access and Portainer for container management.
- Changelog-first operating model is active: `[Unreleased]` tracks mutable status, while dated entries capture completed milestones.
- Operator baseline remains CLI-first, test-backed, and cost-aware, with safety guardrails around lifecycle, snapshot, and cleanup paths.

### Active Priorities

- Keep `CHANGELOG.md` current as the source of active status and completed milestones.
- Continue thin-CLI extraction while preserving operator UX and lifecycle safety guardrails.
- Keep snapshot/recovery guidance and restore-drill practice current in `RUNBOOK.md`.
- Preserve cold-start-ready documentation consistency across README, RUNBOOK, SECURITY, and ARCHITECTURE docs.

### In Progress

- None.

### Blocked

- None.

### Decisions Needed

- None.

### Recently Completed

- Wired Dropbox FUSE mount via rclone: rclone config stored as SecureString at `/edcloud/rclone_config` in SSM; cloud-init fetches it on every rebuild and enables `rclone-dropbox.service` (user systemd, `~/Dropbox` mount); `RCLONE_CONFIG_SSM_PARAMETER` added to `config.py`.
- Added oldspeak MCP bootstrap integration while keeping app code in a separate repo: cloud-init now best-effort syncs `~/src/oldspeak` (via `gh` auth path), bootstraps a local venv/install + spaCy model, and installs local wrappers (`~/.local/bin/oldspeak-mcp-stdio`, `~/.local/bin/oldspeak-mcp-http`) for on-host Cline/Claude Code usage. Docs updated in README, RUNBOOK, and ARCHITECTURE.

## [2026-03-03]

### Added

- Dropbox FUSE mount via rclone wired into cloud-init bootstrap: `rclone_config` SSM parameter fetched at build time, `rclone-dropbox.service` enabled automatically, `~/Dropbox` mounted on every instance.

## [2026-02-21]

### Added

- Backup and operations tooling matured with dedicated modules for backup policy management, resource auditing, and AWS client/discovery support.
- State-volume-focused snapshot operations gained retention support (`keep-last-N` prune workflow) and stronger operator-facing guidance.
- Centralized SSH trust helpers (`edcloud/ssh_trust.py`) and `edc ssh-trust sync/show-path` commands.
- Cloud-init SSH host-key persistence on the state volume (`/opt/edcloud/state/ssh-host-keys`) to reduce reprovision host-key churn.
- Idempotent 4 GiB swap baseline in cloud-init (`/swapfile`, `vm.swappiness=10`).

### Changed

- `edc ssh` and `edc verify` switched to strict host-key checking with an edcloud-specific known_hosts boundary.
- `destroy` lifecycle defaults were hardened to perform cleanup by default, with explicit skip flags for exceptional workflows.
- Snapshot strategy was reoriented toward durable state-volume backups, with docs updated across README, runbook/architecture materials, and operator workflow references.
- Documentation architecture was consolidated: changelog-memory workflow adopted and `SETUP.md` transitioned to `RUNBOOK.md`.
- Restore-drill and DLM lifecycle planning guidance were validated and synchronized into operations docs.

### Fixed

- Cloud-init reliability defects were corrected (heredoc handling, file write behavior, package/bootstrap execution context, and user-data size constraints).
- Volume lifecycle logic was tightened to prevent orphaned EBS volume outcomes during destructive workflows.

## [2026-02-18]

### Added

- `edc reprovision` lifecycle support, including resize orchestration and safer rebuild flow controls.
- Broader regression coverage for cleanup, snapshot lifecycle behavior, and CLI safety confirmation paths.

### Changed

- Public API and lifecycle interaction paths were refined for clearer orchestration between CLI, EC2 operations, and snapshot handling.
- Snapshot operations were hardened with improved wait/ordering behavior and validation around destructive transitions.

### Fixed

- Post-review hardening addressed confirmation guard edge cases and resize safety behavior before merge.

## [2026-02-16]

### Changed

- Configuration and module boundaries were centralized and standardized, reducing duplication and clarifying code ownership across CLI/AWS modules.
- Documentation and script references were aligned with the refactored operator workflow.

### Fixed

- Mypy/type-checking regressions were resolved across key lifecycle paths.
- AWS exception handling was hardened in reliability-critical code paths (`aws_check`, cleanup, and CLI-facing operations).

## [2026-02-17]

### Changed

- Default infrastructure sizing was optimized for lower recurring spend (instance and volume defaults), while retaining the single-instance lab operating model.

## [2026-02-15]

### Added

- Initial project baseline: core `edc` CLI modules for EC2 lifecycle, snapshot, and Tailscale-assisted access, plus first-pass tests.
- Security and publication-readiness scaffolding, including guardrail documentation and repository hygiene workflows.
- Contributor/agent workflow guidance and operator templates for reproducible local/remote operation.

### Changed

- Operator workflow docs were iterated rapidly to codify lifecycle safety, persistent state handling, and day-0 bootstrap expectations.

### Security

- Repository hardening pass prepared the project for broader visibility, including secret-scanning baseline and remediation tracking updates.
