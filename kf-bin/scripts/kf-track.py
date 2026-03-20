#!/usr/bin/env python3
"""kf-track -- Track registry management tool for Kiloforge agents.

Operates on per-track meta.yaml files in .agent/kf/tracks/{trackId}/meta.yaml.
Designed for use by both humans and AI agents.

META.YAML FORMAT:
  Each track stores metadata in its own directory:
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

STATUS VALUES: pending | in-progress | completed | archived
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.tracks import TracksRegistry

# --- Config ---
SCRIPT_DIR = Path(__file__).resolve().parent
# Scripts live globally at ~/.kf/bin/; KF_DIR is the project's .agent/kf/ (resolved from cwd)
KF_DIR = Path(os.environ["KF_DIR"]) if "KF_DIR" in os.environ else Path.cwd() / ".agent" / "kf"
TRACKS_FILE = KF_DIR / "tracks.yaml"
DEPS_FILE = KF_DIR / "tracks" / "deps.yaml"
CONFLICTS_FILE = KF_DIR / "tracks" / "conflicts.yaml"
COMPACTIONS_FILE = KF_DIR / "compactions.yaml"
TRACKS_DIR = KF_DIR / "tracks"
ARCHIVE_DIR = TRACKS_DIR / "_archive"
QUICK_LINKS_FILE = KF_DIR / "quick-links.md"
CONFIG_FILE = KF_DIR / "config.yaml"

# Config schema: list of (key, type, default)
_CONFIG_SCHEMA = [
    ("primary_branch", "string", "main"),
    ("enforce_dep_ordering", "bool", "true"),
]

# Global registry instance
_registry: Optional[TracksRegistry] = None


def _get_registry() -> TracksRegistry:
    global _registry
    if _registry is None:
        _registry = TracksRegistry(TRACKS_DIR)
    return _registry


def _reset_registry():
    """Force re-scan on next access."""
    global _registry
    _registry = None


# --- Helpers ---
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def run_git(*args, check=False):
    """Run a git command and return stdout. Returns empty string on failure unless check=True."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, check=check
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        if check:
            raise
        return ""


def normalize_json(raw_json):
    """Normalize JSON to canonical field order for tracks.yaml stability."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return raw_json

    canonical_keys = ["title", "status", "type", "approved", "created", "updated"]
    optional_ordered = ["archived_at", "archive_reason"]
    result = {}
    for k in canonical_keys:
        if k in data:
            result[k] = data[k]
    for k in optional_ordered:
        if k in data:
            result[k] = data[k]
    # Any remaining keys
    for k in data:
        if k not in result:
            result[k] = data[k]
    return json.dumps(result, separators=(",", ":"))


def _conflict_pair_key(a, b):
    """Build the canonical pair key (lower/higher alphabetical order)."""
    if a < b:
        return f"{a}/{b}"
    return f"{b}/{a}"


# --- File reading helpers (used by non-track operations) ---
def read_file_lines(filepath):
    """Read a file and return list of lines (without newlines). Returns [] if missing."""
    fp = Path(filepath)
    if not fp.exists():
        return []
    return fp.read_text().splitlines()


def read_file_text(filepath):
    """Read file content as string. Returns '' if missing."""
    fp = Path(filepath)
    if not fp.exists():
        return ""
    return fp.read_text()


def write_file(filepath, content):
    """Write content to file, creating parent dirs as needed."""
    fp = Path(filepath)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)


# --- Claim detection ---
def branch_scan_claimed():
    """Scan git branches for implementation branches and return claimed track IDs.
    Returns list of (track_id, worker_name) tuples.
    """
    results = []
    output = run_git("branch", "--list", "feature/*", "bug/*", "chore/*", "refactor/*")
    if not output:
        return results

    branches = [b.strip().lstrip("* ") for b in output.splitlines() if b.strip()]
    if not branches:
        return results

    # Build worktree map
    worktree_map = {}
    wt_output = run_git("worktree", "list")
    if wt_output:
        for wline in wt_output.splitlines():
            parts = wline.split()
            if len(parts) >= 3:
                wt_path = parts[0]
                wt_branch = parts[2].strip("[]")
                worktree_map[wt_branch] = wt_path

    for branch in branches:
        # Extract track ID: strip type prefix
        track_id = branch.split("/", 1)[1] if "/" in branch else branch
        worker = ""
        if branch in worktree_map:
            worker = os.path.basename(worktree_map[branch])
        results.append((track_id, worker))

    return results


def server_query_claims():
    """Query orchestrator claim API for claimed tracks."""
    orch_url = os.environ.get("KF_ORCH_URL", "http://localhost:39517")
    try:
        result = subprocess.run(
            ["curl", "-sf", "--max-time", "1", f"{orch_url}/api/tracks/claims"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        return [(item.get("track_id", ""), item.get("worker", "")) for item in data]
    except Exception:
        return []


def worktree_lock_claimed():
    """Read claims from per-worktree claim locks (instant — filesystem read).
    Returns list of (track_id, worker_name) tuples.
    """
    try:
        from lib import worktree_lock
        return worktree_lock.claimed_track_ids()
    except Exception:
        return []


def get_claimed_tracks():
    """Get claimed tracks from all sources, deduplicated.
    Priority: worktree locks (instant) > server > branch scan (slow).
    """
    seen = set()
    results = []

    # 1. Worktree claim locks (instant)
    for tid, worker in worktree_lock_claimed():
        if tid not in seen:
            seen.add(tid)
            results.append((tid, worker))

    # 2. Orchestrator API (fast)
    for tid, worker in server_query_claims():
        if tid not in seen:
            seen.add(tid)
            results.append((tid, worker))

    # 3. Branch scan (slow — only if no claims found yet)
    if not results:
        for tid, worker in branch_scan_claimed():
            if tid not in seen:
                seen.add(tid)
                results.append((tid, worker))

    return results


def is_track_claimed(track_id):
    """Check if a specific track is claimed. Returns (True, worker) or (False, '')."""
    # Fast path: check worktree locks first (instant)
    try:
        from lib import worktree_lock
        claim = worktree_lock.find_track_claim(track_id)
        if claim:
            wt_name, info = claim
            return True, info.get("holder", wt_name)
    except Exception:
        pass
    # Full search
    claims = get_claimed_tracks()
    for tid, worker in claims:
        if tid == track_id:
            return True, worker
    return False, ""


# --- Track file operations ---
def ensure_tracks_file():
    """No-op: meta.yaml created on add."""
    pass


def ensure_deps_file():
    """No-op: deps stored in meta.yaml."""
    pass


def ensure_conflicts_file():
    """No-op: conflicts stored in meta.yaml."""
    pass


def ensure_compactions_file():
    if not COMPACTIONS_FILE.exists():
        write_file(COMPACTIONS_FILE, (
            "# Kiloforge Compaction Registry\n"
            "#\n"
            "# FORMAT: <commit-hash>: {\"date\":\"...\",\"completed\":N,\"archived\":N,\"track_ids\":[\"...\"],\"first_created\":\"...\",\"last_created\":\"...\"}\n"
            "# ORDER:  Lines sorted by date (newest first) -- most recent compaction at top of data section.\n"
            "# TOOL:   Use `kf-track compact` to manage. Do not edit by hand.\n"
            "#\n"
            "# RECOVERY:\n"
            "#   git show <commit>:.agent/kf/tracks.yaml              -- full track registry at that point\n"
            "#   git show <commit>:.agent/kf/tracks/<id>/spec.md      -- specific track files\n"
            "#   git ls-tree <commit> .agent/kf/tracks/                -- list all track directories\n"
            "#\n"
        ))


def ensure_quick_links_file():
    if not QUICK_LINKS_FILE.exists():
        write_file(QUICK_LINKS_FILE, (
            "# Quick Links\n"
            "#\n"
            "# Navigation links to key project files. Managed by `kf-track quick-links`.\n"
            "#\n"
            "# FORMAT: - [Label](./relative/path.md)\n"
            "\n"
            "- [Product Definition](./product.md)\n"
            "- [Tech Stack](./tech-stack.md)\n"
            "- [Track State](./tracks/)\n"
        ))


def track_exists(track_id):
    return _get_registry().exists(track_id)


def get_field(track_id, field):
    val = _get_registry().get_field(track_id, field)
    if val is None:
        return ""
    return val


def set_field(track_id, field, value):
    reg = _get_registry()
    if not reg.exists(track_id):
        print(f"ERROR: Track not found: {track_id}", file=sys.stderr)
        return False
    reg.set_field(track_id, field, value)
    reg.save(track_ids=[track_id])
    return True


def get_track_deps(track_id):
    """Returns list of dependency track IDs for a given track."""
    return _get_registry().get_deps(track_id)


def deps_satisfied(track_id):
    """Returns True if all deps for track are completed (or no deps)."""
    return _get_registry().deps_satisfied(track_id)


def dep_summary(track_id):
    """Returns a short string summarizing deps: '0/0', '2/3', etc."""
    return _get_registry().dep_summary(track_id)


def conflicts_clean_track(track_id):
    """Remove all conflict entries involving a specific track ID."""
    reg = _get_registry()
    reg.clean_conflicts(track_id)
    reg.save(track_ids=[track_id])


def sort_tracks_file():
    """No-op: not needed with per-track files."""
    pass


def sort_deps_file():
    """No-op: not needed with per-track files."""
    pass


def sort_conflicts_file():
    """No-op: not needed with per-track files."""
    pass


# --- Ref support helpers ---
def setup_ref(ref, need_deps=True):
    """Load registry from a git ref. Returns cleanup function."""
    global _registry
    _registry = TracksRegistry.from_ref(ref)

    def cleanup():
        global _registry
        _registry = None

    return cleanup


# --- Commands ---
def cmd_add(args):
    track_id = None
    title = ""
    track_type = "feature"
    status = "pending"
    deps_str = ""

    i = 0
    while i < len(args):
        if args[i] == "--title":
            title = args[i + 1]; i += 2
        elif args[i] == "--type":
            track_type = args[i + 1]; i += 2
        elif args[i] == "--status":
            status = args[i + 1]; i += 2
        elif args[i] == "--deps":
            deps_str = args[i + 1]; i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr); return 1
        else:
            track_id = args[i]; i += 1

    if not track_id:
        print('Usage: kf-track add <track-id> --title "..." [--type feature|bug|chore|refactor] [--status pending] [--deps "dep1,dep2"]', file=sys.stderr)
        return 1
    if not title:
        print("ERROR: --title is required", file=sys.stderr)
        return 1

    reg = _get_registry()

    if reg.exists(track_id):
        print(f"ERROR: Track already exists: {track_id}", file=sys.stderr)
        return 1

    deps_list = [d.strip() for d in deps_str.split(",") if d.strip()] if deps_str else []
    reg.add(track_id, title, type_=track_type, status=status, deps=deps_list, approved=False)
    reg.save(track_ids=[track_id])

    print(f"Added: {track_id}")
    return 0


def cmd_update(args):
    track_id = None
    status = None

    i = 0
    while i < len(args):
        if args[i] == "--status":
            status = args[i + 1]; i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr); return 1
        else:
            track_id = args[i]; i += 1

    if not track_id or not status:
        print("Usage: kf-track update <track-id> --status <pending|in-progress|completed|archived>", file=sys.stderr)
        return 1

    reg = _get_registry()

    if not reg.exists(track_id):
        print(f"ERROR: Track not found: {track_id}", file=sys.stderr)
        return 1

    valid = ("pending", "in-progress", "completed", "archived")
    if status not in valid:
        print(f"ERROR: Invalid status: {status} (must be pending|in-progress|completed|archived)", file=sys.stderr)
        return 1

    reg.update_status(track_id, status)

    if status in ("completed", "archived"):
        reg.clean_conflicts(track_id)

    affected = [track_id]
    reg.save(track_ids=affected)

    print(f"Updated: {track_id} \u2192 {status}")
    return 0


def cmd_set(args):
    track_id = None
    field = None
    value = None

    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            field = args[i][2:]
            value = args[i + 1]
            i += 2
        elif args[i].startswith("-"):
            field = args[i][1:]
            value = args[i + 1]
            i += 2
        else:
            if track_id is None:
                track_id = args[i]; i += 1
            else:
                print(f"Unknown argument: {args[i]}", file=sys.stderr); return 1

    if not track_id or not field or value is None:
        print("Usage: kf-track set <track-id> --<field> <value>", file=sys.stderr)
        return 1

    reg = _get_registry()

    if not reg.exists(track_id):
        print(f"ERROR: Track not found: {track_id}", file=sys.stderr)
        return 1

    reg.set_field(track_id, field, value)
    reg.save(track_ids=[track_id])
    print(f"Set {track_id}.{field} = {value}")
    return 0


def cmd_get(args):
    track_id = None
    ref = None
    cleanup = None

    i = 0
    while i < len(args):
        if args[i] == "--ref":
            ref = args[i + 1]; i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr); return 1
        else:
            track_id = args[i]; i += 1

    if not track_id:
        print("Usage: kf-track get <track-id> [--ref <branch|commit>]", file=sys.stderr)
        return 1

    if ref:
        cleanup = setup_ref(ref)

    try:
        reg = _get_registry()
        data = reg.get(track_id)
        if data is None:
            print(f"ERROR: Track not found: {track_id}", file=sys.stderr)
            return 1

        print(f"Track: {track_id}")
        display = {k: v for k, v in data.items() if k not in ("deps", "conflicts")}
        print(json.dumps(display, indent=2))

        # Show deps
        deps = reg.get_deps(track_id)
        if deps:
            print()
            print("Dependencies:")
            for dep in deps:
                dep_status = reg.get_field(dep, "status") or "unknown"
                if dep_status == "completed":
                    marker = "[x]"
                elif dep_status == "in-progress":
                    marker = "[~]"
                else:
                    marker = "[ ]"
                print(f"  {marker} {dep}")
    finally:
        if cleanup:
            cleanup()

    return 0


def cmd_list(args):
    filter_status = None
    filter_active = False
    filter_ready = False
    filter_unclaimed = False
    show_all = False
    fmt = "table"
    ref = None
    cleanup = None

    i = 0
    while i < len(args):
        if args[i] == "--status":
            filter_status = args[i + 1]; i += 2
        elif args[i] == "--active":
            filter_active = True; i += 1
        elif args[i] == "--ready":
            filter_ready = True; i += 1
        elif args[i] == "--unclaimed":
            filter_unclaimed = True; filter_active = True; i += 1
        elif args[i] == "--all":
            show_all = True; i += 1
        elif args[i] == "--json":
            fmt = "json"; i += 1
        elif args[i] == "--ids":
            fmt = "ids"; i += 1
        elif args[i] == "--ref":
            ref = args[i + 1]; i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr); return 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr); return 1

    if ref:
        cleanup = setup_ref(ref)

    try:
        reg = _get_registry()

        # Default: --ready
        if not filter_status and not filter_active and not filter_ready and not show_all:
            filter_ready = True
        if filter_ready:
            filter_active = True

        all_entries = reg.all_entries()

        # Filter by status
        if filter_status:
            entries = {tid: d for tid, d in all_entries.items() if d.get("status") == filter_status}
        elif filter_active:
            entries = {tid: d for tid, d in all_entries.items() if d.get("status") not in ("completed", "archived")}
        else:
            entries = all_entries

        # Apply --ready filter
        if filter_ready and entries:
            entries = {tid: d for tid, d in entries.items() if reg.deps_satisfied(tid)}

        # Build claimed cache
        claimed_cache = []
        if entries:
            try:
                claimed_cache = get_claimed_tracks()
            except Exception:
                claimed_cache = []

        claimed_ids = {tid for tid, _ in claimed_cache}

        # Apply --unclaimed filter
        if filter_unclaimed and entries and claimed_cache:
            entries = {tid: d for tid, d in entries.items() if tid not in claimed_ids}

        if not entries:
            if filter_unclaimed:
                print("(no unclaimed tracks \u2014 all active tracks are claimed by workers)")
            elif filter_ready:
                print("(no ready tracks \u2014 all active tracks have unmet dependencies)")
            else:
                print("(no tracks match filter)")
            return 0

        # Sort: no-deps tracks first, then tracks with deps
        no_deps = []
        has_deps = []
        for tid in sorted(entries.keys()):
            if reg.get_deps(tid):
                has_deps.append(tid)
            else:
                no_deps.append(tid)
        sorted_ids = no_deps + has_deps

        count = len(sorted_ids)

        if fmt == "ids":
            for tid in sorted_ids:
                print(tid)
        elif fmt == "json":
            for tid in sorted_ids:
                data = dict(entries[tid])
                deps_info = reg.dep_summary(tid)
                dep_list = reg.get_deps(tid)
                # Remove internal fields for JSON output
                data_out = {k: v for k, v in data.items() if k not in ("deps", "conflicts")}
                data_out["id"] = tid
                data_out["deps"] = dep_list
                data_out["deps_summary"] = deps_info
                print(json.dumps(data_out, separators=(",", ":")))
        else:
            # Table format
            print(f"{'TRACK ID':<50} {'STATUS':<13} {'TYPE':<10} {'DEPS':<10} TITLE")
            print(f"{'--------':<50} {'------':<13} {'----':<10} {'----':<10} -----")

            claimed_map = {tid: worker for tid, worker in claimed_cache}

            for tid in sorted_ids:
                data = entries[tid]
                title = data.get("title", "")
                status = data.get("status", "")
                track_type = data.get("type", "")

                # Enrich status with claim detection
                if status == "pending" and tid in claimed_ids:
                    status = "claimed"

                deps_info = reg.dep_summary(tid)

                if len(title) > 50:
                    title = title[:47] + "..."

                print(f"{tid:<50} {status:<13} {track_type:<10} {deps_info:<10} {title}")

            print()
            if filter_ready:
                print(f"{count} ready track(s)")
            else:
                print(f"{count} track(s)")
    finally:
        if cleanup:
            cleanup()

    return 0


def cmd_deps(args):
    # Extract --ref from args
    ref = None
    cleanup = None
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--ref":
            ref = args[i + 1]; i += 2
        else:
            filtered_args.append(args[i]); i += 1

    if ref:
        cleanup = setup_ref(ref)

    try:
        if not filtered_args:
            print("Usage: kf-track deps <add|remove|list|check> <track-id> [dep-id]", file=sys.stderr)
            return 1

        subcmd = filtered_args[0]
        rest = filtered_args[1:]
        reg = _get_registry()

        if subcmd == "add":
            if len(rest) < 2:
                print("Usage: kf-track deps add <track-id> <dependency-id>", file=sys.stderr)
                return 1
            track_id, dep = rest[0], rest[1]
            reg.add_dep(track_id, dep)
            reg.save(track_ids=[track_id])
            print(f"Added dependency: {track_id} \u2192 {dep}")

        elif subcmd == "remove":
            if len(rest) < 2:
                print("Usage: kf-track deps remove <track-id> <dependency-id>", file=sys.stderr)
                return 1
            track_id, dep = rest[0], rest[1]
            reg.remove_dep(track_id, dep)
            reg.save(track_ids=[track_id])
            print(f"Removed dependency: {track_id} \u2192 {dep}")

        elif subcmd == "list":
            if len(rest) < 1:
                print("Usage: kf-track deps list <track-id>", file=sys.stderr)
                return 1
            track_id = rest[0]
            deps = reg.get_deps(track_id)
            if not deps:
                print("(no dependencies)")
            else:
                for d in deps:
                    print(d)

        elif subcmd == "check":
            if len(rest) < 1:
                print("Usage: kf-track deps check <track-id>", file=sys.stderr)
                return 1
            track_id = rest[0]
            deps = reg.get_deps(track_id)
            if not deps:
                print("OK (no dependencies)")
                return 0
            any_blocked = False
            for dep in deps:
                dep_status = reg.get_field(dep, "status") or "unknown"
                if dep_status == "completed":
                    print(f"  [x] {dep}")
                else:
                    print(f"  [ ] {dep} ({dep_status})")
                    any_blocked = True
            if any_blocked:
                print("BLOCKED \u2014 not all dependencies completed")
                return 1
            else:
                print("OK \u2014 all dependencies satisfied")
                return 0
        else:
            print("Usage: kf-track deps <add|remove|list|check> <track-id> [dep-id]", file=sys.stderr)
            return 1

        return 0
    finally:
        if cleanup:
            cleanup()


def cmd_conflicts(args):
    # Extract --ref from args
    ref = None
    cleanup = None
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--ref":
            ref = args[i + 1]; i += 2
        else:
            filtered_args.append(args[i]); i += 1

    if ref:
        cleanup = setup_ref(ref)

    try:
        if not filtered_args:
            print("Usage: kf-track conflicts <add|remove|list|clean> [args]", file=sys.stderr)
            print("  add <track-a> <track-b> [risk] [note]  Add/update conflict pair", file=sys.stderr)
            print("  remove <track-a> <track-b>              Remove conflict pair", file=sys.stderr)
            print("  list [track-id]                         List pairs (optionally filtered)", file=sys.stderr)
            print("  clean                                   Remove pairs for completed tracks", file=sys.stderr)
            return 1

        subcmd = filtered_args[0]
        rest = filtered_args[1:]
        reg = _get_registry()

        if subcmd == "add":
            if len(rest) < 2:
                print("Usage: kf-track conflicts add <track-a> <track-b> [risk] [note]", file=sys.stderr)
                print("  risk: high, medium, low (default: medium)", file=sys.stderr)
                return 1
            id_a, id_b = rest[0], rest[1]
            risk = rest[2] if len(rest) > 2 else "medium"
            note = rest[3] if len(rest) > 3 else ""

            if id_a == id_b:
                print("ERROR: Cannot create conflict pair with itself", file=sys.stderr)
                return 1

            pair_key = _conflict_pair_key(id_a, id_b)
            reg.add_conflict(id_a, id_b, risk, note)
            reg.save(track_ids=[id_a, id_b])
            print(f"Added conflict pair: {pair_key} (risk: {risk})")

        elif subcmd == "remove":
            if len(rest) < 2:
                print("Usage: kf-track conflicts remove <track-a> <track-b>", file=sys.stderr)
                return 1
            id_a, id_b = rest[0], rest[1]
            pair_key = _conflict_pair_key(id_a, id_b)
            reg.remove_conflict(id_a, id_b)
            reg.save(track_ids=[id_a, id_b])
            print(f"Removed conflict pair: {pair_key}")

        elif subcmd == "list":
            filter_id = rest[0] if rest else None
            if filter_id:
                # Show conflicts for a specific track
                conflicts = reg.get_conflicts(filter_id)
                if not conflicts:
                    print(f"(no conflict pairs for {filter_id})")
                else:
                    for c in conflicts:
                        peer = c.get("peer", "")
                        pair_key = _conflict_pair_key(filter_id, peer)
                        risk = c.get("risk", "?")
                        cnote = c.get("note", "")
                        out = f"  {pair_key:<60}  risk={risk}"
                        if cnote:
                            out += f"  {cnote}"
                        print(out)
            else:
                # Show all conflict pairs
                pairs = reg.all_conflict_pairs()
                if not pairs:
                    print("(no conflict pairs)")
                else:
                    for pair_key in sorted(pairs.keys()):
                        pdata = pairs[pair_key]
                        risk = pdata.get("risk", "?")
                        cnote = pdata.get("note", "")
                        out = f"  {pair_key:<60}  risk={risk}"
                        if cnote:
                            out += f"  {cnote}"
                        print(out)

        elif subcmd == "clean":
            # Find and clean stale conflict pairs
            pairs = reg.all_conflict_pairs()
            all_entries = reg.all_entries()
            removed = 0
            affected_ids = set()

            # Scan all tracks for conflict entries involving non-active tracks
            for tid, data in all_entries.items():
                conflicts = data.get("conflicts") or []
                if not conflicts:
                    continue
                new_conflicts = []
                for c in conflicts:
                    peer = c.get("peer", "")
                    peer_status = reg.get_field(peer, "status") or "unknown"
                    own_status = data.get("status", "unknown")
                    if own_status in ("completed", "archived", "unknown") or peer_status in ("completed", "archived", "unknown"):
                        pair_key = _conflict_pair_key(tid, peer)
                        print(f"  Removed: {pair_key} ({tid}={own_status}, {peer}={peer_status})")
                        removed += 1
                        affected_ids.add(tid)
                    else:
                        new_conflicts.append(c)
                if len(new_conflicts) != len(conflicts):
                    data["conflicts"] = new_conflicts
                    reg._dirty.add(tid)

            if affected_ids:
                reg.save(track_ids=list(affected_ids))
            print(f"Cleaned {removed} stale conflict pair(s)")

        else:
            print("Usage: kf-track conflicts <add|remove|list|clean> [args]", file=sys.stderr)
            print("  add <track-a> <track-b> [risk] [note]  Add/update conflict pair", file=sys.stderr)
            print("  remove <track-a> <track-b>              Remove conflict pair", file=sys.stderr)
            print("  list [track-id]                         List pairs (optionally filtered)", file=sys.stderr)
            print("  clean                                   Remove pairs for completed tracks", file=sys.stderr)
            return 1

        return 0
    finally:
        if cleanup:
            cleanup()


def cmd_archive(args):
    if not args:
        print("Usage: kf-track archive <track-id> [reason]", file=sys.stderr)
        return 1

    track_id = args[0]
    reason = args[1] if len(args) > 1 else "completed"

    reg = _get_registry()

    if not reg.exists(track_id):
        print(f"ERROR: Track not found: {track_id}", file=sys.stderr)
        return 1

    reg.update_status(track_id, "archived")
    reg.set_field(track_id, "archive_reason", reason)
    reg.clean_conflicts(track_id)
    reg.save(track_ids=[track_id])

    print(f"Archived: {track_id} (reason: {reason})")
    return 0


def cmd_compact(args):
    subcmd = args[0] if args else "run"
    rest = args[1:] if len(args) > 1 else []

    if subcmd == "run":
        return _compact_run(rest)
    elif subcmd == "list":
        return _compact_list(rest)
    elif subcmd == "recover":
        return _compact_recover(rest)
    elif subcmd == "import":
        return _compact_import(rest)
    else:
        print("Usage: kf-track compact <run|list|recover [hash]|import [hash] [--source ...]>", file=sys.stderr)
        return 1


def _compact_run(args):
    dry_run = "--dry-run" in args

    from lib.compaction import compact_tracks

    reg = _get_registry()

    compactable_ids = []
    completed_ids = []
    archived_dir_ids = []

    # Source 1: archived track directories
    if ARCHIVE_DIR.exists():
        for d in sorted(ARCHIVE_DIR.iterdir()):
            if d.is_dir():
                archived_dir_ids.append(d.name)
                compactable_ids.append(d.name)

    # Source 2: completed tracks with directories
    completed_entries = reg.list_by_status("completed")
    for tid in sorted(completed_entries.keys()):
        track_dir = TRACKS_DIR / tid
        if track_dir.is_dir():
            completed_ids.append(tid)
            if tid not in compactable_ids:
                compactable_ids.append(tid)

    if not compactable_ids:
        print("Nothing to compact \u2014 no archived directories or completed track directories found.")
        return 0

    total = len(compactable_ids)
    completed_count = len(completed_ids)
    archived_count = len(archived_dir_ids)

    first_created = None
    last_created = None
    for tid in compactable_ids:
        created = reg.get_field(tid, "created") or ""
        if created and created != "null":
            if first_created is None or created < first_created:
                first_created = created
            if last_created is None or created > last_created:
                last_created = created

    print("Compaction summary:")
    print(f"  Total tracks to compact: {total}")
    print(f"  From _archive/:          {archived_count}")
    print(f"  Completed (with dirs):   {completed_count}")
    print(f"  Date range:              {first_created or 'unknown'} \u2014 {last_created or 'unknown'}")
    print()

    if dry_run:
        print("(dry run \u2014 no changes made)")
        print()
        print("Tracks that would be compacted:")
        for tid in compactable_ids:
            print(f"  {tid}")
        return 0

    # Create tarball archive via lib/compaction
    tarball_path = compact_tracks(TRACKS_DIR, compactable_ids)

    # Commit
    run_git("add", str(TRACKS_DIR))
    run_git("commit", "-m",
            f"chore: compact archive ({completed_count} completed, "
            f"{archived_count} archived) into {tarball_path.name}")

    print("Compaction complete.")
    print(f"  Archived {total} track directories to {tarball_path.name}")
    print(f"  Recovery: kf-track compact recover {tarball_path.stem.rsplit('.', 1)[0]}")
    return 0


def _compact_list(args):
    fmt = args[0] if args else "--table"

    from lib.compaction import list_compactions

    records = list_compactions(TRACKS_DIR)

    if not records:
        print("(no compaction records)")
        return 0

    if fmt == "--json":
        for rec in records:
            print(json.dumps(rec, separators=(",", ":")))
    else:
        print(f"{'NAME':<32} {'DATE':<12} {'TRACKS':<8} {'COMPLETED':<10} {'ARCHIVED':<10} {'FIRST':<12} {'LAST':<12}")
        print(f"{'----':<32} {'----':<12} {'------':<8} {'---------':<10} {'--------':<10} {'-----':<12} {'----':<12}")
        for rec in records:
            name = rec.get("name", "")
            date = rec.get("date", "")
            track_count = rec.get("track_count", len(rec.get("track_ids", [])))
            completed = rec.get("completed", 0)
            archived = rec.get("archived", 0)
            fc = rec.get("first_created", "")
            lc = rec.get("last_created", "")
            print(f"{name:<32} {date:<12} {str(track_count):<8} {str(completed):<10} {str(archived):<10} {fc:<12} {lc:<12}")
        print()
        print(f"{len(records)} compaction(s)")

    return 0


def _compact_recover(args):
    if not args:
        print("Usage: kf-track compact recover <compaction-name>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Extracts a compaction tarball to a temporary directory.", file=sys.stderr)
        print("Use 'kf-track compact list' to see available compaction names.", file=sys.stderr)
        return 1

    name_arg = args[0]

    from lib.compaction import extract_compaction, list_compactions

    # Find matching compaction by name prefix
    records = list_compactions(TRACKS_DIR)
    match_name = None
    for rec in records:
        rec_name = rec.get("name", "")
        if rec_name == name_arg or rec_name.startswith(name_arg):
            match_name = rec_name
            break

    if not match_name:
        print(f"ERROR: No compaction found matching '{name_arg}'", file=sys.stderr)
        print("Use 'kf-track compact list' to see available compactions.", file=sys.stderr)
        return 1

    try:
        tmp_dir = extract_compaction(TRACKS_DIR, match_name)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Extracted compaction '{match_name}' to:")
    print(f"  {tmp_dir}")
    print()
    print("Track directories:")
    for d in sorted(tmp_dir.iterdir()):
        if d.is_dir():
            print(f"  {d.name}/")
    print()
    print("To restore a track, copy its directory back:")
    print(f"  cp -r {tmp_dir}/<track-id> {TRACKS_DIR}/")
    print()
    print("Clean up when done:")
    print(f"  rm -rf {tmp_dir}")

    return 0


def _compact_import(args):
    hash_val = None
    source = "conductor"
    note = ""
    track_count = 0

    i = 0
    while i < len(args):
        if args[i] == "--source":
            source = args[i + 1]; i += 2
        elif args[i] == "--note":
            note = args[i + 1]; i += 2
        elif args[i] == "--count":
            track_count = int(args[i + 1]); i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr); return 1
        else:
            hash_val = args[i]; i += 1

    if not hash_val:
        print('Usage: kf-track compact import <commit-hash> [--source conductor] [--note "..."] [--count N]', file=sys.stderr)
        return 1

    ensure_compactions_file()

    for line in read_file_lines(COMPACTIONS_FILE):
        if line.startswith(hash_val):
            print(f"ERROR: Compaction record already exists for {hash_val}", file=sys.stderr)
            return 1

    compact_date = today_iso()
    record = {
        "date": compact_date,
        "source": source,
        "completed": track_count,
        "archived": 0,
        "track_ids": [],
        "note": note,
    }
    record_json = json.dumps(record, separators=(",", ":"))

    with open(COMPACTIONS_FILE, "a") as f:
        f.write(f"{hash_val}: {record_json}\n")

    print(f"Imported compaction record: {hash_val[:10]} (source: {source})")
    return 0


def cmd_quick_links(args):
    subcmd = args[0] if args else "show"
    rest = args[1:] if len(args) > 1 else []

    if subcmd == "show":
        ref = None
        i = 0
        while i < len(rest):
            if rest[i] == "--ref":
                ref = rest[i + 1]; i += 2
            else:
                print(f"Unknown argument: {rest[i]}", file=sys.stderr); return 1

        if ref:
            content = run_git("show", f"{ref}:.agent/kf/quick-links.md")
            if not content:
                print(f"ERROR: Cannot read quick-links.md from ref '{ref}'", file=sys.stderr)
                return 1
            for line in content.splitlines():
                if not line.startswith("#") and line.strip():
                    print(line)
        else:
            ensure_quick_links_file()
            for line in read_file_lines(QUICK_LINKS_FILE):
                if not line.startswith("#") and line.strip():
                    print(line)

    elif subcmd == "add":
        if len(rest) < 2:
            print("Usage: kf-track quick-links add <label> <path>", file=sys.stderr)
            return 1
        label, path = rest[0], rest[1]
        ensure_quick_links_file()
        content = read_file_text(QUICK_LINKS_FILE)
        if f"[{label}]" in content:
            print(f"Quick link '{label}' already exists. Remove first to update.", file=sys.stderr)
            return 1
        with open(QUICK_LINKS_FILE, "a") as f:
            f.write(f"- [{label}]({path})\n")
        print(f"Added quick link: {label} \u2192 {path}")

    elif subcmd == "remove":
        if len(rest) < 1:
            print("Usage: kf-track quick-links remove <label>", file=sys.stderr)
            return 1
        label = rest[0]
        if not QUICK_LINKS_FILE.exists():
            print("No quick-links.md file found.", file=sys.stderr)
            return 1
        content = read_file_text(QUICK_LINKS_FILE)
        if f"[{label}]" not in content:
            print(f"Quick link '{label}' not found.", file=sys.stderr)
            return 1
        lines = [l for l in read_file_lines(QUICK_LINKS_FILE) if f"[{label}]" not in l]
        write_file(QUICK_LINKS_FILE, "\n".join(lines) + "\n")
        print(f"Removed quick link: {label}")

    else:
        print("Usage: kf-track quick-links [show|add <label> <path>|remove <label>]", file=sys.stderr)
        return 1

    return 0


def cmd_index(args):
    ref = None
    i = 0
    while i < len(args):
        if args[i] == "--ref":
            ref = args[i + 1]; i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr); return 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr); return 1

    if ref:
        reg = TracksRegistry.from_ref(ref)
        quick_links_content = run_git("show", f"{ref}:.agent/kf/quick-links.md")
    else:
        reg = _get_registry()
        ensure_quick_links_file()
        quick_links_content = read_file_text(QUICK_LINKS_FILE)

    all_entries = reg.all_entries()

    output = "# Kiloforge Project Index\n\n"

    # Quick links section
    if quick_links_content:
        ql_lines = [l for l in quick_links_content.splitlines() if not l.startswith("#") and l.strip()]
        if ql_lines:
            output += "## Quick Links\n\n"
            output += "\n".join(ql_lines) + "\n\n"

    # Count tracks by status
    pending = sum(1 for d in all_entries.values() if d.get("status") == "pending")
    in_progress = sum(1 for d in all_entries.values() if d.get("status") == "in-progress")
    completed = sum(1 for d in all_entries.values() if d.get("status") == "completed")
    archived = sum(1 for d in all_entries.values() if d.get("status") == "archived")
    total = len(all_entries)

    output += "## Summary\n\n"
    output += "| Status | Count |\n"
    output += "|--------|-------|\n"
    output += f"| Pending | {pending} |\n"
    output += f"| In Progress | {in_progress} |\n"
    output += f"| Completed | {completed} |\n"
    output += f"| Archived | {archived} |\n"
    output += f"| **Total** | **{total}** |\n\n"

    # In-progress tracks
    if in_progress > 0:
        output += "## In Progress\n\n"
        for tid in sorted(all_entries.keys()):
            data = all_entries[tid]
            if data.get("status") != "in-progress":
                continue
            title = data.get("title", "")
            output += f"- **{tid}** \u2014 {title}\n"
        output += "\n"

    # Pending tracks
    if pending > 0:
        output += "## Pending\n\n"
        for tid in sorted(all_entries.keys()):
            data = all_entries[tid]
            if data.get("status") != "pending":
                continue
            title = data.get("title", "")
            output += f"- **{tid}** \u2014 {title}\n"
        output += "\n"

    print(output)
    return 0


def cmd_content(cmd, args):
    """Delegate to kf-track-content script."""
    content_script = SCRIPT_DIR / "kf-track-content.py"
    if not shutil.which("python3"):
        print(f"ERROR: python3 is required for 'kf-track {cmd}' but was not found.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Install Python 3:", file=sys.stderr)
        print("  macOS:   brew install python3", file=sys.stderr)
        print("  Ubuntu:  sudo apt install python3", file=sys.stderr)
        print("  Windows: https://www.python.org/downloads/", file=sys.stderr)
        sys.exit(1)

    # Handle --ref for read-only content commands
    ref = None
    content_args = []
    skip_next = False
    for j, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--ref":
            if j + 1 < len(args):
                ref = args[j + 1]
                skip_next = True
            continue
        content_args.append(arg)

    if ref:
        if cmd not in ("show", "spec", "plan", "progress"):
            print("ERROR: --ref is only supported for read-only commands (show, spec, plan, progress)", file=sys.stderr)
            sys.exit(1)
        track_id = content_args[0] if content_args else None
        if not track_id:
            print("ERROR: track ID required", file=sys.stderr)
            sys.exit(1)
        ref_yaml = run_git("show", f"{ref}:.agent/kf/tracks/{track_id}/track.yaml")
        if not ref_yaml:
            print(f"ERROR: Cannot read track.yaml for '{track_id}' from ref '{ref}'", file=sys.stderr)
            sys.exit(1)
        tmp_dir = tempfile.mkdtemp()
        try:
            track_dir = Path(tmp_dir) / "tracks" / track_id
            track_dir.mkdir(parents=True)
            (track_dir / "track.yaml").write_text(ref_yaml)
            env = os.environ.copy()
            env["KF_DIR"] = tmp_dir
            os.execve(
                str(content_script),
                [str(content_script), cmd] + content_args,
                env
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        os.execv(str(content_script), [str(content_script), cmd] + list(args))


def cmd_status(args):
    ref = None
    i = 0
    while i < len(args):
        if args[i] == "--ref":
            ref = args[i + 1]; i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr); return 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr); return 1

    def _read_kf(path):
        if ref:
            return run_git("show", f"{ref}:.agent/kf/{path}")
        fp = KF_DIR / path
        if fp.exists():
            return fp.read_text()
        return ""

    # --- Project name ---
    product_content = _read_kf("product.md")
    project_name = "Unknown"
    if product_content:
        # Try "## Project Name" section
        lines = product_content.splitlines()
        found_section = False
        for li, line in enumerate(lines):
            if "## Project Name" in line:
                found_section = True
                continue
            if found_section and line.strip():
                project_name = line.strip()
                break
        if project_name == "Unknown":
            for line in lines:
                if line.startswith("# "):
                    project_name = line[2:].strip()
                    break

    # --- Tracks data ---
    if ref:
        reg = TracksRegistry.from_ref(ref)
    else:
        reg = _get_registry()

    all_entries = reg.all_entries()
    if not all_entries:
        print("ERROR: No tracks found. Run /kf-setup first.", file=sys.stderr)
        return 1

    # Count by status
    pending = sum(1 for d in all_entries.values() if d.get("status") == "pending")
    in_progress = sum(1 for d in all_entries.values() if d.get("status") == "in-progress")
    completed = sum(1 for d in all_entries.values() if d.get("status") == "completed")
    archived = sum(1 for d in all_entries.values() if d.get("status") == "archived")
    total = len(all_entries)

    # --- Task progress for active tracks ---
    active_tracks = []  # list of dicts

    for tid in sorted(all_entries.keys()):
        data = all_entries[tid]
        tstatus = data.get("status", "")
        if tstatus not in ("pending", "in-progress"):
            continue
        ttitle = data.get("title", "")
        ttype = data.get("type", "")

        # Get task progress from track.yaml
        track_yaml = ""
        if ref:
            track_yaml = run_git("show", f"{ref}:.agent/kf/tracks/{tid}/track.yaml")
        else:
            track_file = KF_DIR / "tracks" / tid / "track.yaml"
            if track_file.exists():
                track_yaml = track_file.read_text()

        tdone = ttotal_tasks = pdone = ptotal = 0
        next_task = ""
        if track_yaml:
            in_plan = False
            phase_tasks_done = 0
            phase_tasks_total = 0
            current_phase = 0

            for pline in track_yaml.splitlines():
                if pline == "plan:":
                    in_plan = True
                    continue
                if not in_plan:
                    continue
                # Stop at next top-level key
                if pline and not pline.startswith(" ") and ":" in pline and pline != "plan:":
                    break
                if "  - phase:" in pline and pline.strip().startswith("- phase:"):
                    # Close previous phase
                    if current_phase > 0 and phase_tasks_done == phase_tasks_total and phase_tasks_total > 0:
                        pdone += 1
                    current_phase += 1
                    ptotal += 1
                    phase_tasks_done = 0
                    phase_tasks_total = 0
                if "      - text:" in pline:
                    ttotal_tasks += 1
                if "done: true" in pline:
                    tdone += 1
                    phase_tasks_done += 1
                    phase_tasks_total += 1
                elif "done: false" in pline:
                    phase_tasks_total += 1
                    if not next_task:
                        m = re.search(r'text:\s*"?(.+?)"?\s*$', pline)
                        if not m:
                            pass

            # Close last phase
            if current_phase > 0 and phase_tasks_done == phase_tasks_total and phase_tasks_total > 0:
                pdone += 1

            # Second pass for next_task extraction
            if not next_task:
                in_plan2 = False
                last_text = ""
                for pline in track_yaml.splitlines():
                    if pline == "plan:":
                        in_plan2 = True
                        continue
                    if not in_plan2:
                        continue
                    if pline and not pline.startswith(" ") and ":" in pline and pline != "plan:":
                        break
                    m = re.search(r'text:\s*"?(.+?)"?\s*$', pline)
                    if m:
                        last_text = m.group(1).strip('"')
                    if "done: false" in pline and not next_task:
                        next_task = last_text

        active_tracks.append({
            "id": tid,
            "title": ttitle,
            "type": ttype,
            "status": tstatus,
            "task_done": tdone,
            "task_total": ttotal_tasks,
            "phase_done": pdone,
            "phase_total": ptotal,
            "next_task": next_task,
        })

    # --- Deps data ---
    ready_ids = []
    for t in active_tracks:
        tid = t["id"]
        track_deps = reg.get_deps(tid)
        dep_total = len(track_deps)
        dep_met = 0
        blocked = False
        for dep in track_deps:
            dep_status = reg.get_field(dep, "status") or "unknown"
            if dep_status == "completed":
                dep_met += 1
            else:
                blocked = True
        t["dep_met"] = dep_met
        t["dep_total"] = dep_total

        if t["status"] == "pending" and not blocked:
            ready_ids.append(tid)

    # Total tasks across all active tracks
    total_tasks_done = sum(t["task_done"] for t in active_tracks)
    total_tasks_all = sum(t["task_total"] for t in active_tracks)

    # Claim detection
    try:
        claimed_cache = get_claimed_tracks()
    except Exception:
        claimed_cache = []
    claimed_map = {tid: worker for tid, worker in claimed_cache}
    claimed_ids = set(claimed_map.keys())

    # Progress bar helper
    def _progress_bar(done, total_val, width=20):
        if total_val == 0:
            return "[" + "." * width + "] 0%"
        pct = done * 100 // total_val
        filled = done * width // total_val
        empty = width - filled
        return "[" + "#" * filled + "." * empty + f"] {pct}%"

    def _status_label(t):
        if t["status"] == "in-progress":
            return "CLAIMED"
        elif t["status"] == "pending":
            if t["id"] in claimed_ids:
                return "CLAIMED"
            if t["id"] in ready_ids:
                return "AVAILABLE"
            return "BLOCKED"
        return t["status"].upper()

    def _fmt_deps(met, total_val):
        if total_val == 0:
            return "no deps"
        return f"{met}/{total_val} met"

    # --- Output ---
    print("=" * 80)
    print(f"                        PROJECT STATUS: {project_name}")
    print("=" * 80)
    print()
    print("-" * 80)
    print("                              OVERALL PROGRESS")
    print("-" * 80)
    print()
    done_count = completed + archived
    pct = done_count * 100 // total if total > 0 else 0
    print(f"Tracks:     {done_count}/{total} done ({pct}%)")
    print(f"Tasks:      {total_tasks_done}/{total_tasks_all} completed (active tracks)")
    print()
    print(f"Progress:   {_progress_bar(done_count, total)}")
    print()
    print(f"  completed:   {completed:4d}")
    print(f"  in-progress: {in_progress:4d}")
    print(f"  pending:     {pending:4d}")
    print(f"  archived:    {archived:4d}")
    print()

    # --- Track detail table ---
    if active_tracks:
        print("-" * 80)
        print("                              ACTIVE TRACKS")
        print("-" * 80)
        print()
        print(f"{'Status':<10} {'Track ID':<48} {'Type':<8} {'Tasks':<12} Deps")
        print(f"{'----------':<10} {'------------------------------------------------':<48} {'--------':<8} {'------------':<12} ----------")

        # In-progress first
        for t in active_tracks:
            if t["status"] != "in-progress":
                continue
            pct = t["task_done"] * 100 // t["task_total"] if t["task_total"] > 0 else 0
            tasks_str = f"{t['task_done']}/{t['task_total']} ({pct}%)"
            print(f"{_status_label(t):<10} {t['id']:<48} {t['type']:<8} {tasks_str:<12} {_fmt_deps(t['dep_met'], t['dep_total'])}")

        # Then pending
        for t in active_tracks:
            if t["status"] != "pending":
                continue
            pct = t["task_done"] * 100 // t["task_total"] if t["task_total"] > 0 else 0
            tasks_str = f"{t['task_done']}/{t['task_total']} ({pct}%)"
            print(f"{_status_label(t):<10} {t['id']:<48} {t['type']:<8} {tasks_str:<12} {_fmt_deps(t['dep_met'], t['dep_total'])}")

        print()

    # --- Current focus ---
    print("-" * 80)
    print("                              CURRENT FOCUS")
    print("-" * 80)
    print()

    has_claimed = False
    # In-progress tracks
    for t in active_tracks:
        if t["status"] != "in-progress":
            continue
        has_claimed = True
        print(f"Claimed: {t['id']}")
        print(f"  Title: {t['title']}")
        print(f"  Progress: {t['task_done']}/{t['task_total']} tasks, {t['phase_done']}/{t['phase_total']} phases")
        if t["next_task"]:
            print(f"  Next: {t['next_task']}")
        print()

    # Branch-claimed pending tracks
    for t in active_tracks:
        if t["status"] != "pending":
            continue
        tid = t["id"]
        if tid not in claimed_ids:
            continue
        has_claimed = True
        worker = claimed_map.get(tid, "")
        worker_info = f" (by {worker})" if worker else ""
        print(f"Claimed{worker_info}: {t['id']}")
        print(f"  Title: {t['title']}")
        print(f"  Progress: {t['task_done']}/{t['task_total']} tasks, {t['phase_done']}/{t['phase_total']} phases")
        if t["next_task"]:
            print(f"  Next: {t['next_task']}")
        print()

    if not has_claimed:
        print("No tracks currently in-progress.")
        print()

    # --- Ready to start ---
    print("-" * 80)
    print("                            READY TO START")
    print("-" * 80)
    print()
    has_ready = False
    for rid in ready_ids:
        if rid in claimed_ids:
            continue
        for t in active_tracks:
            if t["id"] == rid:
                print(f"  {rid} \u2014 {t['title']}")
                has_ready = True
                break
    if not has_ready:
        print("  (none \u2014 all ready tracks are claimed or have unmet dependencies)")
    print()

    # --- Conflict Risk section ---
    conflict_pairs = reg.all_conflict_pairs()
    if conflict_pairs:
        print("-" * 80)
        print("                              CONFLICT RISK")
        print("-" * 80)
        print()
        for pair_key in sorted(conflict_pairs.keys()):
            pdata = conflict_pairs[pair_key]
            crisk = pdata.get("risk", "unknown")
            cnote = pdata.get("note", "")
            if len(cnote) > 80:
                cnote = cnote[:77] + "..."
            print(f"  {pair_key:<60} [{crisk}]")
            if cnote:
                print(f"    {cnote}")
        print()

    # --- Blocked tracks ---
    blocked_tracks = []
    for t in active_tracks:
        if t["status"] != "pending":
            continue
        tid = t["id"]
        if tid in ready_ids:
            continue
        track_deps = reg.get_deps(tid)
        unmet = []
        for dep in track_deps:
            dep_status = reg.get_field(dep, "status") or "unknown"
            if dep_status != "completed":
                unmet.append(f"{dep} ({dep_status})")
        if unmet:
            blocked_tracks.append((tid, ", ".join(unmet)))

    if blocked_tracks:
        print("-" * 80)
        print("                           BLOCKED (unmet deps)")
        print("-" * 80)
        print()
        for tid, reason in blocked_tracks:
            print(f"  {tid}")
            print(f"    waiting on: {reason}")
        print()

    print("=" * 80)
    return 0


def cmd_config(args):
    if not args:
        print("Usage: kf-track config <list|get|set>", file=sys.stderr)
        print("", file=sys.stderr)
        print("  list                   Show all settings with current values and defaults", file=sys.stderr)
        print("  get <key>              Get a single setting value", file=sys.stderr)
        print("  set <key> <value>      Set a setting value", file=sys.stderr)
        return 1

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "list":
        return _config_list()
    elif subcmd == "get":
        return _config_get(rest)
    elif subcmd == "set":
        return _config_set(rest)
    else:
        print("Usage: kf-track config <list|get|set>", file=sys.stderr)
        print("", file=sys.stderr)
        print("  list                   Show all settings with current values and defaults", file=sys.stderr)
        print("  get <key>              Get a single setting value", file=sys.stderr)
        print("  set <key> <value>      Set a setting value", file=sys.stderr)
        return 1


def _config_get_value(key):
    """Read a config value, applying default if missing."""
    default_val = None
    cfg_type = None
    for k, t, d in _CONFIG_SCHEMA:
        if k == key:
            default_val = d
            cfg_type = t
            break

    if cfg_type is None:
        print(f"ERROR: Unknown config key: {key}", file=sys.stderr)
        return None

    if not CONFIG_FILE.exists():
        return default_val

    for line in read_file_lines(CONFIG_FILE):
        if line.startswith(f"{key}:"):
            val = line.split(":", 1)[1].strip()
            return val if val else default_val

    return default_val


def _config_list():
    for k, t, d in _CONFIG_SCHEMA:
        current = _config_get_value(k)
        print(f"{k}: {current} (default: {d})")
    return 0


def _config_get(args):
    if not args:
        print("Usage: kf-track config get <key>", file=sys.stderr)
        return 1
    val = _config_get_value(args[0])
    if val is not None:
        print(val)
    return 0 if val is not None else 1


def _config_set(args):
    if len(args) < 2:
        print("Usage: kf-track config set <key> <value>", file=sys.stderr)
        return 1

    key, value = args[0], args[1]

    # Validate key
    cfg_type = None
    for k, t, d in _CONFIG_SCHEMA:
        if k == key:
            cfg_type = t
            break

    if cfg_type is None:
        print(f"ERROR: Unknown config key: {key}", file=sys.stderr)
        print("Known keys:", file=sys.stderr)
        for k, t, d in _CONFIG_SCHEMA:
            print(f"  {k}", file=sys.stderr)
        return 1

    if cfg_type == "bool" and value not in ("true", "false"):
        print(f"ERROR: {key} must be a boolean (true|false), got: {value}", file=sys.stderr)
        return 1

    # Create config file if needed
    if not CONFIG_FILE.exists():
        write_file(CONFIG_FILE, (
            "# Kiloforge Project Configuration\n"
            "#\n"
            "# SCHEMA:\n"
            "#   primary_branch: string (default: \"main\")\n"
            "#     The branch agents read track state from.\n"
            "#\n"
            "#   enforce_dep_ordering: bool (default: true)\n"
            "#     When true, the work queue scheduler skips tracks with unmet dependencies\n"
            "#     and continues to the next eligible track (drain-loop). When false, tracks\n"
            "#     are popped in order regardless of dependency status.\n"
            "#\n"
            "# Structured project settings used by kf tooling and agent skills.\n"
            "# TOOL: Managed by `kf-track config` and agent skills. Hand-editable.\n"
            "\n"
        ))

    lines = read_file_lines(CONFIG_FILE)
    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}:"):
            new_lines.append(f"{key}: {value}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}: {value}")

    write_file(CONFIG_FILE, "\n".join(new_lines) + "\n")
    print(f"Set {key} = {value}")
    return 0


def cmd_stash(args):
    if not args:
        print("Usage: kf-track stash <list|save|clean> [track-id]", file=sys.stderr)
        return 1

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "list":
        track_id = rest[0] if rest else None
        pattern = f"stash/{track_id}/*" if track_id else "stash/*"
        output = run_git("branch", "--list", pattern)
        if not output:
            print("(no stash branches found)")
            return 0
        branches = [b.strip().lstrip("* ") for b in output.splitlines() if b.strip()]
        if not branches:
            print("(no stash branches found)")
            return 0
        print(f"{'BRANCH':<60} {'TRACK':<30} {'WORKER':<15} DATE")
        for branch in branches:
            parts = branch.split("/")
            track = parts[1] if len(parts) > 1 else ""
            worker = "/".join(parts[2:]) if len(parts) > 2 else ""
            date_str = run_git("log", "-1", "--format=%ci", branch)
            date_str = date_str.split()[0] if date_str else "unknown"
            print(f"{branch:<60} {track:<30} {worker:<15} {date_str}")

    elif subcmd == "save":
        if not rest:
            print("Usage: kf-track stash save <track-id>", file=sys.stderr)
            return 1
        track_id = rest[0]
        worker_name = os.path.basename(os.getcwd())
        if not worker_name.startswith("worker-"):
            worker_name = run_git("branch", "--show-current") or "unknown"
        stash_branch = f"stash/{track_id}/{worker_name}"

        # Check for uncommitted changes
        diff_result = run_git("diff", "--quiet", "HEAD")
        cached_result = run_git("diff", "--cached", "--quiet", "HEAD")
        # If either returns non-empty or fails, there are changes
        try:
            subprocess.run(["git", "diff", "--quiet", "HEAD"], check=True, capture_output=True)
            subprocess.run(["git", "diff", "--cached", "--quiet", "HEAD"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            run_git("add", "-A")
            run_git("commit", "-m", f"wip: auto-stash for {track_id}", "--no-verify")

        run_git("branch", "-f", stash_branch, "HEAD")
        print(f"Stash branch created: {stash_branch}")

    elif subcmd == "clean":
        if not rest:
            print("Usage: kf-track stash clean <track-id>", file=sys.stderr)
            return 1
        track_id = rest[0]
        output = run_git("branch", "--list", f"stash/{track_id}/*")
        if not output:
            print(f"(no stash branches for {track_id})")
            return 0
        branches = [b.strip().lstrip("* ") for b in output.splitlines() if b.strip()]
        count = 0
        for branch in branches:
            result = run_git("branch", "-D", branch)
            if result is not None:
                count += 1
        print(f"Deleted {count} stash branch(es) for {track_id}")

    else:
        print("Usage: kf-track stash <list|save|clean> [track-id]", file=sys.stderr)
        return 1

    return 0


def cmd_help():
    print("""kf-track \u2014 Track registry management for Kiloforge agents

USAGE:
  kf-track <command> [arguments]

COMMANDS:
  add <id> --title "..." [--type ...] [--deps "a,b"]   Add a new track
  update <id> --status <status>                         Update track status
  set <id> --<field> <value>                            Set arbitrary metadata field
  get <id> [--ref <branch|commit>]                       Show track details + deps
  archive <id> [reason]                                 Archive a track

  list [options]                                        List tracks (default: ready)
    --ready           Active tracks with all deps satisfied (default)
    --active          All pending + in-progress tracks
    --status <s>      Filter by exact status
    --all             Show all tracks including completed/archived
    --json            Output as JSON lines
    --ids             Output track IDs only
    --ref <ref>       Read from a git branch or commit

  show <id> [--section spec|plan|extra|header] [--json]   Show track content
  spec <id> [--field F] [--set "val"]                    Read/write spec fields
  plan <id> [--phase N]                                  Show implementation plan
  task <id> <phase.task> --done|--pending                Update task completion
  progress <id> [--json]                                 Show track completion stats
  extra <id> [--key K] [--set "val"|--delete]            Read/write extra metadata
  init <id> --title "..." [--type ...] [--spec-file ...] Create track.yaml
  migrate <id> [--force] [--keep]                        Convert legacy files to track.yaml
  migrate-all [--force] [--keep] [--dry-run]             Convert all legacy tracks

  compact [run] [--dry-run]                              Compact completed/archived tracks
  compact list [--json]                                  Show compaction history
  compact recover <hash>                                 Show recovery commands for a compaction
  compact import <hash> [--source ...] [--note ...]      Import legacy compaction record

  deps add <id> <dep-id>                                Add a dependency
  deps remove <id> <dep-id>                             Remove a dependency
  deps list <id>                                        List dependencies
  deps check <id>                                       Check if all deps satisfied

  conflicts add <a> <b> [risk] [note]                   Add conflict risk pair
  conflicts remove <a> <b>                              Remove conflict pair
  conflicts list [track-id]                             List pairs (filtered)
  conflicts clean                                       Remove pairs for completed tracks

  stash list [track-id]                                 List stash branches
  stash save <track-id>                                 Save current work to stash branch
  stash clean <track-id>                                Delete stash branches for a track

  status [--ref <branch|commit>]                          Full project status report
  index [--ref <branch|commit>]                          Generate project index
  migrate-meta [--dry-run]                               Migrate legacy files to per-track meta.yaml
  config list                                            List all project settings
  config get <key>                                       Get a setting value
  config set <key> <value>                               Set a setting value

  quick-links [show [--ref <ref>]]                       Show quick links
  quick-links add <label> <path>                         Add a quick link
  quick-links remove <label>                             Remove a quick link

STATUS VALUES:
  pending       Track created, not yet started
  in-progress   Track claimed by a developer
  completed     Track implementation done
  archived      Track moved to archive

EXAMPLES:
  kf-track add my-track_20260310Z --title "My Feature" --type feature
  kf-track update my-track_20260310Z --status in-progress
  kf-track list --status pending
  kf-track list --all
  kf-track deps check my-track_20260310Z
  kf-track archive my-track_20260310Z "completed and merged\"""")
    return 0


def cmd_approve(args):
    """Approve one or more tracks for dispatch."""
    track_ids = [a for a in args if not a.startswith("-")]
    if not track_ids:
        print("Usage: kf-track approve <track-id> [track-id ...]", file=sys.stderr)
        return 1
    reg = _get_registry()
    for tid in track_ids:
        if not reg.exists(tid):
            print(f"ERROR: Track not found: {tid}", file=sys.stderr)
            return 1
        reg.set_field(tid, "approved", True)
        print(f"Approved: {tid}")
    reg.save(track_ids=track_ids)
    return 0


def cmd_disapprove(args):
    """Disapprove (revoke approval) for one or more tracks."""
    track_ids = [a for a in args if not a.startswith("-")]
    if not track_ids:
        print("Usage: kf-track disapprove <track-id> [track-id ...]", file=sys.stderr)
        return 1
    reg = _get_registry()
    for tid in track_ids:
        if not reg.exists(tid):
            print(f"ERROR: Track not found: {tid}", file=sys.stderr)
            return 1
        reg.set_field(tid, "approved", False)
        print(f"Disapproved: {tid}")
    reg.save(track_ids=track_ids)
    return 0


def cmd_migrate_meta(args):
    """Migrate from legacy centralized files to per-track meta.yaml."""
    dry_run = "--dry-run" in args

    legacy = TracksRegistry.from_legacy(TRACKS_FILE, DEPS_FILE, CONFLICTS_FILE)
    entries = legacy.all_entries()

    if not entries:
        print("Nothing to migrate.")
        return 0

    print(f"Migrating {len(entries)} track(s) to per-track meta.yaml...")

    for tid, data in sorted(entries.items()):
        meta_path = TRACKS_DIR / tid / "meta.yaml"
        if meta_path.exists():
            print(f"  SKIP {tid} (meta.yaml already exists)")
            continue
        if dry_run:
            print(f"  WOULD CREATE {tid}/meta.yaml")
        else:
            legacy.tracks_dir = TRACKS_DIR
            legacy.save(track_ids=[tid])
            print(f"  CREATED {tid}/meta.yaml")

    if dry_run:
        print("(dry run \u2014 no changes made)")
    else:
        print("Migration complete. Legacy files can be removed after verification.")
    return 0


# --- Main dispatch ---
def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "help"
    rest = args[1:] if len(args) > 1 else []

    dispatch = {
        "add": lambda: cmd_add(rest),
        "update": lambda: cmd_update(rest),
        "set": lambda: cmd_set(rest),
        "get": lambda: cmd_get(rest),
        "list": lambda: cmd_list(rest),
        "approve": lambda: cmd_approve(rest),
        "disapprove": lambda: cmd_disapprove(rest),
        "archive": lambda: cmd_archive(rest),
        "compact": lambda: cmd_compact(rest),
        "deps": lambda: cmd_deps(rest),
        "conflicts": lambda: cmd_conflicts(rest),
        "stash": lambda: cmd_stash(rest),
        "index": lambda: cmd_index(rest),
        "quick-links": lambda: cmd_quick_links(rest),
        "status": lambda: cmd_status(rest),
        "config": lambda: cmd_config(rest),
        "migrate-meta": lambda: cmd_migrate_meta(rest),
        "help": lambda: cmd_help(),
        "--help": lambda: cmd_help(),
        "-h": lambda: cmd_help(),
    }

    content_cmds = {"show", "spec", "plan", "task", "progress", "extra", "init", "migrate", "migrate-all"}

    if cmd in dispatch:
        rc = dispatch[cmd]()
        sys.exit(rc or 0)
    elif cmd in content_cmds:
        cmd_content(cmd, rest)
    else:
        print(f"Unknown command: {cmd}. Run 'kf-track help' for usage.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
