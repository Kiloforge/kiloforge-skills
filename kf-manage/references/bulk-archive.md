# Bulk Archive

Move all completed track directories into `_archive/` and update `tracks.yaml` in a single operation.

## Use this skill when

- All (or many) active tracks have status `completed` and need archiving
- The user says "bulk archive" or "archive completed tracks"
- Cleaning up the active tracks registry after a round of parallel work

## Do not use this skill when

- Kiloforge is not initialized (use `/kf-setup` first)
- There are no completed tracks to archive
- The user wants to compact (delete) archived directories — use `/kf-compact-archive` instead

## Instructions

### Pre-flight

Run pre-flight check:
```bash
eval "$(~/.kf/bin/kf-preflight.py)"
```
This verifies all required metadata files exist on the primary branch and sets `PRIMARY_BRANCH`. If it fails, it prints an error suggesting `/kf-setup` — **HALT.**

### Step 1: Identify completed tracks

Read `.agent/kf/tracks.yaml` and find all entries with status `completed`.

If none are found, report "No completed tracks to archive" and stop.

### Step 2: Move directories

```bash
mkdir -p .agent/kf/tracks/_archive/
```

For each completed track:
```bash
mv .agent/kf/tracks/{trackId}/ .agent/kf/tracks/_archive/{trackId}/
```

### Step 3: Update tracks.yaml

1. Update all `completed` entries to status `archived`, or remove them from the active entries list
2. Add a new batch archive entry under the `archived` section:

```yaml
archived:
  - batch: "{YYYY-MM-DDTHH:MM:SSZ}"
    count: {count}
    note: "All active tracks completed and archived at {YYYY-MM-DDTHH:MM:SSZ}."
    tracks:
      - id: "{trackId}"
        title: "{title}"
        reason: "Completed"
      # ...
```

Place the new batch entry **above** any previous archive batches so the most recent is first.

### Step 4: Commit

```bash
git add .agent/kf/tracks.yaml .agent/kf/tracks/
git commit -m "chore: bulk archive {count} completed tracks"
```

### Step 5: Merge to primary branch

The archived state must be merged to the primary branch so all worktrees see it. Use the standard metadata merge protocol:

```bash
~/.kf/bin/kf-merge.py --holder "$(basename $(pwd))" --timeout 0
```

This is a metadata-only merge (no `--verify` needed). If exit code 2 (lock held), report and retry. If exit code 3 (conflicts), resolve while locked and re-run.

If already on the primary branch (not in a worktree), the commit from Step 4 is sufficient — skip the merge.

### Step 6: Report

```
================================================================================
                      BULK ARCHIVE COMPLETE
================================================================================
Tracks archived:  {count}
Commit:           {hash}
Active tracks:    {remaining} remaining

Want to compact the archive? Run /kf-compact-archive
================================================================================
```
