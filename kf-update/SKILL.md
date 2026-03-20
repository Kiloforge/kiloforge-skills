---
name: kf-update
description: Update Kiloforge skill definitions and project CLI tools from the latest release
---

# Kiloforge Update

Update skill definitions in `~/.claude/skills/` and CLI tools in `.agent/kf/bin/` from the latest GitHub release.

## Use this skill when

- You want to update skills and CLI tools to the latest release
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

### Step 2 — Check current and latest versions

Check the installed version:

```bash
CURRENT_VERSION=""
if [ -f .agent/kf/VERSION ]; then
  CURRENT_VERSION=$(cat .agent/kf/VERSION)
fi
echo "Installed: ${CURRENT_VERSION:-unknown}"
```

Check the latest release version from GitHub:

```bash
LATEST_TAG=$(gh release view --repo Kiloforge/kiloforge-skills --json tagName -q '.tagName' 2>/dev/null)
if [ -z "$LATEST_TAG" ]; then
  echo "ERROR: Could not fetch latest release. Check network and gh auth."
  # HALT
fi
LATEST_VERSION="${LATEST_TAG#v}"
echo "Latest release: $LATEST_VERSION ($LATEST_TAG)"
```

If `CURRENT_VERSION` equals `LATEST_VERSION`, report that skills are already up to date and **HALT** (unless the user explicitly wants to force update).

### Step 3 — Fetch the latest release

Clone the release tag to a temporary directory:

```bash
KF_TMPDIR=$(mktemp -d)
git clone --depth 1 --branch "$LATEST_TAG" \
  https://github.com/Kiloforge/kiloforge-skills.git \
  "$KF_TMPDIR/kiloforge-skills"
```

If the clone fails, **HALT** — the update cannot proceed without the release source.

### Step 4 — Run the install script in update mode

```bash
python3 "$KF_TMPDIR/kiloforge-skills/kf-bin/scripts/kf-install.py" --update --project-dir "$(pwd)"
```

This replaces skill definitions in `~/.claude/skills/`, CLI scripts in `.agent/kf/bin/`, updates `.gitignore`, and cleans up legacy scripts.

### Step 4b — Record installed version

```bash
echo "$LATEST_VERSION" > .agent/kf/VERSION
```

### Step 4c — Clean up

```bash
rm -rf "$KF_TMPDIR"
```

### Step 5 — Commit and merge to primary branch

The updated scripts, `.gitignore`, and `VERSION` must be committed and merged to the primary branch so all worktrees see them.

```bash
git add .agent/kf/bin/ .agent/kf/.gitignore .agent/kf/VERSION
git diff --cached --quiet || git commit -m "chore(kf): update kiloforge to $LATEST_TAG"
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

### Step 6 — Report

Show the output from `kf-install.py` — it reports which skills were added/updated and which scripts were copied.

Report the version change:

```
Updated: ${CURRENT_VERSION:-unknown} → $LATEST_VERSION ($LATEST_TAG)
```
