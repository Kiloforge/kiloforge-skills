# Kiloforge Skills

[Claude Code](https://claude.com/claude-code) skills for AI-powered project management with [Kiloforge](https://github.com/Kiloforge/kiloforge).

## Install

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/Kiloforge/kiloforge-skills/main/install.sh | sh
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/Kiloforge/kiloforge-skills/main/install.ps1 | iex
```

### Prerequisites

- **Python 3.8+** — `python3` (or `python` on Windows) in PATH
- **Git** — `git` in PATH
- **Claude Code** — `claude` in PATH ([install](https://claude.com/claude-code))

### What gets installed

| Location | Contents |
|----------|----------|
| `~/.claude/skills/kf-*` | Skill definitions (SKILL.md + references/) |
| `~/.kf/bin/` | CLI tools (Python scripts) |
| `~/.kf/.venv/` | Python venv with PyYAML |
| `~/.kf/VERSION` | Installed version |

## Getting Started

After installing, open Claude Code in any project:

```bash
cd my-project
claude
```

Then run `/kf-setup` to initialize Kiloforge, or `/kf-getting-started` for a guided bootstrapping experience.

## Skills

### Core workflow

| Skill | Description |
|-------|-------------|
| `/kf-getting-started` | Interactive project bootstrapper with platform-aware defaults |
| `/kf-setup` | Initialize Kiloforge artifacts (product, tech stack, spec, workflow) |
| `/kf-architect` | Research codebase, create track specs with phased plans |
| `/kf-developer` | Claim and implement tracks with TDD workflow |
| `/kf-status` | Project status, active tracks, and spec fulfillment |

### Management

| Skill | Description |
|-------|-------------|
| `/kf-manage` | Archive, bulk-archive, compact, restore, delete, rename tracks |
| `/kf-validate` | Validate project artifacts for completeness and consistency |
| `/kf-repair` | Audit and repair system integrity |
| `/kf-report` | Generate project timeline, velocity, and cost reports |

### Orchestration

| Skill | Description |
|-------|-------------|
| `/kf-conductor` | Tmux-based multi-agent orchestration |

### Utilities

| Skill | Description |
|-------|-------------|
| `/kf-conflict-resolver` | Resolve git merge conflicts |
| `/kf-revert` | Git-aware undo by logical work unit |
| `/kf-update` | Update skills and CLI tools to latest release |
| `/kf-bin` | CLI tools reference and shared documentation hub |

### Advisors

| Skill | Description |
|-------|-------------|
| `/kf-advisor-product` | Product strategy and competitive analysis |
| `/kf-advisor-reliability` | Codebase reliability audit |

## Update

The tools check for updates daily. When a new version is available, you'll see:

```
[kf] Update available: 0.4.0 → 0.5.0. Run /kf-update to upgrade.
```

Or re-run the install script at any time.

## License

MIT
