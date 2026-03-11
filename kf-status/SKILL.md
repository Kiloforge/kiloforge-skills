---
name: kf-status
description: Display project status, active tracks, and next actions
metadata:
  argument-hint: "[--ref <branch>]"
---

# Kiloforge Status

Display the current status of the Kiloforge project, including overall progress, active tracks, and next actions.

## Use this skill when

- User asks for project status, progress, or overview
- You need to see what tracks are active, pending, or ready to start

## Do not use this skill when

- You need to create or modify tracks (use `/kf-architect` or `/kf-developer`)
- The project has no Kiloforge artifacts (use `/kf-setup` first)

## Instructions

### Step 1 — Run pre-flight check

```bash
eval "$(.agent/kf/bin/kf-preflight.py)"
```

This verifies all required metadata files exist on the primary branch and sets `PRIMARY_BRANCH`. If it fails, it prints an error suggesting `/kf-setup` — **HALT.**

### Step 2 — Show current workers (instant)

Query worktree claim locks for an instant snapshot of who is working on what:

```bash
.agent/kf/bin/kf-claim.py list
```

This reads filesystem-based claim locks (no git or network operations) and outputs a table:

```
WORKTREE             TRACK                                              HOLDER               STARTED
worker-1             auth_20250115100000Z                               worker-1             2025-01-15T10:30:00Z
worker-2             search_20250115100001Z                             worker-2             2025-01-15T10:31:00Z

2 active claim(s)
```

If no claims are active, it prints `(no active claims)` — skip this section in the output.

Display this as a **Current Workers** section at the top of the status report, before the track summary. This gives an immediate picture of parallel activity without waiting for heavier git operations.

### Step 3 — Run the status command

The `kf-track status` command generates the full factual status report:

```bash
.agent/kf/bin/kf-track.py status --ref ${PRIMARY_BRANCH}
```

This outputs all **data** sections:
- **Overall Progress** — track counts, task counts, progress bar
- **Active Tracks** — table with per-track task completion, deps count, and enriched status labels:
  - `CLAIMED` — in-progress tracks (actively being worked by a developer)
  - `AVAILABLE` — pending tracks with all dependencies satisfied (ready to claim)
  - `BLOCKED` — pending tracks with unmet dependencies
  - **Deps column** — shows `N/M met` or `no deps` per track
- **Current Focus** — claimed tracks with next pending task
- **Ready to Start** — pending tracks with all dependencies satisfied
- **Conflict Risk** — active conflict pairs from `conflicts.yaml`, showing risk level and notes (only appears when active pairs exist)
- **Blocked** — pending tracks with unmet dependencies and their current statuses

### Step 4 — Show dispatch recommendations

If developer worktrees exist, run the dispatch script to show prioritized assignments for idle workers:

```bash
.agent/kf/bin/kf-dispatch.py --ref ${PRIMARY_BRANCH}
```

This automatically scans worktrees, computes priority scores (unblock factor, conflict avoidance, type diversity), and matches idle workers to available tracks. It limits recommendations to the number of idle worktrees.

If no worktrees exist, skip this step.

### Step 5 — Assess and recommend

The CLI outputs are factual. After presenting them, add brief **assessment**:

1. **Bottleneck analysis** — If many tracks are blocked on the same dependency, call it out
2. **Recommendations** — Based on the state:
   - No pending tracks → suggest `/kf-architect` to create new work
   - Many completed tracks not archived → suggest `/kf-bulk-archive`
   - In-progress tracks with low progress → note they may be stalled

### Single track detail

For a specific track, use:

```bash
.agent/kf/bin/kf-track.py show {trackId} --ref ${PRIMARY_BRANCH}
.agent/kf/bin/kf-track.py progress {trackId} --ref ${PRIMARY_BRANCH}
```

## Error States

### Kiloforge Not Initialized

If `kf-track status` fails with "No tracks.yaml found":

```
ERROR: Kiloforge not initialized.
Run /kf-setup to initialize Kiloforge for this project.
```

### No Tracks

If the output shows 0 total tracks:

```
Kiloforge is set up but no tracks have been created yet.
Run /kf-architect to create tracks from a feature request.
```
