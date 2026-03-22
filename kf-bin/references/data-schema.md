# Kiloforge Data Schema

Authoritative reference for all Kiloforge data files, their formats, and field definitions. All paths are relative to the project root.

## Directory Layout

```
.agent/kf/
├── config.yaml                          # Project configuration
├── product.yaml                         # Product definition
├── product-guidelines.yaml              # Product guidelines (optional)
├── tech-stack.yaml                      # Technology stack
├── workflow.yaml                        # Development workflow
├── quick-links.md                       # Navigation links (optional)
├── spec.yaml                            # Product spec snapshot (optional)
├── spec/                                # Spec operation files (optional)
│   ├── {timestamp}-{hash}-{slug}.yaml   # Finalized operations
│   └── _draft-{holder}.yaml             # In-progress drafts (not committed)
└── tracks/                              # Per-track directories
    ├── {trackId}/
    │   ├── meta.yaml                    # Track registry entry (source of truth)
    │   └── track.yaml                   # Track content (spec, plan, extra)
    └── _compacted/                      # Compaction tarballs (optional)
```

## config.yaml

Project-level settings read by all tools.

```yaml
primary_branch: main              # Branch agents read state from (default: main)
enforce_dep_ordering: true        # Skip tracks with unmet deps in work queue (default: true)
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `primary_branch` | string | `"main"` | Trunk branch for coordination |
| `enforce_dep_ordering` | bool | `true` | Dependency-aware scheduling |

## tracks/{trackId}/meta.yaml

**Source of truth** for track registration. Created by `kf-track add`. One file per track — no contention between workers.

```yaml
title: "Feature title"
status: pending
type: feature
approved: false
created: "2026-03-21"
updated: "2026-03-21"
deps:
  - prerequisite_track_id
conflicts:
  - peer: other_track_id
    risk: high
    note: "reason"
    added: "2026-03-21"
spec_refs:
  - action: required-for
    item: product.auth.login
  - action: constrained-by
    item: tech.api.cursor-pagination
archived_at: "2026-03-21"         # present only when archived
archive_reason: "completed"       # present only when archived
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Human-readable track name |
| `status` | enum | yes | `pending`, `in-progress`, `completed`, `archived` |
| `type` | string | yes | `feature`, `bug`, `chore`, `refactor` |
| `approved` | bool | yes | Approval gate (conductor-only) |
| `created` | date | yes | ISO date (YYYY-MM-DD) |
| `updated` | date | yes | ISO date, auto-updated on changes |
| `deps` | list[string] | no | Track IDs that must complete first |
| `conflicts` | list[object] | no | Conflict pairs with risk assessment |
| `spec_refs` | list[object] | no | Links to spec items (see below) |
| `archived_at` | date | no | When archived |
| `archive_reason` | string | no | Why archived |

### Track ID format

`{shortname}_{YYYYMMDDHHmmssZ}` — e.g., `user-auth_20260321143022Z`

### spec_refs actions (declarative links — no state changes)

| Action | Target type | Meaning |
|--------|-------------|---------|
| `required-for` | product item | This track helps fulfill the spec item |
| `constrained-by` | technical item | This track must follow this constraint |
| `relates-to` | any | Informational link |

### conflicts entry

| Field | Type | Description |
|-------|------|-------------|
| `peer` | string | Other track ID in the conflict pair |
| `risk` | enum | `high`, `medium`, `low` |
| `note` | string | Why these tracks conflict |
| `added` | date | When the conflict was recorded |

## tracks/{trackId}/track.yaml

Track content — specification, implementation plan, and extra metadata. Created by `kf-track-content init` or written directly.

```yaml
id: track-id_20260321143022Z
title: "Track Title"
type: feature
status: pending
created: 2026-03-21
updated: 2026-03-21
spec:
  summary: "1-2 sentence summary"
  context: |
    Product context
  codebase_analysis: |
    Key findings
  acceptance_criteria:
    - Criterion 1
    - Criterion 2
  out_of_scope: |
    Exclusions
  technical_notes: |
    Approach
plan:
  - phase: "Phase Name"
    tasks:
      - text: "Task description"
        done: false
extra: {}
```

## spec.yaml

Materialized snapshot of the product specification. Updated during archive operations. Managed by `kf-track spec` commands.

```yaml
version: 1
snapshot_date: "2026-03-21"
snapshot_after_tracks: []
snapshot_after_ops: []
items:
  product.auth.login:
    title: "User Login"
    type: product
    category: auth
    status: active
    priority: high
    description: "Email/password login"
    added_by: _init
  tech.api.cursor-pagination:
    title: "Cursor Pagination"
    type: technical
    category: api
    status: active
    priority: high
    description: "All list endpoints use cursor pagination"
    added_by: _init
```

### Item types

| Type | ID prefix | Description |
|------|-----------|-------------|
| `product` | `product.{domain}.{capability}` | User-facing capability (WHAT) |
| `technical` | `tech.{domain}.{constraint}` | Implementation constraint (HOW) |

### Item status

| Status | Description |
|--------|-------------|
| `active` | Current requirement or constraint |
| `fulfilled` | Assessed and confirmed complete |
| `deprecated` | Superseded or removed |

### Item fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Human-readable name |
| `type` | enum | yes | `product` or `technical` |
| `category` | string | yes | Domain grouping (auto-derived from ID) |
| `status` | enum | yes | `active`, `fulfilled`, `deprecated` |
| `priority` | enum | no | `high`, `medium`, `low` |
| `description` | string | no | Detailed description |
| `added_by` | string | auto | Source that created this item |
| `fulfilled_by` | string | auto | Source that fulfilled this item |
| `deprecated_by` | string | auto | Source that deprecated this item |
| `modified_by` | string | auto | Source that last modified this item |
| `moved_by` | string | auto | Source that moved this item |
| `moved_from` | string | auto | Previous ID before move |

## spec/{timestamp}-{hash}-{slug}.yaml

Spec operation files. All spec state changes go through these. Sorted by filename for deterministic replay.

```yaml
date: "2026-03-21"
author: architect-1
description: "Add auth spec items"
operations:
  - action: added
    item: product.auth.login
    title: "User Login"
    priority: high
    description: "Email/password login"
  - action: fulfilled
    item: product.auth.registration
  - action: modified
    item: product.api.users
    priority: high
  - action: deprecated
    item: product.legacy.sessions
  - action: moved
    item: product.old.feature
    to: product.new.feature
  - action: unfulfilled
    item: product.auth.login
    reason: "New OAuth2 requirement"
```

### Spec operation actions (past-tense events)

| Action | Required fields | Description |
|--------|----------------|-------------|
| `added` | `title` | Introduces a new spec item |
| `fulfilled` | — | Marks item as fulfilled (after assessment) |
| `modified` | any of: `title`, `description`, `category`, `priority` | Changes item fields |
| `deprecated` | — | Marks item as deprecated/superseded |
| `moved` | `to` (new item ID) | Reparents item to a new ID |
| `unfulfilled` | `reason` | Reverts fulfilled status |

### Draft files

`_draft-{holder}.yaml` — same format as operation files but excluded from materialization. Finalized via `kf-track spec op finalize`.

## product.yaml

Free-form YAML describing the product: name, description, problem statement, target users, goals. No strict schema — content varies by project.

## tech-stack.yaml

Free-form YAML describing the technology stack: languages, frameworks, database, infrastructure, dependencies.

## workflow.yaml

Development workflow configuration: TDD strictness, commit strategy, verification commands.

## product-guidelines.yaml (optional)

Product voice/tone and design principles.

## quick-links.md (optional)

Markdown file with navigation links. Managed by `kf-track quick-links`.
