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

### Step 2 — Fetch latest skills repo

Clone the skills repo to a temporary directory:

```bash
KF_TMPDIR=$(mktemp -d)
git clone --depth 1 https://github.com/Kiloforge/kiloforge-skills.git "$KF_TMPDIR/kiloforge-skills"
```

If the clone fails, **HALT** — the update cannot proceed without the latest source.

### Step 3 — Run the install script in update mode

```bash
python3 "$KF_TMPDIR/kiloforge-skills/kf-bin/scripts/kf-install.py" --update --project-dir "$(pwd)"
```

This replaces skill definitions in `~/.claude/skills/`, CLI scripts in `.agent/kf/bin/`, updates `.gitignore`, and cleans up legacy scripts.

### Step 3b — Clean up

```bash
rm -rf "$KF_TMPDIR"
```

### Step 4 — Commit and merge to primary branch

The updated scripts and `.gitignore` must be committed and merged to the primary branch so all worktrees see them.

```bash
git add .agent/kf/bin/ .agent/kf/.gitignore
git diff --cached --quiet || git commit -m "chore(kf): update kiloforge CLI tools and config"
```

If running from a worktree (not the primary branch), merge using the standard protocol:

```bash
CURRENT_BRANCH=$(git branch --show-current)
PRIMARY_BRANCH=$(.agent/kf/bin/kf-primary-branch.py 2>/dev/null || echo "main")
if [ "$CURRENT_BRANCH" != "$PRIMARY_BRANCH" ]; then
  .agent/kf/bin/kf-merge.py --holder "$(basename $(pwd))" --timeout 0
fi
```

This is a metadata-only merge (no `--verify` needed). If exit code 2 (lock held), report and retry. If exit code 3 (conflicts), resolve while locked and re-run.

### Step 5 — Report

Show the output from `kf-install.py` — it reports which skills were added/updated and which scripts were copied.
