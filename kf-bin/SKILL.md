---
name: kf-bin
description: List available Kiloforge CLI tools with usage and descriptions. Only required when running as a Kiloforge agent.
---

# Kiloforge CLI Tools

Display available CLI tools installed to `.agent/kf/bin/` during project setup.

## Use this skill when

- You want to see what CLI tools are available
- You need usage help for a specific tool

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

## Instructions

When invoked, display the tools table above. If a tool name is provided as an argument, show its usage by running:

```bash
.agent/kf/bin/{tool} --help
```

If tools are not installed, suggest running `/kf-setup`.
