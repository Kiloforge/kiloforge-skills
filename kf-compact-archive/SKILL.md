---
name: kf-compact-archive
description: Remove archived and completed track directories from the working tree while preserving recovery via git history with rich metadata tracking
---

# Compact Archive

Remove archived (`_archive/`) and completed track directories from the working tree while preserving access via git history. Records rich metadata about each compaction point for future recovery.

## Use this skill when

- The `_archive/` directory has accumulated track folders and you want to reclaim working tree space
- Completed tracks have accumulated in the index and you want to clean them up
- The user says "compact archive" or "compact"
- After a bulk archive, when the user wants to clean up archived directories

## Do not use this skill when

- Kiloforge is not initialized (use `/kf-setup` first)
- There is no `_archive/` directory AND no entries with status `completed` in the index
- The user wants to archive completed tracks without compacting — use `/kf-bulk-archive` instead

## Instructions

### Step 1: Identify compactable tracks

Scan **both** sources of completed/archived tracks:

1. **Archived tracks**: List all track directories in `.agent/kf/tracks/_archive/` (if the directory exists)
2. **Completed index tracks**: Parse `.agent/kf/tracks.yaml` and find all entries with status `completed` that have a corresponding directory in `.agent/kf/tracks/{trackId}/`

If both sources are empty, report "Nothing to compact" and stop.

### Step 2: Record compaction point

```bash
HASH=$(git rev-parse HEAD)
```

### Step 3: Gather metadata from all compactable tracks

For each track directory — from both `_archive/` and from the main `tracks/` dir (for completed tracks) — read its `track.yaml` to collect:

- **Status**: `completed`, `superseded`, `deprecated`, `dropped`, or other non-complete status
- **Created timestamp**: from `track.yaml` `created` field (or parse track ID datetime suffix as fallback)
- **Completed timestamp**: from `track.yaml` `completedAt` or `updated` field (only for completed tracks)

Compute summary stats across ALL compactable tracks (both sources combined):

| Stat | Description |
| ---- | ----------- |
| `completed_count` | Tracks with status `complete` |
| `uncompleted_count` | Tracks with any other status (superseded, deprecated, dropped, etc.) |
| `first_created` | Earliest created ISO timestamp across all compacted tracks |
| `last_created` | Latest created ISO timestamp across all compacted tracks |
| `first_completed` | Earliest completed ISO timestamp across completed tracks (or `---` if none) |
| `last_completed` | Latest completed ISO timestamp across completed tracks (or `---` if none) |

### Step 4: Update archive-compactions.yaml

Create or append to `.agent/kf/archive-compactions.yaml`.

**If creating the file for the first time**, use this format:

```markdown
# Archive Compaction Points

Archived track data can be recovered by checking out the commit before each compaction point.

## Source: `.agent/kf/tracks.yaml`
## Archive: `.agent/kf/tracks/_archive/`

If the tracks index or archive folder location is ever changed, declare the new paths below
and start a new compaction table under that declaration.

| Commit | Date | Completed | Uncompleted | First Created | Last Created | First Completed | Last Completed |
| ------ | ---- | --------- | ----------- | ------------- | ------------ | --------------- | -------------- |
| {HASH} | {YYYY-MM-DDTHH:MM:SSZ} | {completed_count} | {uncompleted_count} | {first_created} | {last_created} | {first_completed} | {last_completed} |
```

**If the file already exists**, append a new row to the **current** table (the one under the most recent Source/Archive declaration). Do NOT create a new table unless the paths have changed.

**If the Source or Archive paths have changed**, append a new section:

```markdown
## Source: `{new_tracks_yaml_path}`
## Archive: `{new_archive_path}`

| Commit | Date | Completed | Uncompleted | First Created | Last Created | First Completed | Last Completed |
| ------ | ---- | --------- | ----------- | ------------- | ------------ | --------------- | -------------- |
| {HASH} | {YYYY-MM-DDTHH:MM:SSZ} | ... |
```

### Step 5: Delete compacted track directories

Remove directories from **both** sources:

```bash
# Remove archived tracks
rm -rf .agent/kf/tracks/_archive/

# Remove completed track directories from the main tracks dir
rm -rf .agent/kf/tracks/{trackId}/  # for each completed track identified in Step 1
```

**Important:** Only delete directories for tracks that were identified in Step 1. Do NOT delete directories for `pending`, `in-progress`, or `future` tracks.

### Step 6: Clean up tracks.yaml

Update `.agent/kf/tracks.yaml`:

1. **Remove all entries with status `completed`** from the tracks list. Only `pending`, `in-progress`, and `future` entries should remain.
2. **Remove all content under the `archived` section**. This includes all batch archive entries added by `/kf-bulk-archive`. The section key itself can be kept (empty) or removed — either is fine.

Both the completed entries and the archived batch listings are preserved in `archive-compactions.yaml` and recoverable via git history, so keeping them in `tracks.yaml` would just be orphaned references to directories that no longer exist.

### Step 7: Commit

```bash
git add .agent/kf/tracks/ .agent/kf/tracks.yaml .agent/kf/archive-compactions.yaml
git commit -m "chore: compact archive ({completed_count} completed, {uncompleted_count} uncompleted — recover from {HASH})"
```

### Step 8: Report

```
================================================================================
                     COMPACT ARCHIVE COMPLETE
================================================================================
Commit before compaction:  {HASH}
Tracks removed:            {total_count}
  From _archive/:          {archive_count}
  From index (completed):  {index_completed_count}
  Completed:               {completed_count}
  Uncompleted:             {uncompleted_count}
Date range (created):      {first_created} — {last_created}
Date range (completed):    {first_completed} — {last_completed}

Recovery commands:
  git show {HASH}:.agent/kf/tracks.yaml
  git ls-tree {HASH} .agent/kf/tracks/_archive/
  git ls-tree {HASH} .agent/kf/tracks/
  git show {HASH}:.agent/kf/tracks/{trackId}/track.yaml
================================================================================
```

## Recovery Reference

To recover compacted tracks, use the commit hash from the compactions table:

```bash
# Recover the full tracks.yaml index at that point (includes completed entries)
git show {HASH}:.agent/kf/tracks.yaml

# List all archived tracks at that point
git ls-tree {HASH} .agent/kf/tracks/_archive/

# List all track directories (including completed ones) at that point
git ls-tree {HASH} .agent/kf/tracks/

# Recover a specific track's files
git show {HASH}:.agent/kf/tracks/{trackId}/track.yaml
git show {HASH}:.agent/kf/tracks/_archive/{trackId}/track.yaml
```
