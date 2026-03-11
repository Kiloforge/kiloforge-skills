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

### Step 1 — Run the status script

A single command returns everything:

```bash
.agent/kf/bin/kf-status.py
```

This combines in one output:
- **Current Workers** — instant snapshot from worktree claim locks (who is working on what)
- **Overall Progress** — track counts, task counts, progress bar
- **Active Tracks** — table with per-track task completion, deps, and enriched status labels (CLAIMED, AVAILABLE, BLOCKED)
- **Current Focus** — claimed tracks with next pending task
- **Ready to Start** — pending tracks with all dependencies satisfied
- **Conflict Risk** — active conflict pairs (only if any exist)
- **Blocked** — tracks with unmet dependencies
- **Dispatch Recommendations** — prioritized worker assignments (only if worktrees exist)

### Step 2 — Assess and recommend

The script output is factual. After presenting it, add brief **assessment**:

1. **Bottleneck analysis** — If many tracks are blocked on the same dependency, call it out
2. **General recommendations** — Based on the state:
   - No pending tracks → suggest `/kf-architect` to create new work
   - Many completed tracks not archived → suggest `/kf-bulk-archive`
   - In-progress tracks with low progress → note they may be stalled

### Single track detail

For a specific track, use:

```bash
.agent/kf/bin/kf-track.py show {trackId}
.agent/kf/bin/kf-track-content.py progress {trackId}
```

## Error States

### Kiloforge Not Initialized

If `kf-status` fails:

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
