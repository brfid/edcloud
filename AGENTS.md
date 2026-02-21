# Agent notes (repo workflow)

## Mission

Build and operate a single-instance AWS EC2 personal cloud lab (`edcloud`) accessed via Tailscale, with Portainer for container management.

Priorities:

- Keep infrastructure simple and cost-aware.
- Keep access model Tailscale-only.
- Keep operations feasible from low-power ARM operator nodes.

## Start-here order

1. `README.md`
2. `CHANGELOG.md` (`[Unreleased]` first)
3. `SECURITY.md`
4. `DESIGN.md`
5. `RUNBOOK.md`
6. `edcloud/` package code

## Source of truth

- Current mutable status / active queue: `CHANGELOG.md` under `[Unreleased]`
- AWS resources: tag-based discovery via `edcloud:managed=true`
- Runtime secrets: AWS SSM Parameter Store
- Config defaults: `edcloud/config.py`
- Bootstrap baseline: `cloud-init/user-data.yaml`
- Tests: `tests/`
- Operator runbook and backup policy: `RUNBOOK.md`

## Changelog memory model

Use `CHANGELOG.md` as agent working memory.

- `## [Unreleased]` must keep these subcategories in order:
  1. `Current State`
  2. `Active Priorities`
  3. `In Progress`
  4. `Blocked`
  5. `Decisions Needed`
  6. `Recently Completed`
- Keep every subcategory present; if empty, use `- None.`.
- Dated entries (`## [YYYY-MM-DD]`) should use standard Keep a Changelog categories.

## Task source

Primary TODO list: `CHANGELOG.md` under `[Unreleased]` → `Active Priorities`.

Secondary procedural backlog: `RUNBOOK.md` checklists.

Optional TODO scan:

```bash
grep -RInE "TODO|FIXME|TBD|\[ \]" README.md CHANGELOG.md RUNBOOK.md DESIGN.md AGENTS.md edcloud tests cloud-init compose
```

## Constraints

### Python environment

- Use repo-local `.venv/` for Python commands.
- Do not install global/system packages.
- Manage dependencies via `pyproject.toml`.

### Documentation policy

- Do not create new markdown files unless explicitly requested.
- Update existing docs instead (`README.md`, `CHANGELOG.md`, `SECURITY.md`, `DESIGN.md`, `RUNBOOK.md`, `AGENTS.md`).
- Use CommonMark-compatible formatting.

### Operational guardrails

- Do not remove `edcloud:managed=true` tags.
- Do not hardcode credentials, tokens, or resource IDs.
- Preserve Tailscale-only access model (no inbound security group rules).
- Keep baseline host config reproducible in `cloud-init/user-data.yaml`.
- Keep snapshot and restore-drill guidance current.
- Backward compatibility is not a default goal; prefer clean, architecturally sound
  breaking changes unless backward compatibility is explicitly requested.

### Validation commands

Run when requested:

```bash
pytest -q
ruff check .
mypy edcloud tests
pre-commit run --all-files
```

## Output expectations for implementation tasks

- Summarize changes by file path.
- State validation performed (or explicitly state none).
- Do not add summary docs unless requested.
