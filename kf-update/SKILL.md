---
name: kf-update
description: Update Kiloforge CLI tools in the current project to the latest version from the skills repo
---

# Kiloforge Update

Update the CLI tools in `.agent/kf/bin/` to the latest version from the kiloforge-skills repo. Does not modify project metadata files.

## Use this skill when

- You want to update the CLI tools to the latest version
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

### Step 2 — Run the install script in update mode

```bash
python3 "$SKILL_DIR/../kf-bin/scripts/kf-install.py" --update --project-dir "$(pwd)"
```

This copies the latest scripts and `lib/` from the skills repo into `.agent/kf/bin/`, rewrites shebangs to use the project-local venv, and cleans up any legacy non-.py scripts.

The `$SKILL_DIR` variable resolves to the directory containing this skill's `SKILL.md`. The `kf-bin/scripts/` directory is at the skills repo root, inside `kf-bin`.

**If `$SKILL_DIR` is not available**, use `--skills-dir` to point at the kiloforge-skills repo:

```bash
python3 /path/to/kiloforge-skills/kf-bin/scripts/kf-install.py --update --project-dir "$(pwd)"
```

### Step 3 — Report

```
================================================================================
                    KILOFORGE UPDATE COMPLETE
================================================================================

Updated CLI tools in .agent/kf/bin/ to latest version.

{output from kf-install.py}

Project metadata files were not modified.
================================================================================
```
