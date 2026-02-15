# Agent notes (repo workflow)

## Mission

Build and maintain a single-instance AWS EC2 personal cloud lab (edcloud) accessed via Tailscale, with Portainer for container management. Focus: simple, cost-effective, secure infrastructure as a portfolio piece.

Operational model: control and orchestration should work from low-power operator nodes (for example, Raspberry Pi Zero 2 W class ARM systems), not require a heavyweight workstation.

## Start-here order (for new agent sessions)

1. `README.md`
2. `SECURITY.md` (understand threat model: Tailscale-only, single-operator)
3. `DESIGN.md` (why not Terraform, why Portainer, cost trade-offs)
4. `SETUP.md` (operator runbook + rebuild/backup policy)
5. Code: `edcloud/` package (6 modules: cli, ec2, snapshot, tailscale, config, aws_check)

## Source-of-truth map

- **AWS resources**: Tag-based discovery (`edcloud:managed=true`) — no local state file
- **Configuration**: `edcloud/config.py` + AWS SSM Parameter Store (runtime secrets) + optional environment variables
- **User data**: `cloud-init/user-data.yaml` (instance bootstrap)
- **Tests**: `tests/` (pytest with mocked boto3)
- **Baseline tools/settings policy**: `SETUP.md` + `cloud-init/user-data.yaml`
- **Backup/recovery policy**: `SETUP.md` (snapshot cadence + restore drills)
- **Operator command surface**: `edc` (with `edcloud` compatibility alias)

## Task list source

- **Primary active TODOs**: `SETUP.md` section `Active Priorities`
- **Code-level TODO scan** (if needed):
  - `grep -RInE "TODO|FIXME|TBD|\\[ \\]" README.md SETUP.md DESIGN.md AGENTS.md edcloud tests cloud-init compose`

## Constraints

### Python environment

- Use the repo-local venv at `.venv/` for all Python commands
- Do not install anything globally or modify system Python
- Dependencies managed via `pyproject.toml`
- Prefer Python over Bash when both are appropriate for the task.

### Testing and validation

Pre-commit hooks are installed and configured. Run when requested:

```bash
# In .venv
pytest -q                    # Unit tests
ruff check .                 # Linting
mypy edcloud tests           # Type checking (if requested)
pre-commit run --all-files   # Full suite
```

### Commit discipline

- Commits should be atomic and focused
- Security changes go in dedicated commits with clear audit trails
- Document "why" in commit messages for non-obvious changes

### Documentation policy

**Critical**: Do NOT create new markdown files unless explicitly requested.

- Update existing docs (README, SECURITY, DESIGN) instead of creating new ones
- Use CommonMark markdown (no GFM extensions unless necessary)
- Bias toward self-documenting code and directory structure over documentation
- Exception: Security-critical or compliance documentation (SECURITY.md exists)

## Do-not-break constraints

- Keep Python execution in `.venv/` only
- Avoid global/system package installs
- Do not remove AWS tags from resources (breaks tag-based discovery)
- Do not hardcode credentials, API keys, or resource IDs in code
- Preserve Tailscale-only access model (no security group inbound rules)
- Keep host baseline reproducible: core tools/settings must be encoded in `cloud-init/user-data.yaml`, not manual-only.
- Preserve backup posture: keep snapshot workflow and restore-drill guidance current.

## Expected output shape for implementation work

- Summarize changes by file path
- State validation performed (or explicitly state "none performed")
- Do not create summary documents unless requested

## Architecture principles (from DESIGN.md)

- **Simplicity over abstraction**: This is a small single-instance deployment, not Terraform scale
- **Tag-based discovery**: No state file = works from multiple devices
- **Cost-awareness**: Auto-shutdown after 30min idle, root+state volume model, snapshot cap guardrail
- **Security-first**: Tailscale-only, no public SSH, IMDSv2 enforced
- **Portfolio-focused**: Self-documenting code with design decisions maintained cleanly and briefly in design.md

## Skill rubric vs. agent notes

**When to use AGENTS.md** (this file):

- Repository-specific context (mission, constraints, architecture)
- Workflow conventions (testing, commits, docs policy)
- "What not to do" guardrails for this specific codebase

**When to use a generic skill rubric**:

- Cross-project capabilities (Python proficiency, AWS knowledge, security practices)
- Evaluation criteria for task difficulty
- Agent capability self-assessment

**Verdict**: These are complementary. AGENTS.md is a "how to work with THIS repo" guide. A skill rubric would be "what skills are needed for repos LIKE this." For a single-repo context, AGENTS.md is sufficient.

## Best practices: agent guidance files

### Do's

- **Start with mission/context**: Cold-start agents need the "why" before the "how"
- **Reference, don't duplicate**: Link to existing docs instead of repeating them
- **Constraints > instructions**: Tell agents what NOT to do (clearer boundaries)
- **Self-documenting structure**: `edcloud/` package name tells you it's the main code
- **Update on major changes**: Keep this file current or it becomes misleading

### Don'ts

- **Don't write a novel**: Agents scan for keywords; walls of text get ignored
- **Don't duplicate README/CONTRIBUTING**: Those are for humans; this is for agents
- **Don't prescribe every detail**: Trust agent reasoning for common patterns
- **Don't make it a skill rubric**: That's for agent evaluation, not repo workflow
- **Don't create extra docs**: Update this file instead

### Format tips

- Use `##` for major sections (mission, constraints, etc.)
- Use `###` sparingly (sub-topics only when truly needed)
- Use lists for scannable content
- Use code blocks for commands, not narrative
- Keep total length under 200 lines (this file: ~150 lines)

### Meta-documentation principle

If your AGENTS.md needs extensive explanation, your repository structure is probably too complex. Good agent notes are short because the repo itself is well-organized.

**This repository's structure**: core docs (README, SECURITY, DESIGN, SETUP, AGENTS) + 1 package (`edcloud/`) + 1 test dir (`tests/`). Self-documenting. AGENTS.md adds workflow constraints, not architectural explanation.

---

**Why this file exists**: GitHub Copilot and similar agents aren't aware of project-specific constraints like "don't create extra docs" or "use tag-based discovery, not state files." This file fills that gap without replacing human-focused documentation.
