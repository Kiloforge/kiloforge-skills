"""Compaction management — compressed archives of completed/archived tracks.

Compacted tracks are stored as:
  .agent/kf/tracks/_compacted/{timestamp}-{hash}.tar.gz  — track directories
  .agent/kf/tracks/_compacted/{timestamp}-{hash}.json    — metadata index

The JSON index contains:
  {
    "date": "2026-03-21",
    "track_ids": ["track_a", "track_b"],
    "track_count": 2,
    "completed": 1,
    "archived": 1,
    "first_created": "2026-03-10",
    "last_created": "2026-03-20"
  }

Each tarball contains the full track directories (meta.yaml, track.yaml, etc.)
at the time of compaction. These can be extracted, migrated to new formats,
and re-compacted — unlike git-history-based recovery.
"""

import io
import json
import secrets
import shutil
import tarfile
import tempfile
import yaml
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _compacted_dir(tracks_dir: Path) -> Path:
    """Return the _compacted/ directory, creating it if needed."""
    d = tracks_dir / "_compacted"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_basename() -> str:
    """Generate a compaction filename base: YYYYMMDD-HHMMSSZ-{6hex}."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    h = secrets.token_hex(3)  # 6 hex chars
    return f"{ts}-{h}"


def compact_tracks(tracks_dir: Path, track_ids: list[str]) -> Path:
    """Create a compressed tarball of the given track directories.

    Args:
        tracks_dir: The .agent/kf/tracks/ directory.
        track_ids: List of track IDs whose directories should be archived.

    Returns:
        Path to the created tarball.

    Side effects:
        - Creates {_compacted}/{basename}.tar.gz with track directories
        - Creates {_compacted}/{basename}.json metadata index
        - Removes the original track directories
        - Removes _archive/ entries for the same track IDs
    """
    if not track_ids:
        raise ValueError("No track IDs provided for compaction")

    compacted = _compacted_dir(tracks_dir)
    basename = _make_basename()
    tarball_path = compacted / f"{basename}.tar.gz"
    index_path = compacted / f"{basename}.json"

    # Gather metadata for the index
    completed = 0
    archived = 0
    first_created = None
    last_created = None

    # Build the tarball
    with tarfile.open(tarball_path, "w:gz") as tar:
        for tid in track_ids:
            # Check both the main tracks dir and _archive
            track_dir = tracks_dir / tid
            archive_dir = tracks_dir / "_archive" / tid

            source_dir = None
            if track_dir.is_dir():
                source_dir = track_dir
            elif archive_dir.is_dir():
                source_dir = archive_dir

            if source_dir is None:
                continue

            # Read meta.yaml for index stats
            meta_path = source_dir / "meta.yaml"
            if meta_path.exists():
                try:
                    meta = yaml.safe_load(meta_path.read_text())
                    if isinstance(meta, dict):
                        status = meta.get("status", "")
                        if status == "completed":
                            completed += 1
                        elif status == "archived":
                            archived += 1
                        created = meta.get("created", "")
                        if created:
                            if first_created is None or created < first_created:
                                first_created = created
                            if last_created is None or created > last_created:
                                last_created = created
                except (yaml.YAMLError, OSError):
                    pass

            # Add the directory to the tarball under its track ID
            tar.add(str(source_dir), arcname=tid)

    # Write the JSON index
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    index_data = {
        "date": today,
        "track_ids": track_ids,
        "track_count": len(track_ids),
        "completed": completed,
        "archived": archived,
        "first_created": first_created or "",
        "last_created": last_created or "",
    }
    index_path.write_text(json.dumps(index_data, indent=2) + "\n")

    # Remove original directories
    for tid in track_ids:
        track_dir = tracks_dir / tid
        if track_dir.is_dir():
            shutil.rmtree(track_dir)
        archive_dir = tracks_dir / "_archive" / tid
        if archive_dir.is_dir():
            shutil.rmtree(archive_dir)

    # Clean up empty _archive/ directory
    archive_root = tracks_dir / "_archive"
    if archive_root.is_dir():
        try:
            archive_root.rmdir()
        except OSError:
            pass  # not empty, that's fine

    return tarball_path


def list_compactions(tracks_dir: Path) -> list[dict]:
    """Scan _compacted/ for JSON index files.

    Returns:
        Sorted list of compaction records (newest first), each containing
        the JSON index fields plus a 'name' key (basename without extension).
    """
    compacted = tracks_dir / "_compacted"
    if not compacted.is_dir():
        return []

    records = []
    for json_path in sorted(compacted.glob("*.json"), reverse=True):
        try:
            data = json.loads(json_path.read_text())
            data["name"] = json_path.stem
            records.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return records


def load_compacted_track(tracks_dir: Path,
                         track_id: str) -> Optional[dict]:
    """Search all tarballs for a specific track and return its meta.yaml.

    Args:
        tracks_dir: The .agent/kf/tracks/ directory.
        track_id: The track to find.

    Returns:
        The meta.yaml content as a dict, or None if not found.
    """
    compacted = tracks_dir / "_compacted"
    if not compacted.is_dir():
        return None

    # Search tarballs newest-first (sorted by filename descending)
    for tarball_path in sorted(compacted.glob("*.tar.gz"), reverse=True):
        try:
            with tarfile.open(tarball_path, "r:gz") as tar:
                meta_name = f"{track_id}/meta.yaml"
                try:
                    member = tar.getmember(meta_name)
                except KeyError:
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                content = f.read().decode("utf-8")
                data = yaml.safe_load(content)
                if isinstance(data, dict):
                    return data
        except (tarfile.TarError, OSError, yaml.YAMLError):
            continue

    return None


def _load_tracks_from_tarball(tarball_path: Path) -> dict[str, dict]:
    """Load all track metadata from a single tarball.

    Returns {track_id: meta_dict} for all tracks with readable meta.yaml.
    """
    result = {}
    try:
        with tarfile.open(tarball_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("/meta.yaml"):
                    track_id = member.name.split("/")[0]
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    content = f.read().decode("utf-8")
                    data = yaml.safe_load(content)
                    if isinstance(data, dict):
                        result[track_id] = data
    except (tarfile.TarError, OSError, yaml.YAMLError):
        pass
    return result


def load_all_compacted_tracks(tracks_dir: Path) -> dict[str, dict]:
    """Load ALL compacted track metadata from all tarballs.

    Uses ThreadPoolExecutor for concurrent tarball reading.

    Args:
        tracks_dir: The .agent/kf/tracks/ directory.

    Returns:
        Dict of {track_id: meta_dict} for all compacted tracks.
        If a track appears in multiple tarballs, the newest (by filename)
        takes precedence.
    """
    compacted = tracks_dir / "_compacted"
    if not compacted.is_dir():
        return {}

    tarballs = sorted(compacted.glob("*.tar.gz"))
    if not tarballs:
        return {}

    result = {}

    if len(tarballs) == 1:
        return _load_tracks_from_tarball(tarballs[0])

    workers = min(8, len(tarballs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Process oldest first so newer entries overwrite older ones
        for tracks in pool.map(_load_tracks_from_tarball, tarballs):
            result.update(tracks)

    return result


def extract_compaction(tracks_dir: Path, compaction_name: str) -> Path:
    """Extract a specific compaction tarball to a temporary directory.

    Args:
        tracks_dir: The .agent/kf/tracks/ directory.
        compaction_name: The basename (without .tar.gz) of the compaction.

    Returns:
        Path to the temporary directory containing extracted tracks.
        The caller is responsible for cleaning up (shutil.rmtree).

    Raises:
        FileNotFoundError: If the tarball doesn't exist.
    """
    compacted = tracks_dir / "_compacted"
    tarball_path = compacted / f"{compaction_name}.tar.gz"

    if not tarball_path.exists():
        raise FileNotFoundError(
            f"Compaction tarball not found: {tarball_path}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="kf-compaction-"))
    with tarfile.open(tarball_path, "r:gz") as tar:
        tar.extractall(tmp_dir)

    return tmp_dir
