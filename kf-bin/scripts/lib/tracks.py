"""Track registry management via per-track meta.yaml files.

Each track stores its metadata in .agent/kf/tracks/{trackId}/meta.yaml.
This eliminates contention on shared state files when multiple workers
operate simultaneously.

meta.yaml format:
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
    archived_at: "2026-03-21"       # optional
    archive_reason: "completed"     # optional

Legacy format (tracks.yaml line-per-track JSON) is supported for reading
via from_legacy() but is not written by new code.
"""

import json
import subprocess
import yaml
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Field ordering for stable YAML output ────────────────────────────────────

META_FIELDS = ["title", "status", "type", "approved", "created", "updated"]
META_FIELDS_OPTIONAL = ["archived_at", "archive_reason"]
META_FIELDS_LISTS = ["deps", "conflicts"]

# Legacy tracks.yaml support
LEGACY_CANONICAL = ["title", "status", "type", "created", "updated"]
LEGACY_OPTIONAL = ["archived_at", "archive_reason"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ordered_meta(data: dict) -> dict:
    """Return a new dict with fields in canonical order for stable YAML."""
    out = {}
    for key in META_FIELDS:
        if key in data:
            out[key] = data[key]
    for key in META_FIELDS_LISTS:
        if key in data and data[key]:
            out[key] = data[key]
    for key in META_FIELDS_OPTIONAL:
        if key in data:
            out[key] = data[key]
    # Any extra fields not in the canonical lists
    for key in data:
        if key not in out:
            out[key] = data[key]
    return out


def _dump_meta(data: dict) -> str:
    """Serialize track metadata to YAML with canonical field order."""
    ordered = _ordered_meta(data)
    return yaml.dump(ordered, default_flow_style=False, sort_keys=False,
                     allow_unicode=True)


def _run_git(*args, check=False) -> str:
    """Run a git command and return stdout. Returns '' on failure."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, check=check
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _conflict_pair_key(id_a: str, id_b: str) -> str:
    """Canonical pair key: lower/higher alphabetically."""
    return f"{min(id_a, id_b)}/{max(id_a, id_b)}"


def normalize_json(data: dict) -> str:
    """Serialize track data as JSON with canonical field order (legacy compat)."""
    ordered = {}
    for key in LEGACY_CANONICAL:
        if key in data:
            ordered[key] = data[key]
    for key in LEGACY_OPTIONAL:
        if key in data:
            ordered[key] = data[key]
    for key in data:
        if key not in ordered:
            ordered[key] = data[key]
    return json.dumps(ordered, separators=(",", ":"))


# ── TracksRegistry ───────────────────────────────────────────────────────────

class TracksRegistry:
    """Read/write interface for per-track meta.yaml files.

    Each track's metadata lives in .agent/kf/tracks/{trackId}/meta.yaml.
    The registry scans these files to build an in-memory view, supports
    mutations, and writes back only the changed tracks.

    For reading from a git ref (e.g., primary branch), use from_ref()
    which batch-reads all meta.yaml files in 2 git calls.

    Falls back to legacy tracks.yaml if no meta.yaml files are found.
    """

    def __init__(self, tracks_dir: Path):
        """Load from a filesystem directory (concurrent YAML parsing)."""
        self.tracks_dir = tracks_dir
        self._entries: dict[str, dict] = {}
        self._dirty: set[str] = set()
        if self.tracks_dir and self.tracks_dir.exists():
            self._scan_fs()

    @classmethod
    def from_ref(cls, ref: str,
                 tracks_dir_rel: str = ".agent/kf/tracks") -> "TracksRegistry":
        """Load track metadata from a git ref using batch operations.

        Uses git ls-tree (1 call) to enumerate track directories, then
        git cat-file --batch (1 call) to read all meta.yaml files
        concurrently. Total: 2 git subprocess calls regardless of track count.
        """
        reg = cls.__new__(cls)
        reg.tracks_dir = None
        reg._entries = {}
        reg._dirty = set()

        # Step 1: list all entries under tracks/
        ls_output = _run_git("ls-tree", "--name-only", ref,
                             f"{tracks_dir_rel}/")
        if not ls_output.strip():
            # Try legacy fallback
            return cls._from_ref_legacy(ref, tracks_dir_rel)

        track_ids = []
        for line in ls_output.strip().splitlines():
            name = line.rstrip("/").rsplit("/", 1)[-1]
            if not name.startswith("_"):
                track_ids.append(name)

        if not track_ids:
            return reg

        # Step 2: batch-read all meta.yaml via git cat-file --batch
        object_specs = "\n".join(
            f"{ref}:{tracks_dir_rel}/{tid}/meta.yaml"
            for tid in track_ids
        ) + "\n"

        result = subprocess.run(
            ["git", "cat-file", "--batch"],
            input=object_specs,
            capture_output=True, text=True, check=False,
        )

        if result.returncode != 0:
            return cls._from_ref_legacy(ref, tracks_dir_rel)

        # Step 3: parse batch output
        output = result.stdout
        pos = 0
        has_any_meta = False
        for tid in track_ids:
            # Find header line ending with newline
            nl = output.find("\n", pos)
            if nl == -1:
                break
            header = output[pos:nl]
            pos = nl + 1

            if "missing" in header:
                continue

            # Parse size from "<sha> <type> <size>"
            parts = header.split()
            if len(parts) < 3:
                continue
            try:
                size = int(parts[2])
            except (ValueError, IndexError):
                continue

            content = output[pos:pos + size]
            pos += size
            # Skip trailing newline after content
            if pos < len(output) and output[pos] == "\n":
                pos += 1

            try:
                data = yaml.safe_load(content)
                if isinstance(data, dict):
                    reg._entries[tid] = data
                    has_any_meta = True
            except yaml.YAMLError:
                pass

        # If no meta.yaml found, fall back to legacy
        if not has_any_meta:
            return cls._from_ref_legacy(ref, tracks_dir_rel)

        return reg

    @classmethod
    def _from_ref_legacy(cls, ref: str,
                         tracks_dir_rel: str) -> "TracksRegistry":
        """Fallback: load from legacy tracks.yaml + deps.yaml via git show."""
        reg = cls.__new__(cls)
        reg.tracks_dir = None
        reg._entries = {}
        reg._dirty = set()

        kf_dir_rel = str(Path(tracks_dir_rel).parent)

        # Read legacy tracks.yaml
        tracks_text = _run_git("show", f"{ref}:{kf_dir_rel}/tracks.yaml")
        if not tracks_text.strip():
            return reg

        for line in tracks_text.strip().splitlines():
            if not line or line.startswith("#"):
                continue
            try:
                colon_idx = line.index(":")
                track_id = line[:colon_idx].strip()
                json_str = line[colon_idx + 1:].strip()
                data = json.loads(json_str)
                # Initialize deps/conflicts lists
                if "deps" not in data:
                    data["deps"] = []
                if "conflicts" not in data:
                    data["conflicts"] = []
                reg._entries[track_id] = data
            except (ValueError, json.JSONDecodeError):
                continue

        # Read legacy deps.yaml
        deps_text = _run_git("show",
                             f"{ref}:{tracks_dir_rel}/deps.yaml")
        if deps_text.strip():
            try:
                # Strip header comments
                data_lines = []
                for dline in deps_text.splitlines():
                    if not dline.startswith("#") and dline.strip():
                        data_lines.append(dline)
                deps_data = yaml.safe_load("\n".join(data_lines))
                if isinstance(deps_data, dict):
                    for tid, dep_list in deps_data.items():
                        if tid in reg._entries:
                            reg._entries[tid]["deps"] = dep_list or []
            except yaml.YAMLError:
                pass

        # Read legacy conflicts.yaml
        conflicts_text = _run_git("show",
                                  f"{ref}:{tracks_dir_rel}/conflicts.yaml")
        if conflicts_text.strip():
            for cline in conflicts_text.strip().splitlines():
                if not cline or cline.startswith("#"):
                    continue
                try:
                    ci = cline.index(":")
                    pair = cline[:ci].strip()
                    cjson = cline[ci + 1:].strip()
                    cdata = json.loads(cjson)
                    parts = pair.split("/")
                    if len(parts) == 2:
                        id_a, id_b = parts
                        entry = {
                            "risk": cdata.get("risk", "medium"),
                            "note": cdata.get("note", ""),
                            "added": cdata.get("added", ""),
                        }
                        if id_a in reg._entries:
                            reg._entries[id_a].setdefault("conflicts", [])
                            reg._entries[id_a]["conflicts"].append(
                                {"peer": id_b, **entry})
                        if id_b in reg._entries:
                            reg._entries[id_b].setdefault("conflicts", [])
                            reg._entries[id_b]["conflicts"].append(
                                {"peer": id_a, **entry})
                except (ValueError, json.JSONDecodeError):
                    continue

        return reg

    @classmethod
    def from_legacy(cls, tracks_file: Path,
                    deps_file: Optional[Path] = None,
                    conflicts_file: Optional[Path] = None) -> "TracksRegistry":
        """Load from legacy centralized files (for migration)."""
        reg = cls.__new__(cls)
        reg.tracks_dir = None
        reg._entries = {}
        reg._dirty = set()

        # Parse tracks.yaml
        if tracks_file.exists():
            for line in tracks_file.read_text().splitlines():
                if not line or line.startswith("#"):
                    continue
                try:
                    ci = line.index(":")
                    tid = line[:ci].strip()
                    jstr = line[ci + 1:].strip()
                    data = json.loads(jstr)
                    data.setdefault("deps", [])
                    data.setdefault("conflicts", [])
                    reg._entries[tid] = data
                except (ValueError, json.JSONDecodeError):
                    continue

        # Parse deps.yaml
        if deps_file and deps_file.exists():
            text = deps_file.read_text()
            data_lines = [l for l in text.splitlines()
                          if l.strip() and not l.startswith("#")]
            try:
                deps_data = yaml.safe_load("\n".join(data_lines))
                if isinstance(deps_data, dict):
                    for tid, dep_list in deps_data.items():
                        if tid in reg._entries:
                            reg._entries[tid]["deps"] = dep_list or []
            except yaml.YAMLError:
                pass

        # Parse conflicts.yaml
        if conflicts_file and conflicts_file.exists():
            for cline in conflicts_file.read_text().splitlines():
                if not cline or cline.startswith("#"):
                    continue
                try:
                    ci = cline.index(":")
                    pair = cline[:ci].strip()
                    cjson = cline[ci + 1:].strip()
                    cdata = json.loads(cjson)
                    parts = pair.split("/")
                    if len(parts) == 2:
                        id_a, id_b = parts
                        entry = {
                            "risk": cdata.get("risk", "medium"),
                            "note": cdata.get("note", ""),
                            "added": cdata.get("added", ""),
                        }
                        if id_a in reg._entries:
                            reg._entries[id_a]["conflicts"].append(
                                {"peer": id_b, **entry})
                        if id_b in reg._entries:
                            reg._entries[id_b]["conflicts"].append(
                                {"peer": id_a, **entry})
                except (ValueError, json.JSONDecodeError):
                    continue

        return reg

    def _scan_fs(self):
        """Scan tracks_dir for {trackId}/meta.yaml files (concurrent)."""
        self._entries = {}
        meta_files = list(self.tracks_dir.glob("*/meta.yaml"))
        meta_files = [f for f in meta_files
                      if not f.parent.name.startswith("_")]

        if not meta_files:
            # Fallback: try legacy tracks.yaml
            self._scan_fs_legacy()
            return

        def _read_one(path: Path):
            try:
                data = yaml.safe_load(path.read_text())
                if isinstance(data, dict):
                    return (path.parent.name, data)
            except Exception:
                pass
            return None

        workers = min(16, len(meta_files))
        if workers <= 1:
            for f in meta_files:
                result = _read_one(f)
                if result:
                    self._entries[result[0]] = result[1]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for result in pool.map(_read_one, meta_files):
                    if result:
                        self._entries[result[0]] = result[1]

    def _scan_fs_legacy(self):
        """Fallback: load from legacy tracks.yaml in parent directory."""
        if not self.tracks_dir:
            return
        kf_dir = self.tracks_dir.parent
        tracks_file = kf_dir / "tracks.yaml"
        deps_file = self.tracks_dir / "deps.yaml"
        conflicts_file = self.tracks_dir / "conflicts.yaml"

        if not tracks_file.exists():
            return

        legacy = TracksRegistry.from_legacy(tracks_file, deps_file,
                                            conflicts_file)
        self._entries = legacy._entries

    # ── Save ─────────────────────────────────────────────────────────────

    def save(self, track_ids: Optional[list[str]] = None):
        """Write meta.yaml for specified tracks (or all dirty tracks).

        Only writes to tracks that have been modified. Each track's
        meta.yaml is an independent file, so concurrent saves to
        different tracks are safe.
        """
        if self.tracks_dir is None:
            raise RuntimeError("Cannot save: registry was loaded from a git "
                               "ref (read-only). Load from filesystem instead.")

        ids_to_save = track_ids or list(self._dirty)
        for tid in ids_to_save:
            if tid not in self._entries:
                continue
            meta_path = self.tracks_dir / tid / "meta.yaml"
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(_dump_meta(self._entries[tid]))

        # Clear dirty flags for saved tracks
        for tid in ids_to_save:
            self._dirty.discard(tid)

    # ── Query methods ────────────────────────────────────────────────────

    def exists(self, track_id: str) -> bool:
        return track_id in self._entries

    def get(self, track_id: str) -> Optional[dict]:
        return self._entries.get(track_id)

    def get_field(self, track_id: str, field: str):
        entry = self._entries.get(track_id)
        if entry is None:
            return None
        return entry.get(field)

    def all_entries(self) -> dict[str, dict]:
        return dict(self._entries)

    def ids(self) -> list[str]:
        return list(self._entries.keys())

    def list_by_status(self, *statuses: str) -> dict[str, dict]:
        return {
            tid: data for tid, data in self._entries.items()
            if data.get("status") in statuses
        }

    def list_active(self) -> dict[str, dict]:
        return self.list_by_status("pending", "in-progress")

    # ── Mutation methods ─────────────────────────────────────────────────

    def add(self, track_id: str, title: str, type_: str = "feature",
            status: str = "pending", deps: Optional[list[str]] = None,
            approved: bool = False) -> dict:
        if track_id in self._entries:
            raise ValueError(f"Track already exists: {track_id}")
        created = today_iso()
        entry = {
            "title": title,
            "status": status,
            "type": type_,
            "approved": approved,
            "created": created,
            "updated": created,
        }
        if deps:
            entry["deps"] = list(deps)
        self._entries[track_id] = entry
        self._dirty.add(track_id)
        return entry

    def set_field(self, track_id: str, field: str, value):
        entry = self._entries.get(track_id)
        if entry is None:
            raise KeyError(f"Track not found: {track_id}")
        entry[field] = value
        if field != "updated":
            entry["updated"] = today_iso()
        self._dirty.add(track_id)

    def update_status(self, track_id: str, status: str):
        valid = ("pending", "in-progress", "completed", "archived")
        if status not in valid:
            raise ValueError(
                f"Invalid status: {status} (must be {', '.join(valid)})")
        self.set_field(track_id, "status", status)
        if status == "archived":
            self.set_field(track_id, "archived_at", today_iso())

    def remove(self, track_id: str):
        """Remove a track from the registry (in-memory only)."""
        self._entries.pop(track_id, None)
        self._dirty.discard(track_id)

    # ── Deps methods ─────────────────────────────────────────────────────

    def get_deps(self, track_id: str) -> list[str]:
        entry = self._entries.get(track_id)
        if entry is None:
            return []
        return list(entry.get("deps") or [])

    def add_dep(self, track_id: str, dep_id: str):
        entry = self._entries.get(track_id)
        if entry is None:
            raise KeyError(f"Track not found: {track_id}")
        deps = entry.setdefault("deps", [])
        if dep_id not in deps:
            deps.append(dep_id)
            self._dirty.add(track_id)

    def remove_dep(self, track_id: str, dep_id: str):
        entry = self._entries.get(track_id)
        if entry is None:
            return
        deps = entry.get("deps", [])
        if dep_id in deps:
            deps.remove(dep_id)
            self._dirty.add(track_id)

    def deps_satisfied(self, track_id: str) -> bool:
        """Check if all dependencies are completed (or no deps)."""
        deps = self.get_deps(track_id)
        if not deps:
            return True
        for dep_id in deps:
            dep_status = self.get_field(dep_id, "status")
            if dep_status != "completed":
                return False
        return True

    def dep_summary(self, track_id: str) -> str:
        """Return 'N/M met' summary or '-' for no deps."""
        deps = self.get_deps(track_id)
        if not deps:
            return "-"
        met = sum(1 for d in deps
                  if self.get_field(d, "status") == "completed")
        total = len(deps)
        check = " \u2713" if met == total else ""
        return f"{met}/{total}{check}"

    def all_deps(self) -> dict[str, list[str]]:
        """Return full dependency graph: {track_id: [dep_ids]}."""
        return {tid: list(data.get("deps") or [])
                for tid, data in self._entries.items()
                if data.get("deps")}

    # ── Conflicts methods ────────────────────────────────────────────────

    def get_conflicts(self, track_id: str) -> list[dict]:
        entry = self._entries.get(track_id)
        if entry is None:
            return []
        return list(entry.get("conflicts") or [])

    def add_conflict(self, id_a: str, id_b: str,
                     risk: str = "medium", note: str = ""):
        """Add a conflict pair — writes to BOTH tracks."""
        if id_a == id_b:
            raise ValueError("Cannot create conflict pair with itself")
        today = today_iso()
        # Add to id_a
        entry_a = self._entries.get(id_a)
        if entry_a is not None:
            conflicts = entry_a.setdefault("conflicts", [])
            # Remove existing entry for this peer if any
            conflicts[:] = [c for c in conflicts if c.get("peer") != id_b]
            conflicts.append({"peer": id_b, "risk": risk, "note": note,
                              "added": today})
            self._dirty.add(id_a)
        # Add to id_b
        entry_b = self._entries.get(id_b)
        if entry_b is not None:
            conflicts = entry_b.setdefault("conflicts", [])
            conflicts[:] = [c for c in conflicts if c.get("peer") != id_a]
            conflicts.append({"peer": id_a, "risk": risk, "note": note,
                              "added": today})
            self._dirty.add(id_b)

    def remove_conflict(self, id_a: str, id_b: str):
        """Remove a conflict pair from both tracks."""
        entry_a = self._entries.get(id_a)
        if entry_a and entry_a.get("conflicts"):
            entry_a["conflicts"] = [
                c for c in entry_a["conflicts"] if c.get("peer") != id_b]
            self._dirty.add(id_a)
        entry_b = self._entries.get(id_b)
        if entry_b and entry_b.get("conflicts"):
            entry_b["conflicts"] = [
                c for c in entry_b["conflicts"] if c.get("peer") != id_a]
            self._dirty.add(id_b)

    def clean_conflicts(self, track_id: str) -> list[str]:
        """Remove all conflict entries involving track_id.

        Clears the track's own conflicts list. Peer tracks' lists are
        NOT modified to avoid cross-track writes during concurrent
        operations. Consumers should filter by checking both sides
        are still active.

        Returns list of peer track IDs whose conflicts were cleaned.
        """
        entry = self._entries.get(track_id)
        if not entry or not entry.get("conflicts"):
            return []
        peers = [c.get("peer") for c in entry["conflicts"] if c.get("peer")]
        entry["conflicts"] = []
        self._dirty.add(track_id)
        return peers

    def all_conflict_pairs(self) -> dict[str, dict]:
        """Return deduplicated conflict pairs: {pair_key: {risk, note, added}}.

        Only includes pairs where BOTH tracks are active (pending/in-progress).
        Pair key is canonical: lower_id/higher_id.
        """
        active = self.list_active()
        pairs = {}
        for tid, data in self._entries.items():
            for c in (data.get("conflicts") or []):
                peer = c.get("peer")
                if not peer:
                    continue
                # Only include if both sides are active
                if tid not in active or peer not in active:
                    continue
                key = _conflict_pair_key(tid, peer)
                if key not in pairs:
                    pairs[key] = {
                        "risk": c.get("risk", "medium"),
                        "note": c.get("note", ""),
                        "added": c.get("added", ""),
                    }
        return pairs

    # ── Migration helpers ────────────────────────────────────────────────

    def write_legacy_index(self, index_path: Path):
        """Write a legacy tracks.yaml index from current entries.

        This is a convenience for backward compatibility. The generated
        file is NOT the source of truth — per-track meta.yaml files are.
        """
        lines = [
            "# Kiloforge Track Registry",
            "#",
            "# NOTE: This file is auto-generated from per-track meta.yaml files.",
            "#       It is NOT the source of truth. Use `kf-track` to manage entries.",
            "#",
        ]
        if self._entries:
            lines.append("")
            for tid in sorted(self._entries.keys()):
                data = self._entries[tid]
                # Only include registry fields (not deps/conflicts)
                reg_data = {k: v for k, v in data.items()
                            if k not in ("deps", "conflicts")}
                lines.append(f"{tid}: {normalize_json(reg_data)}")
        index_path.write_text("\n".join(lines) + "\n")


# ── Compacted track recovery ─────────────────────────────────────────────

def load_compacted_tracks(compactions_dir: Optional[Path] = None,
                          tracks_dir: Optional[Path] = None,
                          ) -> dict[str, dict]:
    """Load compacted track metadata from tarball archives.

    Args:
        compactions_dir: Direct path to _compacted/ dir. If None, derived
                         from tracks_dir.
        tracks_dir: The .agent/kf/tracks/ directory. Used to derive
                    compactions_dir if not provided directly.

    Returns:
        Dict of {track_id: track_meta_dict} for all compacted tracks.
    """
    from lib.compaction import load_all_compacted_tracks
    if compactions_dir is None and tracks_dir is not None:
        compactions_dir = tracks_dir / "_compacted"
    if compactions_dir is None:
        return {}
    # load_all_compacted_tracks expects the tracks dir (parent of _compacted)
    parent = compactions_dir.parent
    return load_all_compacted_tracks(parent)
