---
name: kf-manage
description: "Manage track lifecycle: archive, bulk-archive, compact, restore, delete, rename, and cleanup"
metadata:
  argument-hint: "<archive|bulk-archive|compact|restore|delete|rename|cleanup> [track-id]"
---

# Track Manager

Manage the complete track lifecycle including archiving, bulk-archiving, compacting, restoring, deleting, renaming, and cleaning up orphaned artifacts.

## Use this skill when

- Archiving individual or all completed tracks
- Compacting archived track directories to reclaim space
- Restoring, renaming, or deleting tracks
- Cleaning up orphaned artifacts

## Do not use this skill when

- The project has no Kiloforge artifacts (use `/kf-setup` first)
- You need to create tracks (use `/kf-architect`)
- You need to implement tracks (use `/kf-developer`)

## Pre-flight

```bash
eval "$(~/.kf/bin/kf-preflight.py)"
```

## Operations

### archive <track-id> [reason]

Archive a single track:
```bash
~/.kf/bin/kf-track.py archive {trackId} [reason]
```

### bulk-archive

Archive all completed tracks at once. See `references/bulk-archive.md` for the full workflow:
1. Identify completed tracks via `kf-track list --status completed`
2. Archive each with `kf-track archive`
3. Commit and merge to primary branch

### compact

Remove archived/completed track directories while preserving recovery via git history. See `references/compact-archive.md` for the full workflow:
```bash
~/.kf/bin/kf-track.py compact run [--dry-run]
~/.kf/bin/kf-track.py compact list
~/.kf/bin/kf-track.py compact recover <name>
```

### restore <track-id>

Restore a track from archive or compaction:
- From `_archive/`: move directory back to `tracks/`
- From compaction: use `kf-track compact recover <name>` to extract, then copy back

### delete <track-id>

Remove a track entirely. Confirm with user before proceeding:
```bash
~/.kf/bin/kf-track.py archive {trackId} "deleted"
rm -rf .agent/kf/tracks/{trackId}/
```

### rename <old-id> <new-id>

Rename a track (move directory, update registry).

### cleanup

Find and report orphaned artifacts:
- Track directories without registry entries
- Registry entries without directories
- Stale conflict pairs
- Completed tracks not yet archived

## Safety

- Confirm destructive actions (delete/cleanup) before applying
- Use `--dry-run` for compact operations when available
- Always commit after state changes
