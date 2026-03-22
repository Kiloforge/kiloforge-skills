---
name: kf-bin
description: "Kiloforge CLI tools reference and shared documentation hub. Lists available tools, provides runtime environment info, and hosts reference docs used by other kf-* skills."
---

# Kiloforge CLI & Reference Hub

Reference for CLI tools installed to `~/.kf/bin/` and shared documentation used by other kf-* skills.

## Use this skill when

- You need to see what CLI tools are available
- You need usage help for a specific tool
- Another skill references a doc in `references/` (e.g., merge protocol, data guardian)

## Runtime Environment

All CLI tools require the Kiloforge venv at `~/.kf/.venv` (auto-created during `/kf-setup`).

**NEVER install packages globally.** All dependencies go into `~/.kf/.venv` only.

Scripts are activated via preflight, which every skill evals at startup:
```bash
eval "$(~/.kf/bin/kf-preflight.py)"
```

For platform-specific Python setup, see `references/python-setup.md`.

## Tools

| Tool | Description |
|------|-------------|
| `kf-preflight.py` | Pre-flight check: verifies metadata, sets `PRIMARY_BRANCH` |
| `kf-primary-branch.py` | Resolves primary branch from config.yaml |
| `kf-track.py` | Track registry (add, list, update, claim, deps, conflicts, spec) |
| `kf-track-content.py` | Track content (init, show, spec fields, plan, task, progress) |
| `kf-merge.py` | Merge protocol (lock, rebase, verify, merge, release) |
| `kf-merge-lock.py` | Cross-worktree branch lock (acquire, release, heartbeat) |
| `kf-claim.py` | Per-worktree track claim (acquire, release, list, find) |
| `kf-dispatch.py` | Dispatch assignments for idle developer worktrees |
| `kf-worktree-env.py` | Detect worktree context, export env vars |
| `kf-status.py` | Full project status (workers, tracks, spec, dispatch) |
| `kf-install.py` | Install/update CLI tools (venv, scripts, shebangs) |
| `kf-conductor.py` | Tmux multi-agent orchestration (spawn, dispatch, status) |

## Shared Reference Docs

| Document | Used by | Description |
|----------|---------|-------------|
| `references/merge-protocol.md` | kf-architect, kf-developer | Lock → rebase → verify → merge protocol |
| `references/data-schema.md` | all kf-* skills | Authoritative data schema for all .agent/kf/ files |
| `references/data-guardian.md` | kf-repair, kf-validate | Corruption detection heuristics and response |
| `references/implement-workflow.md` | kf-developer | TDD task execution loop details |
| `references/new-track-interactive.md` | kf-architect | Interactive Q&A track creation flow |
| `references/bulk-archive.md` | kf-manage | Bulk archive workflow |
| `references/compact-archive.md` | kf-manage | Compact archive workflow |
| `references/cli-tools.md` | (this skill) | Extended CLI tool descriptions |
| `references/python-setup.md` | kf-setup | Platform-specific Python installation |

## Instructions

When invoked, display the tools table. If a tool name is given as argument:
```bash
~/.kf/bin/{tool}.py --help
```

If tools are not installed, suggest `/kf-setup`.
