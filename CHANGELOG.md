# Changelog

All notable changes to this project are documented in this file.

Format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
with date-based entries because this repository does not currently publish
semantic version tags.

## [Unreleased]

### Current State
- Single-instance AWS EC2 personal cloud lab operated via `edc`, with Tailscale-only access and Portainer for container management.
- Mutable operator memory now lives here (`[Unreleased]`) and is no longer maintained in the runbook.
- Runbook content is maintained in `RUNBOOK.md`; architecture and security rationale remain in `DESIGN.md` and `SECURITY.md`.

### Active Priorities
- Finalize changelog-first workflow adoption and keep `CHANGELOG.md` current as the source of active status.
- Keep snapshot/recovery guidance and restore-drill practice current in `RUNBOOK.md`.
- Continue CLI/module refactor work while preserving operator UX and safety guardrails.

### In Progress
- None.

### Blocked
- None.

### Decisions Needed
- None.

### Recently Completed
- Added `CHANGELOG.md` with Keep a Changelog structure and custom `[Unreleased]` memory sections.
- Renamed `SETUP.md` to `RUNBOOK.md` and updated documentation references.
- Updated `AGENTS.md` workflow guidance to point mutable status tracking at `CHANGELOG.md`.

## [2026-02-21]

### Changed
- Validated and documented restore-drill workflow updates and DLM lifecycle planning.
- Reworked snapshot guidance toward state-volume-focused backup operations and retention controls.
- Hardened cloud-init host baseline details and synchronized related documentation.

### Fixed
- Corrected cloud-init baseline issues (including heredoc reliability and bootstrap behavior).

## [2026-02-18]

### Added
- `edc reprovision`/resize and related lifecycle safety improvements with expanded test coverage.

### Changed
- Advanced refactor planning and reliability hardening across CLI/AWS interaction paths.

## [2026-02-16]

### Changed
- Centralized configuration patterns and cleaned up module structure/documentation consistency.

### Fixed
- Resolved mypy and AWS exception-handling issues in reliability-sensitive paths.

## [2026-02-15]

### Added
- Initial mac support baseline notes and early workflow scaffolding.
