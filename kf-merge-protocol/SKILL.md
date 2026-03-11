---
name: kf-merge-protocol
description: "Reference document defining the merge protocol for Kiloforge agents. NOT user-invocable — embedded by kf-architect, kf-developer, and kf-report."
---

# Kiloforge Merge Protocol

Reference document for the standard merge-to-primary-branch protocol. All agents that merge work into the primary branch **must** follow this protocol.

## Do not invoke directly

This skill is a reference document. It is embedded by:

- **kf-architect** — merges track definitions (metadata merge)
- **kf-developer** — merges implemented code (implementation merge)
- **kf-report** — merges generated reports (metadata merge)

## The `kf-merge` Script

All merge operations use `.agent/kf/bin/kf-merge`, which encapsulates the full protocol. Skills should call this script instead of implementing merge logic inline.

## Two Merge Flows

### 1. Metadata Merge (no verification)

Used by **kf-architect** and **kf-report** — changes are limited to `.agent/kf/` files (track definitions, reports). No test/build verification is needed.

```bash
.agent/kf/bin/kf-merge --holder architect-1 --timeout 0
```

**Steps:** lock → rebase → resolve state conflicts → merge → release

### 2. Implementation Merge (with verification)

Used by **kf-developer** — changes include source code, tests, and configuration. Full verification is mandatory before and after rebase.

```bash
# Pre-merge: run verification on current branch BEFORE lock
make test && make build && make lint

# Then merge with post-rebase verification
.agent/kf/bin/kf-merge \
  --holder developer-1 \
  --timeout 300 \
  --verify "make test && make build && make lint" \
  --reapply ".agent/kf/bin/kf-track update {trackId} --status completed" \
  --cleanup-branch feature/{trackId}
```

**Steps:** validate → lock → rebase → resolve conflicts → validate → merge → release

## Protocol Steps (Detail)

### Step 1 — Resolve primary branch

```bash
PRIMARY_BRANCH=$(.agent/kf/bin/kf-primary-branch)
```

### Step 2 — Locate primary branch worktree

Find the worktree that has the primary branch checked out:
```bash
git worktree list | grep "[${PRIMARY_BRANCH}]"
```

### Step 3 — Acquire merge lock

Uses `kf-merge-lock` which supports dual-mode acquisition:

- **HTTP mode** — preferred when orchestrator is running (`$KF_ORCH_URL`). Uses TTL (120s), heartbeat (30s intervals), server-side long-poll.
- **mkdir mode** — fallback when orchestrator is unreachable. Uses `$(git rev-parse --git-common-dir)/merge.lock`.

```bash
.agent/kf/bin/kf-merge-lock acquire --timeout <seconds>
```

- `--timeout 0` — fail immediately if held (for architects, non-blocking)
- `--timeout 300` — wait up to 5 minutes (for developers with auto-merge)

**CRITICAL: NEVER force-remove another worker's lock.** If the lock appears stale, report and wait for user instructions.

### Step 4 — Start heartbeat

Keeps the lock alive during the merge window:
```bash
while true; do .agent/kf/bin/kf-merge-lock heartbeat; sleep 30; done &
```

### Step 5 — Rebase onto primary branch

```bash
git rebase ${PRIMARY_BRANCH}
```

**On conflict — track state file resolution:**

Track state files (`tracks.yaml`, `deps.yaml`, `conflicts.yaml`) are append/update structures. The primary branch's version is always ground truth. Accept theirs, then re-apply your additions:

```bash
git checkout --theirs .agent/kf/tracks.yaml .agent/kf/tracks/deps.yaml .agent/kf/tracks/conflicts.yaml
git add .agent/kf/tracks.yaml .agent/kf/tracks/deps.yaml .agent/kf/tracks/conflicts.yaml
git rebase --continue
```

Then re-apply via CLI (the `--reapply` command):
```bash
# Architect: re-register new tracks
.agent/kf/bin/kf-track add <id> --title "..." --type <type>

# Developer: re-mark track complete
.agent/kf/bin/kf-track update {trackId} --status completed
```

**For non-state file conflicts** (source code): release lock, report, HALT.

### Step 6 — Post-rebase verification (implementation merge only)

Run the project's verification suite:
```bash
make test && make build && make lint
```

On failure: release lock, report, HALT.

### Step 7 — Fast-forward merge

```bash
git -C {primary-worktree} merge {current-branch} --ff-only
```

Only fast-forward merges are allowed. If the merge cannot fast-forward, something went wrong during rebase — release lock and HALT.

### Step 8 — Release lock and cleanup

1. Stop heartbeat
2. Release lock via `kf-merge-lock release`
3. Delete implementation branch if applicable

**Note:** The home branch is never reset to the primary branch. Home branches may contain user work. Implementation branches are always created from the primary branch directly.

## `kf-merge` Options Reference

| Option | Default | Description |
|--------|---------|-------------|
| `--holder NAME` | basename of cwd | Lock holder identity |
| `--timeout SECONDS` | 0 | Lock acquisition timeout (0=fail if held) |
| `--verify CMD` | (none) | Post-rebase verification command |
| `--conflict-strategy` | theirs | `theirs` or `ours` for track state conflicts |
| `--reapply CMD` | (none) | Command to re-apply track state after conflict resolution |
| `--cleanup-branch NAME` | (none) | Branch to delete after successful merge |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Merge succeeded |
| 1 | Merge failed (lock released, safe to retry) |
| 2 | Lock held by another worker (not acquired) |

## Safety Rules

1. **ONE merge at a time** — enforced via merge lock
2. **Fast-forward only** — no merge commits, no squashing
3. **NEVER force-remove another worker's lock** — report stale locks to the user
4. **Release lock on ANY failure** — the `kf-merge` script handles this via trap
5. **Track state conflicts favor primary branch** — accept theirs, re-apply additions via CLI
6. **Non-state conflicts are blocking** — release lock, report, HALT
