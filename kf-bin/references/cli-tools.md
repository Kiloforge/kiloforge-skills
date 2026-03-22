# Kiloforge CLI Tools

CLI tools installed to `~/.kf/bin/` during project setup.

## Runtime Environment

All CLI tools are Python scripts that require the Kiloforge virtual environment at `~/.kf/.venv`. This venv is created automatically during `/kf-setup` and contains Python 3 with PyYAML.

**Running scripts:** The venv is activated via `kf-preflight.py`, which every skill evals at startup:

```bash
eval "$(~/.kf/bin/kf-preflight.py)"
```

## Tools

| Tool | Description |
|------|-------------|
| `kf-preflight.py` | Pre-flight check: verifies metadata files and tools exist, sets `PRIMARY_BRANCH` |
| `kf-primary-branch.py` | Resolves the primary branch from config.yaml |
| `kf-track.py` | Track registry management (add, list, update, claim, deps, conflicts, spec) |
| `kf-track-content.py` | Track content management (init, show, spec, plan, task progress) |
| `kf-merge.py` | Unified merge protocol (lock, rebase, verify, merge, release) |
| `kf-merge-lock.py` | Cross-worktree branch lock (acquire, release, heartbeat) |
| `kf-claim.py` | Per-worktree track claim lock (acquire, release, list, find) |
| `kf-dispatch.py` | Compute dispatch assignments for idle developer worktrees |
| `kf-worktree-env.py` | Detect git worktree context and export env vars |
| `kf-status.py` | Full project status in one call (workers, tracks, dispatch) |
| `kf-install.py` | Install or update CLI tools in a project (venv, scripts, shebangs) |
| `kf-conductor.py` | Tmux-based multi-agent orchestration (spawn, dispatch, status, kill, cleanup) |

## Getting help

```bash
~/.kf/bin/{tool}.py --help
```

## If tools are missing

Run `/kf-setup` to install, or `/kf-update` to refresh.
