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

All merge operations use `~/.kf/bin/kf-merge.py`, which encapsulates the full protocol. Skills should call this script instead of implementing merge logic inline.

## Two Merge Flows

### 1. Metadata Merge (no verification)

Used by **kf-architect** and **kf-report** — changes are limited to `.agent/kf/` files (track definitions, reports). No test/build verification is needed.

Track content (unique per-track directories) is committed first. Then a single lock window handles rebase, registry update, and merge. Registry commands run against the clean rebased state so conflicts on shared files are eliminated.

```bash
# Architect: commit track content first, then single lock window for everything else
~/.kf/bin/kf-merge.py --holder architect-1 --timeout 0 \
  --registry-cmd "~/.kf/bin/kf-track.py add X --title '...' --type feature"
```

**Steps:** lock → rebase → registry update → commit → ff-merge → release

For **kf-report** (no registry update needed):

```bash
~/.kf/bin/kf-merge.py --holder report-1 --timeout 0
```

**Steps:** lock → rebase → merge → release

### 2. Implementation Merge (with verification)

Used by **kf-developer** — changes include source code, tests, and configuration. Full verification is mandatory before and after rebase.

```bash
# Pre-merge: run verification on current branch BEFORE lock
make test && make build && make lint

# Then merge with post-rebase verification
~/.kf/bin/kf-merge.py \
  --holder developer-1 \
  --timeout 300 \
  --verify "make test && make build && make lint" \
  --reapply "~/.kf/bin/kf-track.py update {trackId} --status completed" \
  --cleanup-branch feature/{trackId}
```

**Steps:** validate → lock → rebase → resolve conflicts → validate → merge → release

## Protocol Steps (Detail)

### Step 1 — Resolve primary branch

```bash
PRIMARY_BRANCH=$(~/.kf/bin/kf-primary-branch.py)
```

### Step 2 — Locate primary branch worktree

Find the worktree that has the primary branch checked out:
```bash
git worktree list | grep "[${PRIMARY_BRANCH}]"
```

### Step 3 — Acquire branch lock

Uses `kf-merge-lock` which supports dual-mode acquisition:

- **HTTP mode** — preferred when orchestrator is running (`$KF_ORCH_URL`). Uses TTL (120s), heartbeat (30s intervals), server-side long-poll.
- **mkdir mode** — fallback when orchestrator is unreachable. Uses `$(git rev-parse --git-common-dir)/merge.lock`.

```bash
~/.kf/bin/kf-merge-lock.py acquire --timeout <seconds>
```

- `--timeout 0` — fail immediately if held (for architects, non-blocking)
- `--timeout 300` — wait up to 5 minutes (for developers with auto-merge)

**CRITICAL: NEVER force-remove another worker's lock.** If the lock appears stale, report and wait for user instructions.

### Step 4 — Start heartbeat

Keeps the lock alive during the merge window:
```bash
while true; do ~/.kf/bin/kf-merge-lock.py heartbeat; sleep 30; done &
```

### Step 5 — Rebase onto primary branch

```bash
git rebase ${PRIMARY_BRANCH}
```

**On conflict — track state file resolution:**

Track state files (`tracks.yaml`, `deps.yaml`, `conflicts.yaml`) are append/update structures. The primary branch's version is always ground truth. During rebase, `--ours` = the branch being rebased onto (primary), `--theirs` = the commit being replayed (worker's stale version). Accept the primary branch's version with `--ours`, then re-apply your additions:

```bash
git checkout --ours .agent/kf/tracks.yaml .agent/kf/tracks/deps.yaml .agent/kf/tracks/conflicts.yaml
git add .agent/kf/tracks.yaml .agent/kf/tracks/deps.yaml .agent/kf/tracks/conflicts.yaml
git rebase --continue
```

Then re-apply via CLI (the `--reapply` command):
```bash
# Architect: re-register new tracks
~/.kf/bin/kf-track.py add <id> --title "..." --type <type>

# Developer: re-mark track complete
~/.kf/bin/kf-track.py update {trackId} --status completed
```

**For non-state file conflicts** (source code): the lock stays held. The agent must resolve the conflicts manually, `git add` the resolved files, `git rebase --continue`, and then proceed with the merge. The lock is only released after the merge completes (or the agent explicitly aborts with `git rebase --abort && kf-merge-lock release`). This prevents other workers from merging in between and causing cascading conflicts.

### Step 6 — Registry update (metadata merge only)

If `--registry-cmd` is provided, run the command after rebase. This updates shared registry files (`tracks.yaml`, `deps.yaml`, `conflicts.yaml`) against the latest primary branch state, then commits the changes. Since the rebase has already brought in all other workers' changes, the registry update is conflict-free.

```bash
eval "$REGISTRY_CMD"
git add .agent/kf/tracks.yaml .agent/kf/tracks/deps.yaml .agent/kf/tracks/conflicts.yaml
git commit -m "chore(kf): update track registry"
```

### Step 7 — Post-rebase verification (implementation merge only)

Run the project's verification suite:
```bash
make test && make build && make lint
```

On failure: release lock, report, HALT.

### Step 8 — Fast-forward merge

```bash
git -C {primary-worktree} merge {current-branch} --ff-only
```

Only fast-forward merges are allowed. If the merge cannot fast-forward, something went wrong during rebase — release lock and HALT.

### Step 9 — Release lock and cleanup

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
| `--registry-cmd CMD` | (none) | Registry update command to run after rebase, before merge. Auto-staged and committed. Used by architects to update shared registry files against latest state. |
| `--cleanup-branch NAME` | (none) | Branch to delete after successful merge |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Merge succeeded |
| 1 | Merge failed (lock released, safe to retry) |
| 2 | Lock held by another worker (not acquired) |
| 3 | Unresolved conflicts — lock STILL HELD, agent must resolve and retry |

## Safety Rules

1. **NEVER work in the primary branch worktree** — agents must work in their own worktree on their own branch. The primary branch worktree is a merge target only. `kf-merge` will refuse to run if the current branch is the primary branch.
2. **ONE merge at a time** — enforced via branch lock
3. **Fast-forward only** — no merge commits, no squashing
4. **NEVER force-remove another worker's lock** — report stale locks to the user
5. **Release lock on ANY failure** — the `kf-merge` script handles this via trap
6. **Track state conflicts favor primary branch** — accept theirs, re-apply additions via CLI
7. **Non-state conflicts hold the lock** — resolve conflicts while locked, only release after merge completes or explicit abort
