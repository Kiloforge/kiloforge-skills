---
name: kf-conductor
description: Tmux-based multi-agent orchestration — spawn, monitor, and manage parallel Claude Code workers
metadata:
  argument-hint: "[dispatch | status | spawn <worker> <track> | kill <worker> | cleanup]"
---

# Kiloforge Conductor

Orchestrate parallel Claude Code worker agents using tmux. Each worker runs in its own git worktree, implements a kf track autonomously, and self-terminates when done.

## Use this skill when

- You want to dispatch multiple developer agents in parallel
- You need to monitor running workers
- You want to spawn a single worker for a specific track

## Do not use this skill when

- Not running inside a tmux session
- No worktrees exist (create them first)
- The project is not initialized with Kiloforge

## Prerequisites

- **tmux**: Must be running inside a tmux session
- **claude CLI**: Must be available on PATH
- **Git worktrees**: Worker worktrees (e.g., `developer-1`, `developer-2`) must exist
- **Kiloforge initialized**: `.agent/kf/` must be set up

## Instructions

### Quick Start — Auto-dispatch

For the common case (dispatch all available work to idle workers):

```bash
.agent/kf/bin/kf-conductor.py dispatch --timeout 30
```

This runs `kf-dispatch` to compute assignments, then spawns a claude worker in a tmux window for each assignment. Done.

### Manual Operations

#### Spawn a Single Worker

```bash
.agent/kf/bin/kf-conductor.py spawn <worker-name> <track-id> --timeout 30
```

- `worker-name` must match an existing worktree (e.g., `developer-1`)
- The track must not be claimed by another worker
- Creates a tmux window named after the worker
- Worker runs `claude -p "/kf-developer <track-id>"` autonomously

#### Check Status

```bash
.agent/kf/bin/kf-conductor.py status
```

Shows all conductor-managed workers with state (running/completed/failed/timeout/killed) and elapsed time.

```bash
.agent/kf/bin/kf-conductor.py status --json
```

JSON output for programmatic use.

#### Kill a Worker

```bash
.agent/kf/bin/kf-conductor.py kill <worker-name>
```

Kills the tmux window, releases the track claim, updates status to `killed`.

#### Clean Up Finished Workers

```bash
.agent/kf/bin/kf-conductor.py cleanup --completed   # clean successful workers
.agent/kf/bin/kf-conductor.py cleanup --failed       # clean failed/timed-out workers
.agent/kf/bin/kf-conductor.py cleanup --all          # clean everything (kills running workers)
```

Cleanup resets worktrees to their home branch, releases claims, and removes status files.

## Full Dispatch Cycle

As the lead agent, follow this cycle:

### Phase 1 — Pre-flight

1. Verify inside tmux: `echo $TMUX`
2. Run pre-flight: `.agent/kf/bin/kf-preflight.py`
3. Check current state: `.agent/kf/bin/kf-conductor.py status`

### Phase 2 — Dispatch

```bash
.agent/kf/bin/kf-conductor.py dispatch --timeout 30
```

Or for more control, run dispatch manually:

```bash
# See what would be assigned
.agent/kf/bin/kf-dispatch.py

# Spawn workers individually
.agent/kf/bin/kf-conductor.py spawn developer-1 track_20260312T000000Z --timeout 30
.agent/kf/bin/kf-conductor.py spawn developer-2 track_20260312T000001Z --timeout 30
```

### Phase 3 — Monitor

Poll status periodically:

```bash
.agent/kf/bin/kf-conductor.py status
```

Or watch a specific worker's output:

```bash
tmux capture-pane -t developer-1 -p | tail -20
```

### Phase 4 — Handle Results

After workers finish:

```bash
# See results
.agent/kf/bin/kf-conductor.py status

# Clean up completed workers
.agent/kf/bin/kf-conductor.py cleanup --completed

# Handle failures — check what went wrong
tmux capture-pane -t developer-3 -p | tail -50

# Re-dispatch if new tracks are unblocked
.agent/kf/bin/kf-conductor.py dispatch --timeout 30
```

### Phase 5 — Repeat

After cleanup and re-dispatch, loop back to Phase 3 until all tracks are done.

## Worker Behavior

Each spawned worker runs:

```
claude -p "/kf-developer <track-id>" --dangerously-skip-permissions
```

The worker:
1. Claims the track via `kf-claim`
2. Creates a feature branch
3. Implements the track following the spec and plan
4. Runs tests and verification
5. Merges to the primary branch (acquires branch lock)
6. Releases the claim
7. Exits (tmux window closes)

## Status File Location

Worker status files are stored at:

```
$(git rev-parse --git-common-dir)/kf-conductor/<worker-name>.json
```

This is shared across all worktrees in the repo.

## Safety Considerations

- **Workers run with `--dangerously-skip-permissions`** — they have full tool access without human approval. This is necessary for autonomous operation but means workers can run arbitrary commands.
- **Timeout** — Always set a timeout (default: 30 minutes). Workers that exceed the timeout are killed.
- **Merge lock** — Multiple workers completing simultaneously will queue on the branch lock. This is handled by the kf-developer workflow.
- **One track per worker** — Each worktree can only work on one track at a time (enforced by claims).

## Error Handling

| State | Meaning | Action |
|-------|---------|--------|
| `completed` | Worker finished successfully | `cleanup --completed` |
| `failed` | Worker exited with error | Check output, fix issue, re-dispatch |
| `timeout` | Worker exceeded time limit | Kill, check if track needs splitting |
| `killed` | Manually killed | Re-dispatch if needed |

## Creating Worktrees

If you need more workers:

```bash
# From the main worktree
git worktree add ../developer-3 -b developer-3
git worktree add ../developer-4 -b developer-4
```

Worktree folder names must start with `worker-` or `developer-` to be recognized by dispatch.
