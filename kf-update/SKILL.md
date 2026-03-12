---
name: kf-update
description: Check for and apply Kiloforge updates — pulls latest skills repo, updates skill definitions and project CLI tools
metadata:
  argument-hint: "[--check]"
---

# Kiloforge Update

Check for new versions and update everything: pull the skills repo, update skill definitions in `~/.claude/skills/`, and update project-embedded CLI tools in `.agent/kf/bin/`.

## Use this skill when

- You want to check if updates are available
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

### Step 2 — Resolve the skills repo path

The skills repo path is needed for both version checking and updating. Determine it from one of:

1. `$SKILL_DIR/../kf-bin/scripts/kf-install.py` — if `$SKILL_DIR` is available
2. The `.agent/kf/.version` file — contains `skills_dir` from last install
3. Ask the user for the path

### Step 3 — Check for updates

```bash
python3 "{skills_repo}/kf-bin/scripts/kf-install.py" --check --project-dir "$(pwd)"
```

Exit codes:
- `0` — update available
- `2` — already up to date

If already up to date and the user did not pass `--check`, report and **STOP**:

```
Kiloforge is already up to date.
```

If the user passed `--check`, just report the version info and **STOP** (don't apply updates).

### Step 4 — Pull latest skills repo

Update the skills repo itself to get the newest skill definitions and scripts:

```bash
git -C "{skills_repo}" pull --ff-only
```

If the pull fails (e.g., local changes, detached HEAD), warn:

```
WARNING: Could not update skills repo at {skills_repo}.
Proceeding with update from current local version.
```

### Step 5 — Update project and skills

```bash
python3 "{skills_repo}/kf-bin/scripts/kf-install.py" --update --project-dir "$(pwd)"
```

This single command updates everything:
- **Skill definitions** — copies SKILL.md files to `~/.claude/skills/` (new and changed skills)
- **CLI scripts** — copies latest scripts to `.agent/kf/bin/`
- **Shebangs** — rewrites to use project-local venv
- **Legacy cleanup** — removes old non-.py scripts
- **Version stamp** — records installed version in `.agent/kf/.version`

### Step 6 — Report

```
================================================================================
                    KILOFORGE UPDATE COMPLETE
================================================================================

Skills repo:   {skills_repo}
Previous:      {old_version_short} ({old_date})
Updated to:    {new_version_short} ({new_date})
               {commit_subject}

Skills:        {N} updated, {N} added
CLI tools:     Updated in .agent/kf/bin/
Metadata:      Not modified
================================================================================
```
