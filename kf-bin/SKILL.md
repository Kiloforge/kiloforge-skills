---
name: kf-bin
description: Kiloforge CLI tools. NOT user-invocable — installed to .agent/kf/bin/ by kf-setup.
---

# Kiloforge CLI Tools

This directory contains the CLI tools installed to `.agent/kf/bin/` during `/kf-setup`. These are internal tools used by other kf-* skills — not invoked directly by users.

## Tools

| Tool | Type | Description |
|------|------|-------------|
| `kf-preflight` | sh | Pre-flight check: verifies metadata files and tools exist, sets `PRIMARY_BRANCH` |
| `kf-primary-branch` | sh | Resolves the primary branch from config.yaml |
| `kf-track` | bash | Track registry management (add, list, update, deps, conflicts) |
| `kf-track-content` | python3 | Track content management (init, show, spec, plan, task progress) |
| `kf-merge` | sh | Unified merge protocol (lock, rebase, verify, merge, release) |
| `kf-merge-lock` | bash | Cross-worktree merge lock (acquire, release, heartbeat) |
| `kf-worktree-env` | bash | Detect git worktree context and export env vars |
