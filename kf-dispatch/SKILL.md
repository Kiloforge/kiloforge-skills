---
name: kf-dispatch
description: AI swarm dispatcher — analyzes project state and produces prescriptive worker assignments for idle developer agents.
metadata:
  argument-hint: "[--dry-run] [--limit N] [--json]"
---

# Kiloforge Dispatch

Swarm dispatcher that reviews project state (worktrees, active tracks, dependencies, conflict risks) and produces prescriptive worker assignments.

## Use this skill when

- You have idle developer worktrees and need to assign tracks
- You want to know what to work on next across the swarm
- The Kiloforger needs a dispatch plan for available workers

## Do not use this skill when

- You need to create new tracks (use `/kf-architect` instead)
- You want informational status only (use `/kf-status` instead)
- You are a developer worker ready to implement (use `/kf-developer <track-id>` instead)
- The project has no Kiloforge artifacts (use `/kf-setup` first)

---

## Instructions

### Step 1 — Run pre-flight check

```bash
eval "$(.agent/kf/bin/kf-preflight)"
```

If it fails, suggest `/kf-setup` — **HALT.**

### Step 2 — Run the dispatch script

```bash
.agent/kf/bin/kf-dispatch --ref ${PRIMARY_BRANCH}
```

The script automatically:
- Scans all `worker-*` and `developer-*` worktrees and classifies them (idle/active)
- Reads the track registry, dependency graph, and conflict pairs
- Computes priority scores for available tracks:
  - **Unblock factor (3x):** tracks that unblock more blocked tracks score higher
  - **Conflict penalty (-2x):** tracks conflicting with claimed tracks are penalized
  - **Type diversity (+1):** prefer a mix of track types across workers
  - **Task count tiebreaker:** smaller tracks preferred to unblock the pipeline faster
- Matches idle workers to highest-priority tracks (skipping conflict pairs)
- Outputs the formatted dispatch plan

Options:
- `--dry-run` — add a notice that no actions were taken
- `--limit N` — max number of assignments to output
- `--json` — output as JSON for programmatic use

### Step 3 — Present the plan

Display the script output directly. If `--dry-run` was not used, present the commands and ask the user to confirm before executing.

### Step 4 — Handle edge cases

The script handles these automatically and outputs the appropriate message:

- **No tracks exist** — suggests `/kf-architect`
- **No available tracks** — lists blocked tracks and what they're waiting on
- **No idle workers** — lists active workers and suggests adding capacity
- **No developer worktrees** — suggests creating worktrees
