---
name: kf-bin
description: List available Kiloforge CLI tools with usage and descriptions. Only required when running as a Kiloforge agent.
---

# Kiloforge CLI Tools

Display available CLI tools installed to `~/.kf/bin/` during project setup.

## Use this skill when

- You want to see what CLI tools are available
- You need usage help for a specific tool

## Runtime Environment

All CLI tools are Python scripts that require the Kiloforge virtual environment at `~/.kf/.venv`. This venv is created automatically during `/kf-setup` and contains Python 3 with PyYAML.

**IMPORTANT: NEVER install packages globally.** Do not use `pip install`, `pip3 install`, or `--break-system-packages`. All dependencies are installed into the `~/.kf/.venv` venv only. If the venv is missing, `kf-preflight.py` will auto-create it.

**Running scripts:** The venv is activated via `kf-preflight.py`, which every skill evals at startup. This puts `~/.kf/.venv/bin/python` on PATH so all scripts pick up the correct interpreter:

```bash
# Preflight activates the venv and sets PRIMARY_BRANCH
eval "$(~/.kf/bin/kf-preflight.py)"

# All subsequent script calls use the venv automatically
~/.kf/bin/kf-track.py list --active
```

**If running scripts outside of a skill** (e.g., manual testing), activate the venv first:

```bash
source ~/.kf/.venv/bin/activate && ~/.kf/bin/kf-conductor.py status
```

**If the venv is missing or broken**, restore it:

```bash
# Create venv (skip if it exists)
python3 -m venv ~/.kf/.venv

# Install dependencies
~/.kf/.venv/bin/pip install pyyaml
```

For detailed platform-specific installation instructions (macOS, Linux, Windows), see `references/python-setup.md`.

## Tools

| Tool | Description |
|------|-------------|
| `kf-preflight.py` | Pre-flight check: verifies metadata files and tools exist, sets `PRIMARY_BRANCH` |
| `kf-primary-branch.py` | Resolves the primary branch from config.yaml |
| `kf-track.py` | Track registry management (add, list, update, deps, conflicts) |
| `kf-track-content.py` | Track content management (init, show, spec, plan, task progress) |
| `kf-merge.py` | Unified merge protocol (lock, rebase, verify, merge, release) |
| `kf-merge-lock.py` | Cross-worktree branch lock (acquire, release, heartbeat) |
| `kf-claim.py` | Per-worktree track claim lock (acquire, release, list, find) |
| `kf-dispatch.py` | Compute dispatch assignments for idle developer worktrees |
| `kf-worktree-env.py` | Detect git worktree context and export env vars |
| `kf-status.py` | Full project status in one call (workers, tracks, dispatch) |
| `kf-install.py` | Install or update CLI tools in a project (venv, scripts, shebangs) |
| `kf-conductor.py` | Tmux-based multi-agent orchestration (spawn, dispatch, status, kill, cleanup) |

## Instructions

When invoked, display the tools table above. If a tool name is provided as an argument, show its usage by running:

```bash
~/.kf/bin/{tool}.py --help
```

If tools are not installed, suggest running `/kf-setup`.
