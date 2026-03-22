---
name: kf-data-guardian
description: "Data integrity guard for Kiloforge agents. Reference document defining corruption detection heuristics and response protocol. NOT user-invocable — embedded by other kf-* skills."
---

# Kiloforge Data Integrity Guard

This is a **reference document** for Kiloforge agents. It is NOT a user-invocable skill. All `kf-*` agents (except `kf-repair`) should monitor for the corruption signals listed below during normal operation.

## Purpose

Detect corruption in the Kiloforge data system early — before it cascades into silent data loss, conflicting track states, or broken dependency graphs. When corruption is suspected, the agent must **immediately halt** and direct the user to `/kf-repair`.

## Corruption Signals by Data File

### tracks.yaml (Track Registry)

- **YAML parse error** — file is malformed or truncated
- **Duplicate track IDs** — same ID appears more than once
- **Invalid status values** — status is not one of: `pending`, `in-progress`, `completed`, `archived`
- **Missing required fields** — track entry lacks `title`, `status`, or `type`
- **Orphan entries** — track ID in registry but no corresponding directory under `tracks/`
- **Ghost directories** — directory exists under `tracks/` but no registry entry (and not in `_archive/`)

### deps.yaml (Dependency Graph)

- **YAML parse error** — file is malformed or truncated
- **References to non-existent tracks** — dependency lists a track ID not in `tracks.yaml`
- **Dependency cycles** — circular references (A depends on B depends on A)
- **Completed tracks still listed** — completed/archived tracks not pruned from the graph
- **Missing entries for active tracks** — active track has no entry in deps.yaml (should at least have `[]`)

### conflicts.yaml (Conflict Pairs)

- **YAML parse error** — file is malformed or truncated
- **References to non-existent tracks** — pair references a track ID not in `tracks.yaml`
- **Stale pairs** — both tracks in a pair are completed/archived (should have been cleaned)
- **Malformed pair keys** — pair key doesn't follow `<lower-id>/<higher-id>` alphabetical ordering

### config.yaml (Project Configuration)

- **YAML parse error** — file is malformed or truncated
- **Missing `primary_branch`** — key field absent (not fatal but suspicious if file exists)
- **File missing entirely** — when other kf artifacts exist, config.yaml should too

### compactions.yaml (Recovery Metadata)

- **YAML parse error** — file is malformed or truncated
- **References to non-existent tracks** — compaction entries reference tracks not in registry or git history

### Track Directories (tracks/{trackId}/)

- **Missing track.yaml** — directory exists but has no `track.yaml` file
- **Malformed track.yaml** — YAML parse error in per-track spec
- **Status mismatch** — `track.yaml` says `completed` but `tracks.yaml` says `pending` (or vice versa)
- **ID mismatch** — `track.yaml`'s `id` field doesn't match its directory name

## Detection Triggers

Agents should watch for these signals during normal operation:

1. **CLI errors** — any `kf-track` or `kf-track-content` command that exits non-zero with unexpected error messages (not normal "not found" for valid queries)
2. **YAML parse failures** — when reading any `.agent/kf/` YAML file
3. **Inconsistent state** — when a track lookup returns data that contradicts what was just read from another source
4. **Missing directories** — when `tracks.yaml` lists a track but its directory doesn't exist
5. **Unexpected empty files** — when a YAML file exists but is empty (0 bytes)

## Response Protocol

When an agent detects or suspects corruption:

### 1. HALT immediately

Stop the current operation. Do not attempt to fix the corruption — that is `kf-repair`'s job. Do not continue with stale or inconsistent data.

### 2. Alert the user

Display a clear, prominent warning:

```
================================================================================
    DATA INTEGRITY WARNING — Possible Kiloforge data corruption detected
================================================================================

Signal:   {describe what was detected}
File:     {which file or directory is affected}
Context:  {what operation was being performed}

RECOMMENDED ACTION: Run /kf-repair to diagnose and fix the issue.

This agent has halted to prevent cascading errors.
================================================================================
```

### 3. Do not attempt repairs

- Do NOT manually edit tracks.yaml, deps.yaml, or conflicts.yaml to fix inconsistencies
- Do NOT delete or recreate directories to resolve mismatches
- Do NOT skip the corrupted data and continue — corruption often indicates a deeper issue

### 4. Exceptions

The following are NOT corruption signals — do not alert for these:

- **Track not found** when querying a specific ID — may just be a typo or the track hasn't been created yet
- **Empty track list** — project may genuinely have no tracks
- **deps.yaml missing entirely** — backwards compatibility; older projects may not have it
- **conflicts.yaml missing entirely** — normal if no conflict pairs have been registered
- **kf-track CLI not found** — setup issue, not data corruption

## Scope

This guard applies to ALL `kf-*` skills **except**:

- **kf-repair** — the repair agent IS the corruption fixer; it must be able to read and modify corrupt data without halting

## Quick Reference

For agents embedding this guard, add this compact block to your skill:

```
## Data Integrity Guard

Before operating on Kiloforge data, watch for corruption signals: YAML parse errors,
CLI commands failing unexpectedly, missing track directories, status contradictions,
or orphan/ghost entries. If detected, HALT immediately and alert the user:

  "DATA INTEGRITY WARNING — Run /kf-repair to diagnose and fix."

Do NOT attempt repairs. See ~/.claude/skills/kf-data-guardian/SKILL.md for full heuristics.
```
