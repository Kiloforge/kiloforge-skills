---
name: kf-validate
description: Validates Kiloforge project artifacts for completeness,
  consistency, and correctness. Use after setup, when diagnosing issues, or
  before implementation to verify project context.
allowed-tools: Read Glob Grep Bash
metadata:
  model: opus
  color: cyan
---

# Kiloforge Validate

Validates the structure, completeness, and consistency of all Kiloforge project artifacts. Checks directory layout, required files, track registry integrity, YAML validity, dependency/conflict consistency, and pattern correctness.

## Use this skill when

- After running `/kf-setup` to confirm initialization succeeded
- Before starting implementation to verify project context is intact
- When diagnosing unexpected behavior in track commands or workflows
- After manual edits to Kiloforge YAML files
- As a lightweight pre-flight check (use `/kf-repair` for deeper audit with auto-fix)

## Do not use this skill when

- You need to fix issues found during validation (use `/kf-repair`)
- You need to create or modify tracks (use `/kf-architect` or `/kf-developer`)
- The project has no Kiloforge artifacts yet (use `/kf-setup` first)
- You need a full system health audit with repairs (use `/kf-repair --fix`)

---

## Instructions

### Step 1 — Resolve primary branch

```bash
PRIMARY_BRANCH=$(.agent/kf/bin/kf-primary-branch)
echo "Primary branch: $PRIMARY_BRANCH"
```

### Step 2 — Validate directory structure

Check that the Kiloforge directory layout exists:

```bash
# Core directory
git show ${PRIMARY_BRANCH}:.agent/kf/ > /dev/null 2>&1 && echo "PASS: .agent/kf/ exists" || echo "FAIL: .agent/kf/ missing"

# Tracks directory
git show ${PRIMARY_BRANCH}:.agent/kf/tracks/ > /dev/null 2>&1 && echo "PASS: .agent/kf/tracks/ exists" || echo "FAIL: .agent/kf/tracks/ missing"

# CLI tools
git show ${PRIMARY_BRANCH}:.agent/kf/bin/kf-primary-branch > /dev/null 2>&1 && echo "PASS: kf-primary-branch exists" || echo "FAIL: kf-primary-branch missing"
git show ${PRIMARY_BRANCH}:.agent/kf/bin/kf-track > /dev/null 2>&1 && echo "PASS: kf-track exists" || echo "FAIL: kf-track missing"
git show ${PRIMARY_BRANCH}:.agent/kf/bin/kf-track-content > /dev/null 2>&1 && echo "PASS: kf-track-content exists" || echo "FAIL: kf-track-content missing"
git show ${PRIMARY_BRANCH}:.agent/kf/bin/kf-merge-lock > /dev/null 2>&1 && echo "PASS: kf-merge-lock exists" || echo "FAIL: kf-merge-lock missing"
git show ${PRIMARY_BRANCH}:.agent/kf/bin/kf-worktree-env > /dev/null 2>&1 && echo "PASS: kf-worktree-env exists" || echo "FAIL: kf-worktree-env missing"
```

If `.agent/kf/` is missing entirely, report the error and suggest `/kf-setup`. **HALT**.

### Step 3 — Validate required context files

These files must exist for a properly initialized project:

| File | Required | Purpose |
|------|----------|---------|
| `.agent/kf/product.yaml` | Yes | Product definition |
| `.agent/kf/tech-stack.yaml` | Yes | Technology stack |
| `.agent/kf/workflow.yaml` | Yes | Development workflow |
| `.agent/kf/tracks.yaml` | Yes | Track registry |
| `.agent/kf/config.yaml` | Yes | Project configuration |
| `.agent/kf/product-guidelines.yaml` | No | Optional product guidelines |

```bash
for file in product.yaml tech-stack.yaml workflow.yaml tracks.yaml config.yaml; do
  git show ${PRIMARY_BRANCH}:.agent/kf/${file} > /dev/null 2>&1 \
    && echo "PASS: ${file}" \
    || echo "FAIL: ${file} missing"
done

# Optional files
for file in product-guidelines.yaml; do
  git show ${PRIMARY_BRANCH}:.agent/kf/${file} > /dev/null 2>&1 \
    && echo "PASS: ${file} (optional)" \
    || echo "INFO: ${file} not present (optional)"
done
```

### Step 4 — Validate track registry

Load `tracks.yaml` and verify its structure:

```bash
.agent/kf/bin/kf-track list --all --ref ${PRIMARY_BRANCH}
```

For each track entry, verify:

| Check | Description |
|-------|-------------|
| Required fields present | `title`, `status`, `type`, `created` |
| Valid status value | One of: `pending`, `in-progress`, `completed`, `archived` |
| Valid track ID pattern | Matches `{shortname}_{YYYYMMDDHHmmssZ}` (e.g., `user_auth_20250115100000Z`) |
| No duplicate IDs | Each track ID appears exactly once |

### Step 5 — Validate track directories match registry

Every pending or in-progress track in `tracks.yaml` should have a corresponding directory at `.agent/kf/tracks/{trackId}/` containing a `track.yaml` file.

```bash
# List track directories on the primary branch
git ls-tree --name-only ${PRIMARY_BRANCH} .agent/kf/tracks/ | grep -v -E '^(deps|conflicts)\.yaml$'

# Cross-reference with registry
.agent/kf/bin/kf-track list --all --ref ${PRIMARY_BRANCH}
```

| Check | Description |
|-------|-------------|
| Directory exists for active tracks | Every pending/in-progress track has `.agent/kf/tracks/{trackId}/track.yaml` |
| No orphaned directories | No track directories without a matching registry entry |
| track.yaml is valid YAML | Each track.yaml parses without errors |

### Step 6 — Validate dependencies file

Check `.agent/kf/tracks/deps.yaml` for consistency:

```bash
git show ${PRIMARY_BRANCH}:.agent/kf/tracks/deps.yaml 2>/dev/null
```

| Check | Description |
|-------|-------------|
| Valid YAML | File parses without errors |
| All referenced track IDs exist | Every track ID in deps.yaml exists in tracks.yaml |
| No self-dependencies | No track depends on itself |
| No references to archived/completed tracks | Dependencies only reference active tracks |
| No circular dependencies | Dependency graph is acyclic |

### Step 7 — Validate conflicts file

Check `.agent/kf/tracks/conflicts.yaml` for consistency:

```bash
git show ${PRIMARY_BRANCH}:.agent/kf/tracks/conflicts.yaml 2>/dev/null
```

| Check | Description |
|-------|-------------|
| Valid YAML | File parses without errors |
| All referenced track IDs exist | Both tracks in each pair exist in tracks.yaml |
| Pair ordering | Track IDs in each pair are alphabetically ordered |
| No stale pairs | No completed/archived tracks in conflict pairs |
| Valid risk levels | Risk is one of: `low`, `medium`, `high` |

### Step 8 — Pattern matching validation

Verify that values across all files conform to expected patterns:

**Status markers in tracks.yaml:**

Valid values: `pending`, `in-progress`, `completed`, `archived`

**Track ID pattern:**

```
{shortname}_{YYYYMMDDHHmmssZ}
Example: user_auth_20250115100000Z
```

The timestamp suffix must be exactly 15 characters: 4-digit year, 2-digit month, 2-digit day, 2-digit hour, 2-digit minute, 2-digit second, followed by `Z`.

**Date fields:**

`created` and `updated` fields should be valid `YYYY-MM-DD` format.

---

## Output Format

Present results as a summary table:

```
================================================================================
                     KILOFORGE VALIDATION REPORT
================================================================================

Check                              Status
─────────────────────────────────  ──────
Directory structure                PASS
Required context files             PASS
Track registry integrity           PASS
Track directories match registry   WARN — 1 orphaned directory
Dependencies file consistency      PASS
Conflicts file consistency         PASS
Pattern matching                   PASS
─────────────────────────────────  ──────

Warnings:
  [WARN] Orphaned directory: .agent/kf/tracks/old_feature_20250101000000Z/

Overall: VALID (with warnings)
================================================================================
```

Status summary line:
- **VALID** — all checks pass
- **VALID (with warnings)** — no failures, some warnings
- **INVALID** — one or more checks failed

---

## Error States

### Kiloforge Not Initialized

If `.agent/kf/` does not exist:

```
ERROR: Kiloforge not initialized.
Run /kf-setup to initialize Kiloforge for this project.
```

### No Tracks

If tracks.yaml exists but contains zero entries:

```
Kiloforge is set up but no tracks have been created yet.
Validation is limited to directory structure and context files.
Run /kf-architect to create tracks from a feature request.
```

### Issues Found

If validation finds failures, recommend the appropriate next step:

```
Validation found issues that need repair.
Run /kf-repair to diagnose and fix these problems.
```
