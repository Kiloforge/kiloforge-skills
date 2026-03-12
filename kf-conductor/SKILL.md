---
name: kf-conductor
description: Tmux-based multi-agent orchestration — persistent manager loop that auto-dispatches, monitors, and manages parallel Claude Code workers
metadata:
  argument-hint: "[start | stop | suspend | resume | status | dispatch | spawn <worker> <track> | kill <worker> | cleanup]"
---

# Kiloforge Conductor

Orchestrate parallel Claude Code worker agents using tmux. A persistent manager loop automatically dispatches work to idle workers, cleans up completed ones, and respects `max_workers` concurrency limits. Each worker runs in its own git worktree, implements a kf track autonomously, and self-terminates when done.

## Use this skill when

- You want to start the manager loop to automatically process the track queue
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

### Quick Start — Manager Loop (Recommended)

Start the persistent manager in a dedicated tmux window. It automatically dispatches work, cleans up completed workers, and loops until stopped:

```bash
# In a dedicated tmux window:
.agent/kf/bin/kf-conductor.py start --timeout 30
```

The manager:
1. Polls every 5 seconds
2. Auto-cleans completed/failed workers (releases claims, resets worktrees)
3. Runs `kf-dispatch` to find eligible work and spawns workers for it
4. Respects `max_workers` from `config.yaml` (default: 4)
5. Logs status every ~60 seconds

Control the manager from another tmux window:

```bash
.agent/kf/bin/kf-conductor.py suspend   # Pause dispatching (running workers continue)
.agent/kf/bin/kf-conductor.py resume    # Resume dispatching
.agent/kf/bin/kf-conductor.py stop      # Graceful shutdown (finish current, no new)
```

### One-Shot Dispatch

For a single dispatch cycle without the persistent loop:

```bash
.agent/kf/bin/kf-conductor.py dispatch --timeout 30
```

This runs `kf-dispatch` to compute assignments, spawns workers, then exits.

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

Shows manager state (running/suspended/stopping/stopped) and all conductor-managed workers with state (running/completed/failed/timeout/killed) and elapsed time.

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

**Note:** When the manager loop is running, cleanup happens automatically — you don't need to run it manually.

## Full Dispatch Cycle

### Option A — Automated (Manager Loop)

Best for processing an entire track queue hands-off:

1. Verify inside tmux: `echo $TMUX`
2. Run pre-flight: `.agent/kf/bin/kf-preflight.py`
3. Start the manager: `.agent/kf/bin/kf-conductor.py start --timeout 30`
4. Monitor from another window: `.agent/kf/bin/kf-conductor.py status`
5. The manager handles dispatch, cleanup, and re-dispatch automatically
6. When done: `.agent/kf/bin/kf-conductor.py stop` or Ctrl+C

### Option B — Manual (Lead Agent)

Best when you want fine-grained control over each dispatch cycle:

#### Phase 1 — Pre-flight

1. Verify inside tmux: `echo $TMUX`
2. Run pre-flight: `.agent/kf/bin/kf-preflight.py`
3. Check current state: `.agent/kf/bin/kf-conductor.py status`

#### Phase 2 — Dispatch

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

#### Phase 3 — Monitor

Poll status periodically:

```bash
.agent/kf/bin/kf-conductor.py status
```

Or watch a specific worker's output:

```bash
tmux capture-pane -t developer-1 -p | tail -20
```

#### Phase 4 — Handle Results

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

#### Phase 5 — Repeat

After cleanup and re-dispatch, loop back to Phase 3 until all tracks are done.

## Manager States

| State | Meaning | Transition |
|-------|---------|------------|
| `running` | Actively dispatching and cleaning up | `suspend` → suspended, `stop` → stopping |
| `suspended` | No new dispatches; running workers continue | `resume` → running, `stop` → stopping |
| `stopping` | Waiting for running workers to finish, then exits | Automatic when all workers done |
| `stopped` | Manager exited | `start` to restart |

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

## Concurrency

`max_workers` is read from `.agent/kf/config.yaml` (default: 4). Override at runtime:

```bash
.agent/kf/bin/kf-conductor.py start --max-workers 6 --timeout 30
```

Or set it permanently:

```bash
.agent/kf/bin/kf-track.py config set max_workers 6
```

## Status File Location

Worker status files are stored at:

```
$(git rev-parse --git-common-dir)/kf-conductor/<worker-name>.json
```

Manager state is stored at:

```
$(git rev-parse --git-common-dir)/kf-conductor/_manager.json
```

Both are shared across all worktrees in the repo.

## Safety Considerations

- **Workers run with `--dangerously-skip-permissions`** — they have full tool access without human approval. This is necessary for autonomous operation but means workers can run arbitrary commands.
- **Timeout** — Always set a timeout (default: 30 minutes). Workers that exceed the timeout are killed.
- **Merge lock** — Multiple workers completing simultaneously will queue on the branch lock. This is handled by the kf-developer workflow.
- **One track per worker** — Each worktree can only work on one track at a time (enforced by claims).
- **Manager is single-instance** — Only one manager can run at a time (enforced by PID check).

## Error Handling

| State | Meaning | Action |
|-------|---------|--------|
| `completed` | Worker finished successfully | Auto-cleaned by manager, or `cleanup --completed` |
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
