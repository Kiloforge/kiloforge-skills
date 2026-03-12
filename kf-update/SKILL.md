---
name: kf-update
description: Update Kiloforge skill definitions and project CLI tools from the skills repo
---

# Kiloforge Update

Update skill definitions in `~/.claude/skills/` and CLI tools in `.agent/kf/bin/` from the skills repo.

## Use this skill when

- You want to update skills and CLI tools to the latest version
- A new skill or tool has been added and you need it
- A bug fix was made and you need the fix

## Do not use this skill when

- The project has no `.agent/kf/` directory (use `/kf-setup` first)
- You need to change project configuration (edit the yaml files directly)

## Instructions

### Step 1 — Verify Kiloforge is initialized

Check that `.agent/kf/bin/` exists:

```bash
ls .agent/kf/bin/*.py
```

If not found, suggest `/kf-setup` instead. **HALT.**

### Step 2 — Pull latest skills repo

```bash
git -C "$SKILL_DIR/.." pull --ff-only
```

If the pull fails (e.g., local changes, detached HEAD), warn but continue:

```
WARNING: Could not pull latest skills repo. Updating from current local version.
```

### Step 3 — Run the install script in update mode

```bash
python3 "$SKILL_DIR/../kf-bin/scripts/kf-install.py" --update --project-dir "$(pwd)"
```

This replaces skill definitions in `~/.claude/skills/`, CLI scripts in `.agent/kf/bin/`, rewrites shebangs, and cleans up legacy scripts.

**If `$SKILL_DIR` is not available**, use `--skills-dir`:

```bash
python3 /path/to/kiloforge-skills/kf-bin/scripts/kf-install.py --update --project-dir "$(pwd)"
```

### Step 4 — Report

Show the output from `kf-install.py` — it reports which skills were added/updated and which scripts were copied.
