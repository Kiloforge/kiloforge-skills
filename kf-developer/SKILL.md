---
name: kf-developer
description: Receive a track ID, validate it is an active unclaimed track, then implement it following the kiloforge workflow. Worker role in the track generation => approval => push to worker pipeline.
metadata:
  argument-hint: "<track-id> [--disable-auto-merge]"
---

# Kiloforge Developer

Implement a kiloforge track in a parallel worktree workflow. Receives a track ID, validates it is available for work, then executes the full implementation cycle: branch, implement, verify, and merge.

## Use this skill when

- A track has been generated and approved via `/kf-architect`
- You have been assigned a specific track ID to implement
- You are a developer worker in a parallel worktree setup

## Do not use this skill when

- You need to create new tracks (use `/kf-architect` instead)
- The project has no Kiloforge artifacts (use `/kf-setup` first)
- You are working in a single-branch workflow (use `/kf-implement` instead)

---

## After Compaction

When entering the developer role, output this anchor line exactly:

```
ACTIVE ROLE: kf-developer — track {trackId} — skill at ~/.claude/skills/kf-developer/SKILL.md
```

This line is designed to survive compaction summaries. If you see it in your context but can no longer recall the full workflow, re-read the skill file before continuing. For project-specific values, re-read only what you need:

- Verification commands: `.agent/kf/workflow.yaml`
- Track list/statuses: `.agent/kf/bin/kf-track.py list`
- Track progress: `.agent/kf/bin/kf-track-content.py progress {trackId}`
- Main worktree path: `git worktree list`

---

## Worktree Convention

This agent is expected to run in a worktree whose folder name starts with `worker-` (e.g., `worker-1`, `worker-2`, `worker-3`). The corresponding branch name matches the folder name.

### Step 0 — Verify worktree identity and resolve primary branch

```bash
git branch --show-current
git rev-parse --git-common-dir 2>/dev/null
git rev-parse --git-dir 2>/dev/null
git worktree list
```

- The current branch should match `worker-*` (this is the **home branch**)
- If not on a `worker-*` branch, warn but continue
- Record the **primary branch worktree path** from `git worktree list` — needed for merge operations
- Record the **home branch** (the `worker-*` branch) — to return to after merge

---

## Phase 1: Validation

### Step 1 — Run pre-flight check

```bash
eval "$(.agent/kf/bin/kf-preflight.py)"
```

This verifies all required metadata files exist on the primary branch and sets `PRIMARY_BRANCH`. If it fails, it prints an error suggesting `/kf-setup` — **HALT.**

**IMPORTANT: Your home branch is almost always stale.** Always read track state from the primary branch using `--ref ${PRIMARY_BRANCH}` on CLI commands or `git show ${PRIMARY_BRANCH}:<path>` for file reads. Implementation branches are always created from `${PRIMARY_BRANCH}` directly. Do NOT use `git reset --hard`.

### Step 2 — Parse track ID

If no argument was provided:

```
ERROR: Track ID required.

Usage: /kf-developer <track-id>

To see available tracks, run `.agent/kf/bin/kf-track.py list` or /kf-architect to create new ones.
```

**HALT.**

### Step 3 — Validate track exists and is claimable

1. **Check track exists and get its status:**
   ```bash
   .agent/kf/bin/kf-track.py get {trackId}
   ```
   If not found (exits non-zero):
   ```
   ERROR: Track not found — {trackId}

   Available tracks:
   {output from `.agent/kf/bin/kf-track.py list --active`}
   ```
   **HALT.**

   If track status is `completed`:
   ```
   ERROR: Track already complete — {trackId}

   This track has already been implemented and marked complete on ${PRIMARY_BRANCH}.
   ```
   **HALT.**

   If track is marked `in-progress`, check if another worker has it.

2. **Check if another worker has claimed it:**
   ```bash
   git worktree list
   git branch --list 'kf/*'
   ```

   Look for a branch matching `*/{trackId}`. If found:
   ```
   ERROR: Track already claimed — {trackId}

   Branch kf/{type}/{trackId} already exists, indicating another worker is implementing this track.

   Worktree: {worktree path if identifiable}
   Branch:   {branch name}

   Choose a different track or coordinate with the other worker.
   ```
   **HALT.**

3. **Check dependency graph — all prerequisites must be completed:**

   Run the dependency check:
   ```bash
   .agent/kf/bin/kf-track.py deps check {trackId}
   ```

   If the command exits non-zero (BLOCKED), it will list unmet dependencies:
   ```
   ERROR: Dependencies not met — {trackId}

   {output from kf-track deps check}

   Wait for these tracks to complete, or ask the architect to restructure dependencies.
   ```
   **HALT.**

   If `deps.yaml` does not exist, skip this check (backwards compatibility).

### Step 4 — Enter developer mode

```
================================================================================
                    KILOFORGE DEVELOPER — TRACK VALIDATED
================================================================================

Track:    {trackId}
Title:    {title from track.yaml}
Type:     {type}
Tasks:    {total tasks from track.yaml plan}
Phases:   {total phases}

Beginning implementation:
1. Create branch kf/{type}/{trackId} from ${PRIMARY_BRANCH}
2. Implement all tasks following the plan
3. Verify and prepare for merge
================================================================================
```

**Proceed immediately to Phase 2 (Setup).**

Output the compaction anchor:
```
ACTIVE ROLE: kf-developer — track {trackId} — skill at ~/.claude/skills/kf-developer/SKILL.md
```

---

## Phase 2: Setup

### Step 5 — Create implementation branch

Create an implementation branch from the primary branch:

```bash
git checkout -b kf/{type}/{trackId} ${PRIMARY_BRANCH}
```

Branch naming: `kf/{type}/{trackId}` where type comes from metadata (e.g., `kf/feature/auth_20250115100000Z`). The implementation branch is always created from `${PRIMARY_BRANCH}` to ensure it starts with the latest code.

#### Step 5b — Check for stash branches

After creating the implementation branch, check if a previous worker stashed work for this track:

```bash
STASH=$(git branch --list "kf/stash/{trackId}/*" | head -1 | sed 's/^[* ]*//')
if [ -n "$STASH" ]; then
  git merge "$STASH" --no-edit
  git branch -D "$STASH"
  echo "Restored from stash: $STASH"
fi
```

If a stash branch exists, merge it into the fresh implementation branch. This recovers any work saved by a previous agent that was interrupted before completing the track. Delete the stash branch after merging — it's no longer needed.

### Step 6 — Load workflow configuration

Read `.agent/kf/workflow.yaml` and parse:
- Verification commands (e.g., `make test`, `make e2e`)
- TDD strictness level
- Commit strategy

### Step 7 — Load track context

Load track context via CLI (now from the working tree, which is based on the primary branch):
```bash
# Full track content
.agent/kf/bin/kf-track-content.py show {trackId}

# Or section by section for large tracks:
.agent/kf/bin/kf-track-content.py show {trackId} --section spec
.agent/kf/bin/kf-track-content.py show {trackId} --section plan
.agent/kf/bin/kf-track-content.py progress {trackId}

# Check conflict risk with other active tracks
.agent/kf/bin/kf-track.py conflicts list {trackId}
```

Also read project context:
- `.agent/kf/product.yaml`
- `.agent/kf/tech-stack.yaml`
- `.agent/kf/code_styleguides/` (if present)

---

## Phase 3: Implementation

### Step 8 — Execute the plan

Follow the exact same implementation workflow as `/kf-implement`:

- Execute each task in the plan sequentially
- Follow TDD workflow if configured in `workflow.yaml`
- Commit after each task completion using the commit strategy from `workflow.yaml`
- Update task completion via CLI: `.agent/kf/bin/kf-track-content.py task {trackId} <phase>.<task> --done`
- Check progress: `.agent/kf/bin/kf-track-content.py progress {trackId}`
- Run phase verification at the end of each phase
- **Do NOT pause between phases** — proceed continuously through all phases without waiting for user approval

### Step 9 — Mark track complete

After all tasks are done, update all tracking files and commit:

1. **Update track status** using `kf-track` (updates `tracks.yaml`, prunes `deps.yaml`, and cleans `conflicts.yaml` automatically):
   ```bash
   .agent/kf/bin/kf-track.py update {trackId} --status completed
   ```
2. Verify all tasks are marked done: `.agent/kf/bin/kf-track-content.py progress {trackId}`

```bash
git add .agent/kf/tracks.yaml .agent/kf/tracks/deps.yaml .agent/kf/tracks/conflicts.yaml .agent/kf/tracks/{trackId}/
git commit -m "chore: mark track {trackId} complete"
```

---

## Phase 4: Merge

### Step 10 — Report completion and merge (or wait)

By default, auto-merge is enabled — proceed directly to the merge sequence after implementation completes.

If `--disable-auto-merge` **was** provided:

```
================================================================================
                    TRACK COMPLETE — READY TO MERGE
================================================================================
Track:      {trackId} - {title}
Branch:     kf/{type}/{trackId}
Tasks:      {completed}/{total}

Ready to merge. Say "merge" to begin the lock -> rebase -> verify -> merge sequence.
================================================================================
```

**Wait for explicit "merge" command before proceeding.**

If `--disable-auto-merge` was **not** provided (default): skip the pause and proceed directly to the merge sequence.

### Step 11 — Merge sequence

When the user says "merge" (or immediately if auto-merge is enabled (default)), execute the full merge protocol. The developer performs an **implementation merge** — verification is mandatory.

For the full merge protocol details, see `kf-merge-protocol/SKILL.md`.

#### 11a. Pre-merge verification

Run the full verification suite **before** acquiring the lock to avoid holding it during long test runs:

```bash
# Read verification commands from workflow.yaml (e.g., make test && make build && make lint)
VERIFY_CMD="<commands from workflow.yaml>"
eval "$VERIFY_CMD"
```

If verification fails, do **not** attempt to merge. Fix issues first.

#### 11b. Merge via kf-merge

```bash
VERIFY_CMD="<commands from workflow.yaml>"

.agent/kf/bin/kf-merge.py \
  --holder "$(basename $(pwd))" \
  --timeout 300 \
  --verify "$VERIFY_CMD" \
  --reapply ".agent/kf/bin/kf-track.py update {trackId} --status completed" \
  --cleanup-branch kf/{type}/{trackId}
```

- `--timeout 300` — wait up to 5 minutes for the lock (auto-merge mode)
- `--verify` — runs verification again post-rebase (primary branch may have introduced changes)
- `--reapply` — re-marks the track as completed if track state conflicts were resolved
- `--cleanup-branch` — deletes the implementation branch after merge

**With `--disable-auto-merge`:** Use `--timeout 0` instead. If lock is held (exit code 2), report and **HALT** — wait for user to say "merge" to retry.

#### 11c. Post-merge cleanup

After `kf-merge` succeeds:

```bash
# Return to developer home branch
git checkout {worker-home-branch}

# Clean up any stash branches for this track
for b in $(git branch --list "kf/stash/{trackId}/*" | sed 's/^[* ]*//'); do
  git branch -D "$b"
done
```

Report:

```
================================================================================
                         MERGE COMPLETE
================================================================================
Track:       {trackId} - {title}
Merged into: ${PRIMARY_BRANCH}
Branch:      kf/{type}/{trackId} (deleted)
Home branch: {worker-home-branch} (synced to ${PRIMARY_BRANCH})

Developer is ready for next track.
================================================================================
```

---

## Error Handling Summary

| Error                      | Action                                                   |
|----------------------------|----------------------------------------------------------|
| No track ID provided       | Display usage, **HALT**                                  |
| Track not found            | List available tracks from primary branch, **HALT**      |
| Track already complete     | Notify, **HALT**                                         |
| Track already claimed      | Show claiming worker/branch, **HALT**                    |
| Track missing spec/plan    | Suggest regeneration, **HALT**                           |
| Kiloforge not initialized  | Suggest `/kf-setup`, **HALT**                     |
| Verification failure       | Report details, offer fix/retry/wait                     |
| Merge lock held            | Report (`kf-merge-lock status`), wait for other worker |
| Rebase conflict (state files) | Accept theirs, continue rebase, re-apply via CLI     |
| Rebase conflict (source code) | Release lock, report, **HALT**                       |
| Post-rebase verify failure | Release lock, report, offer fix/retry/abort              |
| Merge not fast-forwardable | Release lock, offer re-rebase or abort                   |

---

## Flags Summary

| Flag | Effect |
|------|--------|
| (none) | Default: implement, auto-merge (poll merge lock if held) |
| `--disable-auto-merge` | Pause after implementation; wait for explicit "merge" command |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KF_ORCH_URL` | `http://localhost:4001` | Orchestrator URL for HTTP lock API |

## Merge Lock Modes

The merge lock is managed by the shared `.agent/kf/bin/kf-merge-lock.py` helper, which supports dual-mode acquisition:

1. **HTTP mode** — Preferred when kiloforge orchestrator is running. Uses TTL (120s), heartbeat (every 30s), and server-side long-poll for `--auto-merge`. Crash recovery via automatic TTL expiry.
2. **mkdir mode** — Fallback when orchestrator is unreachable. Uses `$(git rev-parse --git-common-dir)/merge.lock` directory. PID-based stale detection with auto-cleanup.

Detection is automatic. Run `kf-merge-lock status` to inspect current lock state.

## Critical Rules

1. **ALWAYS validate before implementing** — never start work on an invalid or claimed track
2. **ALWAYS read track state from the primary branch** — resolve from `.agent/kf/config.yaml`, default `main`. Use `git show ${PRIMARY_BRANCH}:<path>`, not local working tree
3. **NEVER push to remote** — all branches are local only
4. **Auto-merge is the default** — only pause for explicit "merge" command when `--disable-auto-merge` is provided
5. **ALWAYS verify after rebase** — full verification after rebase, before merge
6. **ALWAYS use --ff-only** — clean fast-forward merges only
7. **ONE merge at a time** — enforce via cross-worktree merge lock (HTTP preferred, mkdir fallback)
8. **HALT on any failure** — do not continue past errors without user input
9. **Follow workflow.yaml** — all TDD, commit, and verification rules apply
10. **Return to home branch** — always checkout back to `worker-*` branch after merge
11. **ALWAYS send heartbeat** — start heartbeat after lock acquire, stop after release
13. **NEVER force-remove another worker's lock** — if the merge lock is held, HALT and wait for user instructions. Do not `rm -rf` the lock directory or force-release HTTP locks held by others.
