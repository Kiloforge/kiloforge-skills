---
name: kf-developer
description: Receive a track ID, validate it is an active unclaimed track, then implement it following the kiloforge workflow.
metadata:
  argument-hint: "<track-id> [--disable-auto-merge] [--auto-exit[=SECONDS]]"
---

# Kiloforge Developer

Implement a kiloforge track in a parallel worktree workflow. Receives a track ID, validates it is available for work, then executes the full implementation cycle: branch, implement, verify, and merge.

## Use this skill when

- A track exists and is available for implementation
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
- Track list/statuses: `~/.kf/bin/kf-track.py list --ref ${PRIMARY_BRANCH}`
- Track progress: `~/.kf/bin/kf-track-content.py progress {trackId}`
- Main worktree path: `git worktree list`

---

## Worktree Convention

This agent runs in a dedicated git worktree. The worktree folder name is the agent's identity and its **home branch** name. Naming conventions vary — worktrees may be named `kfc-<id>-worker-N` (conductor-managed), `developer-N`, `worker-N`, or any other name. The role (developer, architect, etc.) is determined by the skill invoked, **not** by the worktree name.

### Step 0 — Verify worktree identity and resolve primary branch

```bash
git branch --show-current
git rev-parse --git-common-dir 2>/dev/null
git rev-parse --git-dir 2>/dev/null
git worktree list
```

- Record the current branch — this is the **home branch**
- Record the **primary branch worktree path** from `git worktree list` — needed for merge operations
- Record the **home branch** name — to return to after merge

**CRITICAL: Verify you are NOT in the primary branch worktree.** Compare your current working directory against the primary branch worktree path from `git worktree list`. If they match, you are in the main worktree — **HALT immediately:**

```
ERROR: You are in the primary branch worktree. Agents must NEVER work
in the main worktree. Use your own worker worktree instead.

Current directory: $(pwd)
Primary worktree:  {primary worktree path}
```

The main worktree is a merge target only — no agent should ever checkout branches, commit, or modify files there. All work happens in dedicated worker worktrees.

---

## Phase 1: Claim Track

### Step 1 — Validate and claim in one step

If no track ID argument was provided, **HALT** with usage instructions.

Otherwise, run a single command that validates the track, checks dependencies, and acquires the claim:

```bash
eval "$(~/.kf/bin/kf-preflight.py)" && ~/.kf/bin/kf-track.py claim {trackId}
```

This command does everything in one step:
1. Reads `PRIMARY_BRANCH` from project config
2. Loads track state from the primary branch
3. Validates the track exists and is not completed/archived
4. Checks all dependencies are satisfied
5. Acquires the worktree claim lock atomically

**If exit code is 0:** the track is claimed. The output contains structured key=value lines (`PRIMARY_BRANCH=`, `TITLE=`, `TYPE=`, `SPEC_CONSTRAINED_BY=`, etc.). Parse these and proceed.

**If exit code is non-zero:** the error message explains why (track not found, already completed, deps blocked, claim held by another worktree). **HALT.**

**CRITICAL — Primary branch usage:**
- The `claim` output includes `PRIMARY_BRANCH=<value>`. Use this value for ALL subsequent commands.
- **NEVER hardcode `main`** — always use `${PRIMARY_BRANCH}` from the claim output.
- **NEVER read local `.agent/kf/` files directly** — your worktree is stale. Always use `--ref ${PRIMARY_BRANCH}` or `git show ${PRIMARY_BRANCH}:<path>`.
- Implementation branches are always created from `${PRIMARY_BRANCH}`.

### Step 2 — Enter developer mode

```
================================================================================
                    KILOFORGE DEVELOPER — TRACK CLAIMED
================================================================================

Track:    {trackId}
Title:    {TITLE from claim output}
Type:     {TYPE from claim output}

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

### Step 3 — Create implementation branch

Create an implementation branch from the primary branch:

```bash
git checkout -b kf/{type}/{trackId} ${PRIMARY_BRANCH}
```

Branch naming: `kf/{type}/{trackId}` where type comes from metadata (e.g., `kf/feature/auth_20250115100000Z`). The implementation branch is always created from `${PRIMARY_BRANCH}` to ensure it starts with the latest code.

#### Step 3b — Check for stash branches

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

### Step 4 — Load workflow configuration

Read `.agent/kf/workflow.yaml` and parse:
- Verification commands (e.g., `make test`, `make e2e`)
- TDD strictness level
- Commit strategy

### Step 5 — Load track context

Load track context via CLI (now from the working tree, which is based on the primary branch):
```bash
# Full track content
~/.kf/bin/kf-track-content.py show {trackId}

# Or section by section for large tracks:
~/.kf/bin/kf-track-content.py show {trackId} --section spec
~/.kf/bin/kf-track-content.py show {trackId} --section plan
~/.kf/bin/kf-track-content.py progress {trackId}

# Check conflict risk with other active tracks
~/.kf/bin/kf-track.py conflicts list {trackId} --ref ${PRIMARY_BRANCH}

# Check spec context — what product items this track fulfills and what technical constraints apply
~/.kf/bin/kf-track.py spec validate {trackId} --ref ${PRIMARY_BRANCH}
```

The `spec validate` command shows which product spec items this track is `required-for` (deliverables) and which technical spec items it is `constrained-by` (implementation rules to follow). If the track has no `spec_refs` or no spec exists, this is silently skipped.

Also read project context:
- `.agent/kf/product.yaml`
- `.agent/kf/tech-stack.yaml`
- `.agent/kf/code_styleguides/` (if present)

---

## Phase 3: Implementation

### Step 6 — Execute the plan

Follow the exact same implementation workflow as `/kf-implement`:

- Execute each task in the plan sequentially
- Follow TDD workflow if configured in `workflow.yaml`
- Commit after each task completion using the commit strategy from `workflow.yaml`
- Update task completion via CLI: `~/.kf/bin/kf-track-content.py task {trackId} <phase>.<task> --done`
- Check progress: `~/.kf/bin/kf-track-content.py progress {trackId}`
- Run phase verification at the end of each phase
- **Do NOT pause between phases** — proceed continuously through all phases without waiting for user approval

### Step 7 — Pre-completion spec check

Before marking the track complete, verify alignment with spec items:

```bash
~/.kf/bin/kf-track.py spec validate {trackId}
```

If the track has `constrained-by` or `relates-to` spec refs, review each one:

- **constrained-by** — Read the technical spec item's description. Verify the implementation actually follows the constraint (e.g., if `tech.api.cursor-pagination` is listed, confirm list endpoints use cursor pagination, not offset). If a constraint was not followed, fix the implementation before proceeding.
- **relates-to** — Read the related spec item. Verify the implementation is consistent with it (e.g., no conflicting patterns or duplicated functionality). This is informational — note any concerns but don't block completion.

If the track has no spec_refs or no spec exists, skip this step.

### Step 8 — Mark track complete

After all tasks are done and spec alignment is verified, update all tracking files and commit:

1. **Update track status** using `kf-track` (updates meta.yaml and cleans conflicts automatically):
   ```bash
   ~/.kf/bin/kf-track.py update {trackId} --status completed
   ```
2. Verify all tasks are marked done: `~/.kf/bin/kf-track-content.py progress {trackId}`

3. **Assess spec fulfillment** (if the track has `required-for` spec refs):

   ```bash
   ~/.kf/bin/kf-track.py spec validate {trackId}
   ```

   Check the fulfillment status in the output. Only items this track is `required-for` are relevant. Look for `READY` lines.

   **If any items show `READY`** (this track was the last required track), assess each:

   - Re-read the product spec item's title, description, and priority
   - Review the implementation across all contributing tracks (listed in the output)
   - Check any `constrained-by` technical spec items — verify the implementation follows each constraint
   - Verify the capability described by the product spec item actually works end-to-end

   If the assessment passes:
   ```bash
   ~/.kf/bin/kf-track.py spec op fulfilled <item-id>
   ~/.kf/bin/kf-track.py spec op finalize --description "Fulfilled <item-id>: <brief rationale>"
   git add .agent/kf/spec/
   git commit -m "chore: fulfill spec item <item-id>"
   ```

   If the assessment fails, report the gaps but do **not** mark as fulfilled.

   **If no items show `READY`** or the track has no `required-for` refs, skip assessment.

   **If no spec exists or no spec_refs**, skip this step entirely.

```bash
git add .agent/kf/tracks/{trackId}/
git commit -m "chore: mark track {trackId} complete"
```

---

## Phase 4: Merge

### Step 9 — Report completion and merge (or wait)

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

### Step 10 — Merge sequence

When the user says "merge" (or immediately if auto-merge is enabled (default)), execute the full merge protocol. The developer performs an **implementation merge** — verification is mandatory.

For the full merge protocol details, see `kf-merge-protocol/SKILL.md`.

#### 10a. Pre-merge verification

Run the full verification suite **before** acquiring the lock to avoid holding it during long test runs:

```bash
# Read verification commands from workflow.yaml (e.g., make test && make build && make lint)
VERIFY_CMD="<commands from workflow.yaml>"
eval "$VERIFY_CMD"
```

If verification fails, do **not** attempt to merge. Fix issues first.

#### 10b. Merge via kf-merge

```bash
VERIFY_CMD="<commands from workflow.yaml>"

~/.kf/bin/kf-merge.py \
  --holder "$(basename $(pwd))" \
  --timeout 300 \
  --verify "$VERIFY_CMD" \
  --reapply "~/.kf/bin/kf-track.py update {trackId} --status completed" \
  --cleanup-branch kf/{type}/{trackId}
```

- `--timeout 300` — wait up to 5 minutes for the lock (auto-merge mode)
- `--verify` — runs verification again post-rebase (primary branch may have introduced changes)
- `--reapply` — re-marks the track as completed if track state conflicts were resolved
- `--cleanup-branch` — deletes the implementation branch after merge

**With `--disable-auto-merge`:** Use `--timeout 0` instead. If lock is held (exit code 2), report and **HALT** — wait for user to say "merge" to retry.

**Exit code 3** means unresolved rebase conflicts — lock is STILL HELD. Resolve the source code conflicts (`git add` the resolved files, `git rebase --continue`), then re-run `kf-merge.py` (acquire is idempotent for the same holder). Only release the lock after merge completes or via explicit abort (`git rebase --abort && kf-merge-lock release`).

#### 10c. Post-merge cleanup

After `kf-merge` succeeds:

```bash
# Return to developer home branch
git checkout {worker-home-branch}

# Clean up any stash branches for this track
for b in $(git branch --list "kf/stash/{trackId}/*" | sed 's/^[* ]*//'); do
  git branch -D "$b"
done
```

Release the worktree claim — the merge has already propagated the completed status to the primary branch:

```bash
~/.kf/bin/kf-claim.py release
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

### Step 10d — Auto-exit (if `--auto-exit` was provided)

If the `--auto-exit` flag was provided, exit the session after completion:

1. Resolve the tmux pane target for this worker:
   ```bash
   # Read from conductor status file
   WORKER_NAME=$(basename $(pwd))
   STATUS_FILE="$(git rev-parse --git-common-dir)/kf-conductor/${WORKER_NAME}.json"
   if [ -f "$STATUS_FILE" ]; then
     TMUX_WINDOW=$(python3 -c "import json; d=json.load(open('$STATUS_FILE')); print(d.get('tmux_window',''))")
     PANE_INDEX=$(python3 -c "import json; d=json.load(open('$STATUS_FILE')); print(d.get('pane_index',''))")
     PANE_TARGET="${TMUX_WINDOW}.${PANE_INDEX}"
   fi
   ```
2. Print a countdown notice so the user knows the session will close:
   ```
   Auto-exit in {N} seconds. Type anything to cancel.
   ```
3. If a delay was specified (e.g., `--auto-exit=30`), wait that many seconds first — this gives the user a window to intervene or review the output
4. **If the user sends any message during the countdown, cancel the auto-exit** — the user wants to continue interacting. Acknowledge with: `Auto-exit cancelled.`
5. If no user input is received during the delay, kill the tmux pane:
   ```bash
   tmux kill-pane -t "${PANE_TARGET}"
   ```

If `--auto-exit` with no value was provided, kill the pane immediately (no delay):
```bash
tmux kill-pane -t "${PANE_TARGET}"
```

If no status file is found (not running under conductor), fall back to just stopping the response.

If `--auto-exit` was **not** provided, remain in the interactive session.

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
| Branch lock held           | Report (`kf-merge-lock status`), wait for other worker |
| Rebase conflict (state files) | Accept theirs, continue rebase, re-apply via CLI     |
| Rebase conflict (source code) | Lock stays held — resolve conflicts, `git add`, `git rebase --continue`, then proceed with merge. Only release lock after merge or explicit abort. |
| Post-rebase verify failure | Release lock, report, offer fix/retry/abort              |
| Merge not fast-forwardable | Release lock, offer re-rebase or abort                   |

---

## Flags Summary

| Flag | Effect |
|------|--------|
| (none) | Default: implement, auto-merge (poll branch lock if held) |
| `--disable-auto-merge` | Pause after implementation; wait for explicit "merge" command |
| `--auto-exit` | Exit the session after completion (default: immediate) |
| `--auto-exit=30` | Wait 30 seconds after completion, then exit |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KF_ORCH_URL` | `http://localhost:4001` | Orchestrator URL for HTTP lock API |

## Branch Lock Modes

The branch lock is managed by the shared `~/.kf/bin/kf-merge-lock.py` helper, which supports dual-mode acquisition:

1. **HTTP mode** — Preferred when kiloforge orchestrator is running. Uses TTL (120s), heartbeat (every 30s), and server-side long-poll for `--auto-merge`. Crash recovery via automatic TTL expiry.
2. **mkdir mode** — Fallback when orchestrator is unreachable. Uses `$(git rev-parse --git-common-dir)/merge.lock` directory. PID-based stale detection with auto-cleanup.

Detection is automatic. Run `kf-merge-lock status` to inspect current lock state.

## Critical Rules

1. **ALWAYS validate before implementing** — never start work on an invalid or claimed track
2. **ALWAYS use `${PRIMARY_BRANCH}` from claim output** — NEVER hardcode `main` or `master`. NEVER read local `.agent/kf/` files directly — always `--ref ${PRIMARY_BRANCH}` or `git show ${PRIMARY_BRANCH}:<path>`
3. **NEVER push to remote** — all branches are local only
4. **Auto-merge is the default** — only pause for explicit "merge" command when `--disable-auto-merge` is provided
5. **ALWAYS verify after rebase** — full verification after rebase, before merge
6. **ALWAYS use --ff-only** — clean fast-forward merges only
7. **ONE merge at a time** — enforce via cross-worktree branch lock (HTTP preferred, mkdir fallback)
8. **HALT on any failure** — do not continue past errors without user input
9. **Follow workflow.yaml** — all TDD, commit, and verification rules apply
10. **Return to home branch** — always checkout back to `worker-*` branch after merge
11. **ALWAYS send heartbeat** — start heartbeat after lock acquire, stop after release
13. **NEVER force-remove another worker's lock** — if the branch lock is held, HALT and wait for user instructions. Do not `rm -rf` the lock directory or force-release HTTP locks held by others.
