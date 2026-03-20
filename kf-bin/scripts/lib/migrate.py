"""Kiloforge project metadata migration framework.

Each project stores a meta_version file in .agent/kf/ that tracks which
migrations have been applied. On preflight or update, pending migrations
run automatically.

Migration functions receive the kf_dir Path and transform metadata in place.
They must be idempotent — safe to re-run if interrupted.

Historical parsers for old formats are preserved in lib/tracks.py
(from_legacy, from_ref_legacy, load_compacted_tracks) for reading
compacted archives that can never be migrated.
"""

import json
import shutil
from pathlib import Path
from typing import Optional


def get_meta_version(kf_dir: Path) -> int:
    """Read the current metadata version for a project."""
    version_file = kf_dir / "meta_version"
    if version_file.exists():
        try:
            return int(version_file.read_text().strip())
        except (ValueError, OSError):
            return 0
    return 0


def set_meta_version(kf_dir: Path, version: int):
    """Write the metadata version file."""
    version_file = kf_dir / "meta_version"
    version_file.write_text(f"{version}\n")


def run_pending_migrations(kf_dir: Path, dry_run: bool = False) -> list[str]:
    """Run all pending migrations for a project.

    Returns list of migration descriptions that were applied.
    """
    current = get_meta_version(kf_dir)
    applied = []

    for version, name, description, fn in MIGRATIONS:
        if version <= current:
            continue

        if dry_run:
            applied.append(f"[DRY RUN] v{version}: {description}")
            continue

        print(f"  Migrating v{version}: {description}...")
        try:
            fn(kf_dir)
            set_meta_version(kf_dir, version)
            applied.append(f"v{version}: {description}")
        except Exception as e:
            print(f"  ERROR in migration v{version}: {e}")
            # Stop on first failure — don't skip migrations
            break

    return applied


def latest_version() -> int:
    """Return the latest available migration version."""
    if not MIGRATIONS:
        return 0
    return MIGRATIONS[-1][0]


# ── Migration v1: Per-track meta.yaml ────────────────────────────────────────

def migrate_001_per_track_meta(kf_dir: Path):
    """Migrate from centralized tracks.yaml + deps.yaml + conflicts.yaml
    to per-track meta.yaml files.

    - Reads tracks.yaml, deps.yaml, conflicts.yaml
    - Creates {trackId}/meta.yaml for each track
    - Removes the legacy files
    - Idempotent: skips tracks that already have meta.yaml
    """
    tracks_file = kf_dir / "tracks.yaml"
    deps_file = kf_dir / "tracks" / "deps.yaml"
    conflicts_file = kf_dir / "tracks" / "conflicts.yaml"
    tracks_dir = kf_dir / "tracks"

    # Check if there's anything to migrate
    has_legacy = tracks_file.exists()
    if not has_legacy:
        return  # nothing to do

    # Import here to avoid circular deps at module level
    from lib.tracks import TracksRegistry

    # Load from legacy files
    reg = TracksRegistry.from_legacy(
        tracks_file,
        deps_file if deps_file.exists() else None,
        conflicts_file if conflicts_file.exists() else None,
    )

    if not reg.ids():
        # Empty registry — just clean up
        _remove_legacy_files(tracks_file, deps_file, conflicts_file)
        return

    # Write per-track meta.yaml for each track
    reg.tracks_dir = tracks_dir
    migrated = 0
    skipped = 0
    for tid in reg.ids():
        meta_path = tracks_dir / tid / "meta.yaml"
        if meta_path.exists():
            skipped += 1
            continue
        reg.save(track_ids=[tid])
        migrated += 1

    if migrated > 0:
        print(f"    Migrated {migrated} track(s) to meta.yaml")
    if skipped > 0:
        print(f"    Skipped {skipped} track(s) (meta.yaml already exists)")

    # Remove legacy files
    _remove_legacy_files(tracks_file, deps_file, conflicts_file)


def _remove_legacy_files(tracks_file: Path, deps_file: Path,
                         conflicts_file: Path):
    """Remove legacy centralized state files."""
    for f in [tracks_file, deps_file, conflicts_file]:
        if f.exists():
            f.unlink()
            print(f"    Removed {f.name}")


# ── Migration v2: Spec directory + empty spec.yaml ───────────────────────────

def migrate_002_spec_init(kf_dir: Path):
    """Ensure spec.yaml and spec/ directory exist.

    - Creates .agent/kf/spec/ if missing
    - Creates empty .agent/kf/spec.yaml if missing
    - Idempotent: does nothing if already present
    """
    spec_dir = kf_dir / "spec"
    spec_file = kf_dir / "spec.yaml"

    if not spec_dir.exists():
        spec_dir.mkdir(parents=True, exist_ok=True)
        print("    Created spec/ directory")

    if not spec_file.exists():
        from lib.spec import SpecSnapshot
        snap = SpecSnapshot()
        snap.save(spec_file)
        print("    Created empty spec.yaml")


# ── Migration v3: Remove .agent/kf/bin/ (scripts moved to ~/.kf/) ───────────

def migrate_003_remove_local_bin(kf_dir: Path):
    """Remove the per-project bin/ directory now that scripts are global.

    - Removes .agent/kf/bin/ entirely
    - Idempotent: does nothing if already removed
    """
    bin_dir = kf_dir / "bin"
    if bin_dir.exists():
        shutil.rmtree(bin_dir)
        print("    Removed .agent/kf/bin/ (scripts now at ~/.kf/bin/)")

    # Also remove the per-project .venv
    venv_dir = kf_dir / ".venv"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
        print("    Removed .agent/kf/.venv (venv now at ~/.kf/.venv/)")


# ── Migration v4: Compaction tarballs ─────────────────────────────────────────

def migrate_004_compaction_tarballs(kf_dir: Path):
    """Convert git-history compactions to tarball archives.

    Reads compactions.yaml, extracts tracks from git history,
    creates tarballs in _compacted/, removes compactions.yaml.
    """
    import json as _json
    import subprocess
    import tarfile
    import tempfile
    import secrets
    from datetime import datetime, timezone

    import yaml

    compactions_file = kf_dir / "compactions.yaml"
    tracks_dir = kf_dir / "tracks"

    if not compactions_file.exists():
        return  # nothing to migrate

    # Parse compactions.yaml: each line is "<commit>: {json}"
    compaction_records = []
    for line in compactions_file.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        try:
            ci = line.index(":")
            commit = line[:ci].strip()
            jstr = line[ci + 1:].strip()
            data = _json.loads(jstr)
            track_ids = data.get("track_ids", [])
            if commit and track_ids:
                compaction_records.append((commit, data, track_ids))
        except (ValueError, _json.JSONDecodeError):
            continue

    if not compaction_records:
        # No valid records, just clean up the file
        compactions_file.unlink()
        print("    Removed empty compactions.yaml")
        return

    compacted_dir = tracks_dir / "_compacted"
    compacted_dir.mkdir(parents=True, exist_ok=True)
    converted = 0

    for commit, record_data, track_ids in compaction_records:
        # Try to extract track files from git history
        tmp_dir = Path(tempfile.mkdtemp(prefix="kf-migrate-"))
        any_extracted = False

        try:
            for tid in track_ids:
                track_tmp = tmp_dir / tid
                track_tmp.mkdir(parents=True, exist_ok=True)

                # Try meta.yaml first
                for fname in ("meta.yaml", "track.yaml", "spec.md"):
                    result = subprocess.run(
                        ["git", "show",
                         f"{commit}:.agent/kf/tracks/{tid}/{fname}"],
                        capture_output=True, text=True, check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        (track_tmp / fname).write_text(result.stdout)
                        any_extracted = True

                # Also try _archive path
                if not (track_tmp / "meta.yaml").exists():
                    result = subprocess.run(
                        ["git", "show",
                         f"{commit}:.agent/kf/tracks/_archive/{tid}/meta.yaml"],
                        capture_output=True, text=True, check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        (track_tmp / "meta.yaml").write_text(result.stdout)
                        any_extracted = True

                # Fall back to legacy tracks.yaml entry
                if not (track_tmp / "meta.yaml").exists():
                    legacy_text = subprocess.run(
                        ["git", "show",
                         f"{commit}:.agent/kf/tracks.yaml"],
                        capture_output=True, text=True, check=False,
                    )
                    if legacy_text.returncode == 0 and legacy_text.stdout.strip():
                        for lline in legacy_text.stdout.splitlines():
                            if not lline or lline.startswith("#"):
                                continue
                            try:
                                lci = lline.index(":")
                                ltid = lline[:lci].strip()
                                ljstr = lline[lci + 1:].strip()
                                if ltid == tid:
                                    ldata = _json.loads(ljstr)
                                    ldata.setdefault("deps", [])
                                    ldata.setdefault("conflicts", [])
                                    (track_tmp / "meta.yaml").write_text(
                                        yaml.dump(ldata,
                                                  default_flow_style=False,
                                                  sort_keys=False))
                                    any_extracted = True
                            except (ValueError, _json.JSONDecodeError):
                                continue

            if not any_extracted:
                print(f"    SKIP compaction {commit[:10]}: "
                      f"no track data recoverable from git history")
                continue

            # Create tarball
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
            h = secrets.token_hex(3)
            basename = f"{ts}-{h}"
            tarball_path = compacted_dir / f"{basename}.tar.gz"
            index_path = compacted_dir / f"{basename}.json"

            with tarfile.open(tarball_path, "w:gz") as tar:
                for tid in track_ids:
                    track_tmp = tmp_dir / tid
                    if track_tmp.is_dir() and any(track_tmp.iterdir()):
                        tar.add(str(track_tmp), arcname=tid)

            # Create JSON index from original record data
            index_data = {
                "date": record_data.get("date", ""),
                "track_ids": track_ids,
                "track_count": len(track_ids),
                "completed": record_data.get("completed", 0),
                "archived": record_data.get("archived", 0),
                "first_created": record_data.get("first_created", ""),
                "last_created": record_data.get("last_created", ""),
                "migrated_from_commit": commit,
            }
            if record_data.get("source"):
                index_data["source"] = record_data["source"]

            index_path.write_text(
                _json.dumps(index_data, indent=2) + "\n")
            converted += 1

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # Remove compactions.yaml
    compactions_file.unlink()
    print(f"    Converted {converted} compaction(s) to tarballs")
    print("    Removed compactions.yaml")


# ── Migration registry ───────────────────────────────────────────────────────
# (version, name, description, function)

MIGRATIONS = [
    (1, "per_track_meta",
     "Migrate tracks.yaml/deps.yaml/conflicts.yaml to per-track meta.yaml",
     migrate_001_per_track_meta),
    (2, "spec_init",
     "Initialize spec.yaml and spec/ directory",
     migrate_002_spec_init),
    (3, "remove_local_bin",
     "Remove per-project bin/ and .venv (moved to ~/.kf/)",
     migrate_003_remove_local_bin),
    (4, "compaction_tarballs",
     "Convert git-history compactions to tarball archives",
     migrate_004_compaction_tarballs),
]
