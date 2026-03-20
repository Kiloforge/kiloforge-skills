"""Product specification management for Kiloforge.

The product spec uses an event-sourcing model:
  - spec.yaml is a materialized snapshot (ground truth at a point in time)
  - .agent/kf/spec/ contains operation files (ALL state changes to the spec)
  - Tracks declare spec_refs in meta.yaml (declarative links, no state changes)
  - Materialization replays: snapshot → spec operations (by filename order)
  - Archive operations re-snapshot the materialized spec

Item IDs are hierarchical, using dot notation:
    auth.oauth2, auth.mfa, api.rate-limiting, data.export

spec.yaml format:
    version: 1
    snapshot_date: "2026-03-21"
    snapshot_after_tracks: []       # tracks baked into this snapshot
    snapshot_after_ops: []          # operation file names baked into snapshot
    items:
      auth.oauth2:
        title: "OAuth2 Authentication"
        category: auth
        status: active
        priority: high
        description: "OAuth2-based user authentication"
        added_by: _init

Spec operation file format (.agent/kf/spec/{timestamp}-{hash}-{slug}.yaml):
    date: "2026-03-21"
    author: architect-1
    description: "Initial product spec from product.md"
    operations:
      - action: adds
        item: auth.oauth2
        title: "OAuth2 Authentication"
        category: auth
        priority: high
        description: "OAuth2-based user auth"
      - action: fulfills
        item: auth.login
      - action: moves
        item: legacy.session-auth
        to: auth.session

Track meta.yaml spec_refs format (declarative links only):
    spec_refs:
      - action: required-for
        item: auth.oauth2
      - action: relates-to
        item: api.rate-limiting

Fulfillment flow:
  1. Architect creates tracks with spec_refs: [{action: required-for, item: X}]
  2. Multiple tracks can be required-for the same spec item
  3. Developers implement and complete tracks
  4. When all tracks required-for item X are complete, item is "ready for assessment"
  5. An implementer assesses and creates a spec op: {action: fulfills, item: X}
"""

import secrets
import subprocess
import yaml
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Helpers ──────────────────────────────────────────────────────────────────

def today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_timestamp() -> str:
    """Timestamp for operation file names: YYYYMMDD-HHMMSSZ-{hash}"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    h = secrets.token_hex(3)  # 6-char random hash
    return f"{ts}-{h}"


SPEC_HEADER = """\
# Kiloforge Product Specification
#
# Materialized snapshot. Updated during archive operations.
#
# SPEC OPERATIONS (.agent/kf/spec/):
#   All state changes to the spec go through operation files.
#   adds       — Introduces a new spec item (requires title + description)
#   fulfills   — Marks a spec item as fulfilled (after assessment)
#   modifies   — Changes an existing spec item's fields
#   deprecates — Removes/supersedes an existing spec item
#   moves      — Reparents a spec item (requires 'to' field with new ID)
#   unfulfills — Reverts fulfilled status (requires 'reason')
#
# TRACK SPEC_REFS (.agent/kf/tracks/{id}/meta.yaml):
#   Tracks declare links to spec items but never change spec state.
#   required-for — This track is needed to fulfill the spec item
#   relates-to   — Informational link (no fulfillment implications)
#
# ITEM IDS: Hierarchical dot notation (e.g., auth.oauth2, api.rate-limiting)
# STATUS: active | fulfilled | deprecated
#
# TOOL: Use `kf-track spec` to manage. Do not edit by hand.

"""

# Actions allowed in spec operation files (ALL state changes)
SPEC_OP_ACTIONS = ("adds", "fulfills", "modifies", "deprecates", "moves",
                   "unfulfills")
# Actions allowed in track spec_refs (declarative links only, no state changes)
TRACK_REF_ACTIONS = ("required-for", "relates-to")
# All valid actions (union)
VALID_ACTIONS = SPEC_OP_ACTIONS + TRACK_REF_ACTIONS

VALID_STATUSES = ("active", "fulfilled", "deprecated")
ITEM_FIELDS = ("title", "category", "status", "priority", "description",
               "added_by", "fulfilled_by", "deprecated_by", "modified_by",
               "moved_by", "moved_from", "unfulfilled_by", "unfulfill_reason")


def _ordered_item(data: dict) -> dict:
    """Return item dict with canonical field order."""
    out = {}
    for key in ITEM_FIELDS:
        if key in data:
            out[key] = data[key]
    for key in data:
        if key not in out:
            out[key] = data[key]
    return out


def _run_git(*args) -> str:
    """Run a git command and return stdout. Returns '' on failure."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, check=False
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def parent_id(item_id: str) -> str:
    """Return the parent ID of a hierarchical item ID."""
    if "." in item_id:
        return item_id.rsplit(".", 1)[0]
    return ""


def children_of(items: dict[str, dict], prefix: str) -> dict[str, dict]:
    """Return items that are direct children of prefix."""
    result = {}
    depth = prefix.count(".") + 1 if prefix else 0
    for item_id, data in items.items():
        if prefix and not item_id.startswith(prefix + "."):
            continue
        if not prefix and "." in item_id:
            if item_id.count(".") != 0:
                continue
        if item_id.count(".") == depth:
            result[item_id] = data
    return result


def tree_under(items: dict[str, dict], prefix: str) -> dict[str, dict]:
    """Return all items under a prefix (any depth)."""
    if not prefix:
        return dict(items)
    return {k: v for k, v in items.items()
            if k == prefix or k.startswith(prefix + ".")}


# ── SpecOp (standalone operation file) ───────────────────────────────────────

class SpecOp:
    """A standalone spec operation file (.agent/kf/spec/{name}.yaml)."""

    def __init__(self, name: str = "", date: str = "",
                 author: str = "", description: str = ""):
        self.name = name
        self.date = date or today_iso()
        self.author = author
        self.description = description
        self.operations: list[dict] = []

    @classmethod
    def load(cls, path: Path) -> "SpecOp":
        op = cls(name=path.stem)
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            return op
        op.date = data.get("date", "")
        op.author = data.get("author", "")
        op.description = data.get("description", "")
        ops = data.get("operations", [])
        if isinstance(ops, list):
            op.operations = ops
        return op

    @classmethod
    def from_text(cls, text: str, name: str = "") -> "SpecOp":
        op = cls(name=name)
        if not text.strip():
            return op
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return op
        op.date = data.get("date", "")
        op.author = data.get("author", "")
        op.description = data.get("description", "")
        ops = data.get("operations", [])
        if isinstance(ops, list):
            op.operations = ops
        return op

    def save(self, path: Path):
        data = {
            "date": self.date,
            "author": self.author,
            "description": self.description,
            "operations": self.operations,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(data, default_flow_style=False,
                                  sort_keys=False, allow_unicode=True))

    def add_operation(self, action: str, item: str, **kwargs):
        """Add an operation to this file."""
        op = {"action": action, "item": item}
        op.update(kwargs)
        self.operations.append(op)


def load_spec_ops(spec_dir: Path) -> list[SpecOp]:
    """Load all finalized spec operation files, sorted by filename.

    Excludes draft files (_draft-*.yaml) which are in-progress.
    """
    if not spec_dir.exists():
        return []
    files = sorted(f for f in spec_dir.glob("*.yaml")
                   if not f.name.startswith("_draft-"))
    if not files:
        return []

    def _load(f):
        try:
            return SpecOp.load(f)
        except Exception:
            return None

    if len(files) <= 1:
        results = [_load(f) for f in files]
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(files))) as pool:
            results = list(pool.map(_load, files))

    return [r for r in results if r is not None]


def load_spec_ops_from_ref(ref: str,
                           spec_dir_rel: str = ".agent/kf/spec") -> list[SpecOp]:
    """Load spec operation files from a git ref using batch operations."""
    ls_output = _run_git("ls-tree", "--name-only", ref, f"{spec_dir_rel}/")
    if not ls_output.strip():
        return []

    filenames = []
    for line in ls_output.strip().splitlines():
        name = line.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".yaml") and not name.startswith("_draft-"):
            filenames.append(name)

    if not filenames:
        return []

    filenames.sort()

    # Batch read
    specs_input = "\n".join(
        f"{ref}:{spec_dir_rel}/{f}" for f in filenames
    ) + "\n"

    result = subprocess.run(
        ["git", "cat-file", "--batch"],
        input=specs_input,
        capture_output=True, text=True, check=False,
    )

    ops = []
    output = result.stdout
    pos = 0
    for fname in filenames:
        nl = output.find("\n", pos)
        if nl == -1:
            break
        header = output[pos:nl]
        pos = nl + 1

        if "missing" in header:
            continue

        parts = header.split()
        if len(parts) < 3:
            continue
        try:
            size = int(parts[2])
        except (ValueError, IndexError):
            continue

        content = output[pos:pos + size]
        pos += size
        if pos < len(output) and output[pos] == "\n":
            pos += 1

        try:
            op = SpecOp.from_text(content, name=fname.replace(".yaml", ""))
            ops.append(op)
        except Exception:
            pass

    return ops


def create_spec_op(spec_dir: Path, operations: list[dict],
                   author: str = "", description: str = "",
                   slug: str = "update") -> Path:
    """Create a finalized spec operation file with auto-generated name.

    Returns the path to the created file.
    """
    timestamp = now_timestamp()
    name = f"{timestamp}-{slug}"
    op = SpecOp(name=name, author=author, description=description)
    op.operations = operations
    path = spec_dir / f"{name}.yaml"
    op.save(path)
    return path


# ── Draft management ─────────────────────────────────────────────────────────
#
# Drafts allow agents to accumulate spec operations incrementally across
# context compressions and restarts. Each operation is persisted to disk
# immediately. When ready, the draft is finalized into a timestamped file.
#
# Draft files are named _draft-{holder}.yaml and are excluded from
# materialization, git staging, and load_spec_ops().
#

DRAFT_PREFIX = "_draft-"


def _draft_path(spec_dir: Path, holder: str) -> Path:
    """Path to a holder's draft file."""
    return spec_dir / f"{DRAFT_PREFIX}{holder}.yaml"


def draft_add(spec_dir: Path, holder: str, action: str, item: str,
              **kwargs) -> Path:
    """Append an operation to the holder's draft file (persisted immediately).

    Creates the draft if it doesn't exist, appends if it does.
    Returns the draft file path.
    """
    path = _draft_path(spec_dir, holder)
    if path.exists():
        op = SpecOp.load(path)
    else:
        op = SpecOp(name=f"{DRAFT_PREFIX}{holder}", author=holder)
    op.add_operation(action, item, **kwargs)
    op.save(path)
    return path


def draft_load(spec_dir: Path, holder: str) -> Optional[SpecOp]:
    """Load a holder's draft, or None if no draft exists."""
    path = _draft_path(spec_dir, holder)
    if not path.exists():
        return None
    return SpecOp.load(path)


def draft_list(spec_dir: Path) -> list[tuple[str, SpecOp]]:
    """List all draft files. Returns [(holder, SpecOp), ...]."""
    if not spec_dir.exists():
        return []
    results = []
    for f in sorted(spec_dir.glob(f"{DRAFT_PREFIX}*.yaml")):
        holder = f.stem[len(DRAFT_PREFIX):]
        try:
            results.append((holder, SpecOp.load(f)))
        except Exception:
            pass
    return results


def draft_finalize(spec_dir: Path, holder: str,
                   description: str = "",
                   slug: str = "spec-update") -> Optional[Path]:
    """Finalize a draft into a permanent timestamped operation file.

    Removes the draft file and creates a finalized one.
    Returns the new file path, or None if no draft exists.
    """
    draft = draft_load(spec_dir, holder)
    if draft is None or not draft.operations:
        return None

    # Create finalized file
    path = create_spec_op(
        spec_dir,
        operations=draft.operations,
        author=holder,
        description=description or draft.description,
        slug=slug,
    )

    # Remove draft
    draft_path = _draft_path(spec_dir, holder)
    draft_path.unlink(missing_ok=True)

    return path


def draft_discard(spec_dir: Path, holder: str) -> bool:
    """Discard a draft without finalizing. Returns True if draft existed."""
    path = _draft_path(spec_dir, holder)
    if path.exists():
        path.unlink()
        return True
    return False


def check_uncommitted_drafts(spec_dir: Path) -> list[str]:
    """Check for draft files that should not be committed.

    Returns a list of warning messages. Agents and merge scripts should
    call this before committing to prevent accidental draft inclusion.
    """
    warnings = []
    if not spec_dir.exists():
        return warnings
    for f in spec_dir.glob(f"{DRAFT_PREFIX}*.yaml"):
        holder = f.stem[len(DRAFT_PREFIX):]
        try:
            op = SpecOp.load(f)
            count = len(op.operations)
        except Exception:
            count = 0
        warnings.append(
            f"Draft spec operations found: {f.name} "
            f"({count} operation(s) by {holder}). "
            f"Finalize with `kf-track spec op finalize` before committing."
        )
    return warnings


# ── SpecSnapshot ─────────────────────────────────────────────────────────────

class SpecSnapshot:
    """Read/write interface for spec.yaml."""

    def __init__(self):
        self.version: int = 1
        self.snapshot_date: str = today_iso()
        self.snapshot_after_tracks: list[str] = []
        self.snapshot_after_ops: list[str] = []
        self.items: dict[str, dict] = {}

    @classmethod
    def load(cls, path: Path) -> "SpecSnapshot":
        """Load from a spec.yaml file."""
        snap = cls()
        if not path.exists():
            return snap
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            return snap
        snap.version = data.get("version", 1)
        snap.snapshot_date = data.get("snapshot_date", "")
        snap.snapshot_after_tracks = data.get("snapshot_after_tracks", [])
        snap.snapshot_after_ops = data.get("snapshot_after_ops", [])
        items = data.get("items", {})
        if isinstance(items, dict):
            snap.items = items
        return snap

    @classmethod
    def from_text(cls, text: str) -> "SpecSnapshot":
        """Load from a YAML string (e.g., git show output)."""
        snap = cls()
        if not text.strip():
            return snap
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return snap
        snap.version = data.get("version", 1)
        snap.snapshot_date = data.get("snapshot_date", "")
        snap.snapshot_after_tracks = data.get("snapshot_after_tracks", [])
        snap.snapshot_after_ops = data.get("snapshot_after_ops", [])
        items = data.get("items", {})
        if isinstance(items, dict):
            snap.items = items
        return snap

    def save(self, path: Path):
        """Write spec.yaml to disk."""
        data = {
            "version": self.version,
            "snapshot_date": self.snapshot_date,
            "snapshot_after_tracks": sorted(self.snapshot_after_tracks),
            "snapshot_after_ops": sorted(self.snapshot_after_ops),
            "items": {},
        }
        for item_id in sorted(self.items.keys()):
            data["items"][item_id] = _ordered_item(self.items[item_id])

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            SPEC_HEADER
            + yaml.dump(data, default_flow_style=False, sort_keys=False,
                        allow_unicode=True)
        )

    def add_item(self, item_id: str, title: str, category: str = "",
                 priority: str = "medium", description: str = "",
                 added_by: str = "_init"):
        """Add a new spec item."""
        if item_id in self.items:
            raise ValueError(f"Spec item already exists: {item_id}")
        if not category and "." in item_id:
            category = item_id.split(".")[0]
        self.items[item_id] = _ordered_item({
            "title": title,
            "category": category,
            "status": "active",
            "priority": priority,
            "description": description,
            "added_by": added_by,
        })

    def has_item(self, item_id: str) -> bool:
        return item_id in self.items

    def get_item(self, item_id: str) -> Optional[dict]:
        return self.items.get(item_id)

    def list_items(self, status: Optional[str] = None,
                   category: Optional[str] = None) -> dict[str, dict]:
        result = {}
        for item_id, data in self.items.items():
            if status and data.get("status") != status:
                continue
            if category and data.get("category") != category:
                continue
            result[item_id] = data
        return result

    def item_ids(self) -> list[str]:
        return sorted(self.items.keys())

    def categories(self) -> list[str]:
        cats = set()
        for data in self.items.values():
            cat = data.get("category", "")
            if cat:
                cats.add(cat)
        return sorted(cats)

    def tree(self, prefix: str = "") -> dict[str, dict]:
        return tree_under(self.items, prefix)

    def children(self, prefix: str = "") -> dict[str, dict]:
        return children_of(self.items, prefix)

    def top_level_groups(self) -> list[str]:
        groups = set()
        for item_id in self.items:
            groups.add(item_id.split(".")[0])
        return sorted(groups)


# ── Materialization ──────────────────────────────────────────────────────────

def materialize(snapshot: SpecSnapshot,
                spec_ops: Optional[list[SpecOp]] = None) -> SpecSnapshot:
    """Replay spec operations on top of the snapshot.

    Only spec operation files change the spec state. Track spec_refs
    (required-for, relates-to) are declarative links that don't modify
    the spec — use fulfillment_status() to assess readiness.

    Args:
        snapshot: The base spec snapshot.
        spec_ops: Spec operation files (from .agent/kf/spec/).

    Returns:
        A new SpecSnapshot representing the materialized spec.
    """
    result = SpecSnapshot()
    result.version = snapshot.version
    result.snapshot_date = snapshot.snapshot_date
    result.snapshot_after_tracks = list(snapshot.snapshot_after_tracks)
    result.snapshot_after_ops = list(snapshot.snapshot_after_ops)
    result.items = {k: dict(v) for k, v in snapshot.items.items()}

    baked_ops = set(snapshot.snapshot_after_ops)

    if spec_ops:
        for op in spec_ops:
            if op.name in baked_ops:
                continue
            source = f"op:{op.name}"
            for ref in op.operations:
                if not isinstance(ref, dict):
                    continue
                action = ref.get("action", "")
                item_id = ref.get("item", "")
                if action and item_id:
                    _apply_ref(result, source, action, item_id, ref)

    return result


def _apply_ref(spec: SpecSnapshot, source_id: str, action: str,
               item_id: str, ref: dict):
    """Apply a single spec operation."""

    if action == "adds":
        if item_id not in spec.items:
            category = ref.get("category", "")
            if not category and "." in item_id:
                category = item_id.split(".")[0]
            spec.items[item_id] = _ordered_item({
                "title": ref.get("title", item_id),
                "category": category,
                "status": "active",
                "priority": ref.get("priority", "medium"),
                "description": ref.get("description", ""),
                "added_by": source_id,
            })

    elif action == "fulfills":
        if item_id in spec.items:
            spec.items[item_id]["status"] = "fulfilled"
            spec.items[item_id]["fulfilled_by"] = source_id

    elif action == "modifies":
        if item_id in spec.items:
            item = spec.items[item_id]
            for field in ("title", "description", "category", "priority"):
                if field in ref:
                    item[field] = ref[field]
            item["modified_by"] = source_id

    elif action == "deprecates":
        if item_id in spec.items:
            spec.items[item_id]["status"] = "deprecated"
            spec.items[item_id]["deprecated_by"] = source_id

    elif action == "moves":
        new_id = ref.get("to", "")
        if not new_id:
            return
        if item_id in spec.items and new_id not in spec.items:
            item_data = dict(spec.items.pop(item_id))
            item_data["moved_by"] = source_id
            item_data["moved_from"] = item_id
            if "." in new_id:
                item_data["category"] = new_id.split(".")[0]
            spec.items[new_id] = _ordered_item(item_data)
            # Move children
            old_prefix = item_id + "."
            new_prefix = new_id + "."
            children_to_move = [
                (k, v) for k, v in list(spec.items.items())
                if k.startswith(old_prefix)
            ]
            for old_child_id, child_data in children_to_move:
                del spec.items[old_child_id]
                new_child_id = new_prefix + old_child_id[len(old_prefix):]
                child_data = dict(child_data)
                child_data["moved_by"] = source_id
                child_data["moved_from"] = old_child_id
                if "." in new_child_id:
                    child_data["category"] = new_child_id.split(".")[0]
                spec.items[new_child_id] = _ordered_item(child_data)

    elif action == "unfulfills":
        if item_id in spec.items:
            item = spec.items[item_id]
            if item.get("status") == "fulfilled":
                item["status"] = "active"
                item["unfulfilled_by"] = source_id
                reason = ref.get("reason", "")
                if reason:
                    item["unfulfill_reason"] = reason
                # Clear the fulfilled_by since it's no longer fulfilled
                item.pop("fulfilled_by", None)

    # Track ref actions (required-for, relates-to) are declarative links
    # and do not modify the spec. They are handled by fulfillment_status().
    # Unknown actions silently ignored for forward compatibility.


def snapshot_from_materialized(materialized: SpecSnapshot,
                               archived_track_ids: list[str],
                               consumed_op_names: Optional[list[str]] = None,
                               ) -> SpecSnapshot:
    """Create a new snapshot after archiving tracks / consuming ops.

    Called during archive/bulk-archive to persist the current
    materialized state as the new baseline snapshot.
    """
    snap = SpecSnapshot()
    snap.version = materialized.version + 1
    snap.snapshot_date = today_iso()
    snap.snapshot_after_tracks = sorted(
        set(materialized.snapshot_after_tracks) | set(archived_track_ids)
    )
    snap.snapshot_after_ops = sorted(
        set(materialized.snapshot_after_ops) | set(consumed_op_names or [])
    )
    snap.items = {k: dict(v) for k, v in materialized.items.items()}
    return snap


# ── Validation ───────────────────────────────────────────────────────────────

def validate_spec_refs(spec: SpecSnapshot,
                       spec_refs: list[dict]) -> list[str]:
    """Validate track spec_refs (declarative links: required-for, relates-to).

    Tracks cannot perform spec state changes — those must go through
    spec operation files in .agent/kf/spec/.

    Returns a list of error messages (empty = valid).
    """
    errors = []
    for i, ref in enumerate(spec_refs):
        action = ref.get("action", "")
        item_id = ref.get("item", "")
        prefix = f"spec_refs[{i}]"

        if not action:
            errors.append(f"{prefix}: missing 'action'")
            continue
        if not item_id:
            errors.append(f"{prefix}: missing 'item'")
            continue

        if action in SPEC_OP_ACTIONS:
            errors.append(
                f"{prefix}: '{action}' is a spec operation — use a spec "
                f"operation file (.agent/kf/spec/) instead of track spec_refs")
            continue

        if action not in TRACK_REF_ACTIONS:
            errors.append(f"{prefix}: unknown action '{action}'")
            continue

        # Both required-for and relates-to need valid spec items
        if not spec.has_item(item_id):
            errors.append(
                f"{prefix}: item '{item_id}' not found in spec")
        elif action == "required-for":
            item = spec.get_item(item_id)
            if item and item.get("status") == "deprecated":
                errors.append(
                    f"{prefix}: item '{item_id}' is deprecated")

    return errors


def validate_spec_ops(spec: SpecSnapshot,
                      operations: list[dict]) -> list[str]:
    """Validate spec operation entries (structural changes).

    Returns a list of error messages (empty = valid).
    """
    errors = []
    for i, ref in enumerate(operations):
        action = ref.get("action", "")
        item_id = ref.get("item", "")
        prefix = f"operations[{i}]"

        if not action:
            errors.append(f"{prefix}: missing 'action'")
            continue
        if not item_id:
            errors.append(f"{prefix}: missing 'item'")
            continue

        if action in TRACK_REF_ACTIONS:
            errors.append(
                f"{prefix}: '{action}' belongs in track spec_refs, "
                f"not in spec operations")
            continue

        if action not in SPEC_OP_ACTIONS:
            errors.append(f"{prefix}: unknown action '{action}'")
            continue

        if action == "adds":
            if not ref.get("title"):
                errors.append(f"{prefix}: 'adds' requires 'title'")
            if spec.has_item(item_id):
                errors.append(
                    f"{prefix}: item '{item_id}' already exists "
                    f"(use 'modifies' to change it)")

        elif action in ("modifies", "deprecates"):
            if not spec.has_item(item_id):
                errors.append(
                    f"{prefix}: item '{item_id}' not found in spec")

        elif action == "moves":
            if not ref.get("to"):
                errors.append(f"{prefix}: 'moves' requires 'to'")
            elif spec.has_item(ref["to"]):
                errors.append(
                    f"{prefix}: target '{ref['to']}' already exists")
            if not spec.has_item(item_id):
                errors.append(
                    f"{prefix}: item '{item_id}' not found in spec")

        elif action == "fulfills":
            if not spec.has_item(item_id):
                errors.append(
                    f"{prefix}: item '{item_id}' not found in spec")
            elif (spec.get_item(item_id) or {}).get("status") == "deprecated":
                errors.append(
                    f"{prefix}: item '{item_id}' is deprecated")

        elif action == "unfulfills":
            if not spec.has_item(item_id):
                errors.append(
                    f"{prefix}: item '{item_id}' not found in spec")
            elif (spec.get_item(item_id) or {}).get("status") != "fulfilled":
                errors.append(
                    f"{prefix}: item '{item_id}' is not fulfilled")
            if not ref.get("reason"):
                errors.append(
                    f"{prefix}: 'unfulfills' requires 'reason'")

    return errors


# ── Fulfillment readiness ────────────────────────────────────────────────────

def fulfillment_status(spec: SpecSnapshot,
                       tracks: dict[str, dict]) -> dict[str, dict]:
    """Compute fulfillment readiness for each active spec item.

    Examines track spec_refs (required-for) to determine which tracks
    are needed for each spec item and whether they're all completed.

    Args:
        spec: The current (materialized) spec snapshot.
        tracks: Dict of {track_id: track_meta_dict} — all tracks.

    Returns:
        Dict of {item_id: {
            "status": spec item status,
            "required_tracks": [track_ids required for this item],
            "completed_tracks": [completed track_ids],
            "pending_tracks": [incomplete track_ids],
            "ready_for_assessment": bool (all required tracks completed),
            "has_requirements": bool (at least one track is required-for),
        }}

    Only includes active (non-deprecated) spec items.
    """
    result = {}

    # Build reverse index: spec_item → [track_ids required for it]
    item_tracks: dict[str, list[str]] = {}
    for tid, meta in tracks.items():
        spec_refs = meta.get("spec_refs")
        if not spec_refs or not isinstance(spec_refs, list):
            continue
        for ref in spec_refs:
            if not isinstance(ref, dict):
                continue
            if ref.get("action") == "required-for":
                item_id = ref.get("item", "")
                if item_id:
                    item_tracks.setdefault(item_id, []).append(tid)

    for item_id, item_data in spec.items.items():
        status = item_data.get("status", "active")
        if status == "deprecated":
            continue

        required = item_tracks.get(item_id, [])
        completed = []
        pending = []
        for tid in required:
            track_meta = tracks.get(tid, {})
            if track_meta.get("status") == "completed":
                completed.append(tid)
            else:
                pending.append(tid)

        has_reqs = len(required) > 0
        ready = has_reqs and len(pending) == 0

        result[item_id] = {
            "status": status,
            "required_tracks": sorted(required),
            "completed_tracks": sorted(completed),
            "pending_tracks": sorted(pending),
            "ready_for_assessment": ready,
            "has_requirements": has_reqs,
        }

    return result
