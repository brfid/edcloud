# Changelog

All notable changes to this project are documented in this file.

Format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with date-based entries because this repository does not currently publish semantic version tags.

## [2026-08-25]

### Changed

- Marked the project as an unmaintained historical artifact.
- Reworked the README, runbook, architecture, security policy, agent guidance, and docstrings for accuracy and concise Google developer documentation style.
- Corrected first-run provisioning, storage defaults, snapshot retention, restore-drill scope, SSH handling, dotfiles bootstrap, and cost-estimate documentation.
- Removed the nonfunctional `provision --skip-snapshot` and `load-tailscale-env-key --no-shell-export` options while preserving the legacy Tailscale reconciliation flags.
- Disabled recurring Dependabot updates and scheduled CodeQL scans while retaining checks on repository activity.

### Fixed

- Preserved pre-reprovision snapshot IDs when replacement provisioning fails.
- Removed double-counting from the managed-resource cost audit.
- Preserved SSH host-key verification across connections and propagated security-group discovery, IAM policy, and EBS cleanup failures.

## [2026-08-13]

### Added

- Added gitleaks, detect-secrets, pre-commit, CI secret scanning, and local secret and PII exclusions.

### Changed

- Removed dead code, centralized subprocess and AWS helpers, connected typed return contracts, and made cloud-init template substitution strict and delimiter-safe.

## [2026-05-25]

### Fixed

- Avoided duplicate `Name` tags on DLM-created snapshots.

## [2026-05-23]

### Changed

- Removed the obsolete dotfiles installer invocation, dead code, and pass-through wrappers.

## [2026-04-12]

### Changed

- Replaced the repository's dotfiles helper with direct relink instructions for agents.

## [2026-04-05]

### Added

- Extracted user-data, security-group, and Cline-sync modules; added typed API contracts and a PEP 561 marker.
- Added Python 3.14 to CI and completed the strict type-checking and lint pass.

### Fixed

- Added missing AWS client mocks for region-less test environments.

## [2026-04-01]

### Changed

- Updated the dotfiles helper for the repository's declarative layout.

## [2026-03-28]

### Added

- Added a stow-based dotfiles linking helper.

## [2026-03-25]

### Added

- Added configurable dotfiles repository and branch selection with authenticated-user and persisted-origin fallbacks.

## [2026-03-04]

### Added

- Added best-effort `oldspeak` repository bootstrap and local MCP wrappers.

## [2026-03-03]

### Added

- Dropbox FUSE mount via rclone wired into cloud-init bootstrap: `rclone_config` SSM parameter fetched at build time, `rclone-dropbox.service` enabled automatically, `~/Dropbox` mounted on every instance.

## [2026-02-21]

### Added

- Backup and operations tooling matured with dedicated modules for backup policy management, resource auditing, and AWS client/discovery support.
- State-volume-focused snapshot operations gained retention support (`keep-last-N` prune workflow) and stronger operator-facing guidance.
- Cloud-init SSH host-key persistence on the state volume (`/opt/edcloud/state/ssh-host-keys`) to reduce reprovision host-key churn.
- Idempotent 4 GiB swap baseline in cloud-init (`/swapfile`, `vm.swappiness=10`).

### Changed

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

## [2026-02-17]

### Changed

- Default infrastructure sizing was optimized for lower recurring spend (instance and volume defaults), while retaining the single-instance lab operating model.

## [2026-02-16]

### Changed

- Configuration and module boundaries were centralized and standardized, reducing duplication and clarifying code ownership across CLI/AWS modules.
- Documentation and script references were aligned with the refactored operator workflow.

### Fixed

- Mypy/type-checking regressions were resolved across key lifecycle paths.
- AWS exception handling was hardened in reliability-critical code paths (`aws_check`, cleanup, and CLI-facing operations).

## [2026-02-15]

### Added

- Initial project baseline: core `edc` CLI modules for EC2 lifecycle, snapshot, and Tailscale-assisted access, plus first-pass tests.
- Security and publication-readiness scaffolding, including guardrail documentation and repository hygiene workflows.
- Contributor/agent workflow guidance and operator templates for reproducible local/remote operation.

### Changed

- Operator workflow docs were iterated rapidly to codify lifecycle safety, persistent state handling, and day-0 bootstrap expectations.

### Security

- Repository hardening pass prepared the project for broader visibility, including secret-scanning baseline and remediation tracking updates.
