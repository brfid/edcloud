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

- Maintainability pass (simplification / DRY / SoC / robustness):
  - Removed dead code: the unused `skip_snapshot` parameter on `cleanup.run_cleanup_workflow`, a dead `elif ...: pass` branch in `sync-cline-auth`, and `cline_sync.validate_source`'s discarded parsed-payload return value. Fixed the author-name typo in `pyproject.toml`.
  - DRY: single-sourced the `run_checked` subprocess helper (new `proc.py`) used by `cli.py` and `cline_sync.py`; extracted `ec2._raise_if_orphans` to collapse the "no instance, orphans exist" `TagDriftError` block duplicated across `start`/`stop`/`destroy`; defaulted prune keep-count and snapshot GB-month rate from their `config` constants instead of re-hardcoding `3` / `0.05`.
  - Wired the previously-unused `SnapshotInfo`, `PruneResult`, `RestoreDrillResult`, `BackupPolicyResult`, and `SnapshotCostReport` TypedDicts into their producing functions so the typed API contracts are checked end-to-end (`mypy --strict`).
  - SoC: moved the default-VPC lookup to `discovery.default_vpc_id`, removing a private in-function `from edcloud.ec2 import _get_default_vpc_id` reach-in from `security_group.py`.
  - Robustness: cloud-init render slots now use a distinct `@@{KEY}` delimiter (`string.Template` subclass with strict `substitute` + guard) so shell `$VAR` / `${VAR}` can never silently collide with a render key. Added `tests/test_user_data.py`.
- Aligned rebuild flow with declarative dotfiles repo: removed stale
  `install.sh` invocation from cloud-init, updated `README.md` and `RUNBOOK.md`
  to document dotfiles application as a post-rebuild operator step
  (manual or LLM-driven). No CLI, IAM, or SSM surface changes.
- Architecture refactor for self-documentation and module clarity:
  - Extracted `user_data.py` from `ec2.py` — cloud-init template rendering with `string.Template` safe substitution replacing fragile `str.replace` chains.
  - Extracted `security_group.py` from `ec2.py` — SG discovery/lifecycle and `TagDriftError` as the unified tag-drift exception across all modules.
  - Extracted `cline_sync.py` from `cli.py` — Cline OAuth sync workflow (validation, diagnostics, file transfer) as a UI-agnostic library module.
  - Added `types.py` with `TypedDict` definitions (`InstanceStatus`, `ProvisionResult`, `ResizeResult`, `VolumeInfo`, `CostEstimate`, `SnapshotInfo`, etc.) for typed API contracts.
  - Added `py.typed` PEP 561 marker for downstream type-checking support.
  - Unified `TagDriftError` usage: `snapshot.py` now raises `TagDriftError` (not `RuntimeError`) for duplicate state volume conditions, consistent with `ec2.py`.
  - Updated `lifecycle.py` to accept `Mapping[str, Any]` for TypedDict compatibility.
  - Bumped version to `1.0.0`; updated license field to PEP 639 format.
  - Added Python 3.14 to CI matrix; removed commented-out mypy pre-commit hook.
  - Added `*.code-workspace` to `.gitignore`.
  - Added `__all__` to `__init__.py`.
  - Replaced manual `scripts/setup-dotfiles.sh` with terse `AGENTS.md` instructions for dotfiles relink on a running host.
  - Updated `docs/ARCHITECTURE.md` with new module structure, dotfiles bootstrap flow, and design principles.
- Code quality pass for demo readiness:
  - Fixed all mypy strict errors (8 across 4 files) and ruff lint violations; tooling now passes clean.
  - Added CI workflow (`.github/workflows/ci.yml`) running pytest, ruff, and mypy on push/PR.
  - Removed dead code: unused `auto_snapshot_before_destroy`, vestigial `destroy(force=)` parameter, uncalled `_ec2_resource` wrapper, test-only `tailscale.ssh_command`.
  - Collapsed redundant wrapper functions in `ec2.py`, `resource_audit.py`, and `cli.py` that added indirection without logic.
  - Made `cleanup.py` UI-agnostic by accepting I/O callbacks instead of importing `click` directly, consistent with `lifecycle.py`.
  - Fixed N+1 `describe_volumes` API calls in `ec2.status()` (now a single batched call).
  - Single-sourced package version via `importlib.metadata` instead of duplicating in `__init__.py` and `pyproject.toml`.
- Hardened dotfiles bootstrap path in cloud-init:
  - Added `DOTFILES_REPO` / `DOTFILES_BRANCH` template variables rendered from `InstanceConfig`.
  - Added CLI options/env support on `provision` and `reprovision` (`--dotfiles-repo`, `--dotfiles-branch`, `EDCLOUD_DOTFILES_REPO`, `EDCLOUD_DOTFILES_BRANCH`).
  - Implemented repo/branch input validation in `edcloud.ec2` to reduce template-injection risk.
  - Updated cloud-init logic to resolve dotfiles source with fallback order (`gh` user URL, then persisted local origin for `auto`) and continue bootstrap on non-fatal sync failures.
  - Updated tests (`tests/test_ec2.py`, `tests/test_cli.py`) and docs (`README.md`, `RUNBOOK.md`, `docs/ARCHITECTURE.md`) to reflect new behavior.
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
