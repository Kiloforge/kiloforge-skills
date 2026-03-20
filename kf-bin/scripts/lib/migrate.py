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
]
