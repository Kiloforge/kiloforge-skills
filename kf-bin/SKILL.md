---
name: kf-bin
description: List available Kiloforge CLI tools with usage and descriptions. Only required when running as a Kiloforge agent.
---

# Kiloforge CLI Tools

Display available CLI tools installed to `.agent/kf/bin/` during project setup.

## Use this skill when

- You want to see what CLI tools are available
- You need usage help for a specific tool

## Runtime Environment

All CLI tools are Python scripts that require the Kiloforge virtual environment at `.agent/kf/.venv`. This venv is created automatically during `/kf-setup` and contains Python 3 with PyYAML.

**IMPORTANT: NEVER install packages globally.** Do not use `pip install`, `pip3 install`, or `--break-system-packages`. All dependencies are installed into the project-local venv only. If the venv is missing, `kf-preflight.py` will auto-create it.

**Running scripts:** During setup, script shebangs are rewritten to point directly at the venv interpreter (`.agent/kf/.venv/bin/python`), so scripts run with the correct environment automatically:

```bash
# Scripts are executable and use the venv automatically via their shebang
.agent/kf/bin/kf-track.py list --active
```

**If the venv is missing or broken**, restore it:

```bash
# Create venv (skip if it exists)
python3 -m venv .agent/kf/.venv

# Install dependencies
.agent/kf/.venv/bin/pip install pyyaml

# Rewrite shebangs to use the venv
KF_PYTHON=".agent/kf/.venv/bin/python"
for f in .agent/kf/bin/*.py; do
  sed -i.bak "1s|.*|#!$KF_PYTHON|" "$f" && rm -f "$f.bak"
done
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

## Instructions

When invoked, display the tools table above. If a tool name is provided as an argument, show its usage by running:

```bash
.agent/kf/bin/{tool}.py --help
```

If tools are not installed, suggest running `/kf-setup`.
