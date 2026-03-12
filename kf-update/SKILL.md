---
name: kf-update
description: Check for and apply Kiloforge updates — pulls latest skills repo and updates project CLI tools
metadata:
  argument-hint: "[--check]"
---

# Kiloforge Update

Check for new versions and update both the global skills repo and project-embedded CLI tools.

## Use this skill when

- You want to check if updates are available
- You want to update CLI tools to the latest version
- A new tool has been added and you need it in the project
- A bug fix was made to a CLI tool and you need the fix

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

### Step 5 — Update project CLI tools

```bash
python3 "{skills_repo}/kf-bin/scripts/kf-install.py" --update --project-dir "$(pwd)"
```

This copies the latest scripts and `lib/` from the skills repo into `.agent/kf/bin/`, rewrites shebangs to use the project-local venv, cleans up legacy scripts, and stamps the new version.

### Step 6 — Report

```
================================================================================
                    KILOFORGE UPDATE COMPLETE
================================================================================

Skills repo:   {skills_repo}
Previous:      {old_version_short} ({old_date})
Updated to:    {new_version_short} ({new_date})
               {commit_subject}

Updated CLI tools in .agent/kf/bin/ to latest version.
Project metadata files were not modified.
================================================================================
```
