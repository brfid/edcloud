# Agent notes

## Mission

Preserve edcloud as an accurate historical artifact. Prefer small correctness, security, and documentation fixes. Do not restart feature development unless the user explicitly requests it.

## Read first

1. `README.md`
2. `docs/ARCHITECTURE.md`
3. `SECURITY.md`
4. `RUNBOOK.md`
5. `CHANGELOG.md`
6. `edcloud/` and `tests/`

## Sources of truth

- AWS resource ownership: the `edcloud:managed=true` tag and tag-based discovery.
- Runtime defaults and tags: `edcloud/config.py`.
- Bootstrap behavior: `cloud-init/user-data.yaml`.
- CLI behavior and help: `edcloud/cli.py`.
- Operator procedures: `RUNBOOK.md`.
- Historical changes: `CHANGELOG.md` and Git history.

When documentation and code disagree, verify the behavior in code and tests before changing the documentation.

## Safety guardrails

- Preserve the Tailscale-only access model. Newly created security groups must have no inbound rules.
- Do not remove or weaken the `edcloud:managed=true` or `edcloud:volume-role` tags.
- Do not hardcode credentials, tokens, personal data, or live AWS resource identifiers.
- Keep bootstrap secrets in SSM Parameter Store and local credentials in restricted, untracked files.
- Treat state volumes and snapshots as data-bearing resources. Do not delete them without explicit authorization and exact target verification.
- Keep the host baseline reproducible in `cloud-init/user-data.yaml`.

On a running host, dotfiles are cloned but not applied. Read `~/src/dotfiles/README.md`, create only links whose targets exist, and do not assume an `install.sh` helper exists.

## Git workflow

- Create one task branch per change: `agent/<topic>-YYYYMMDD`.
- Do not commit directly to `main` unless the user explicitly requests a small, low-risk direct commit.
- Prefer a squash-merged pull request and keep `main` linear.
- Do not rewrite published branches. For an intentional rewrite, create a backup branch or tag and a bundle before using `--force-with-lease`.
- Preserve unrelated working-tree changes.

## Python environment

- Use the repository-local `.venv/` for Python commands.
- Manage dependencies through `pyproject.toml`; do not install project tools globally.
- Preserve documented interfaces unless a behavior is unsafe or nonfunctional.

## Documentation

- Update existing Markdown files instead of creating new ones unless the user requests a new document.
- Use CommonMark-compatible Markdown and soft-wrap prose with one source line per paragraph or list item.
- Follow the Google developer documentation style guide: use direct language, active voice, sentence-case headings, descriptive links, and code formatting for commands, filenames, and identifiers.
- Prefer current commands and generated help over duplicated static reference lists.
- Preserve historical claims only when Git history supports them.

## Validation

Run the checks appropriate to the change from the repository virtual environment:

```bash
pytest -q
ruff check .
ruff format --check edcloud tests
mypy edcloud/
pre-commit run --all-files
```

Summarize changed files and validation results. Do not create a separate summary document.
