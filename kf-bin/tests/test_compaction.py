#!/usr/bin/env python3
"""Tests for lib/compaction.py — tarball-based track compaction.

Run: python3 -m pytest kf-bin/tests/test_compaction.py -v
  or: python3 kf-bin/tests/test_compaction.py
"""

import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

# Add scripts dir to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lib.compaction import (
    compact_tracks,
    extract_compaction,
    list_compactions,
    load_all_compacted_tracks,
    load_compacted_track,
)


class TestCompaction(unittest.TestCase):
    """Test tarball-based track compaction."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tracks_dir = self.tmpdir / "tracks"
        self.tracks_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_track(self, track_id, status="completed", title="Test",
                      created="2026-03-15"):
        """Create a track directory with meta.yaml."""
        track_dir = self.tracks_dir / track_id
        track_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "title": title,
            "status": status,
            "type": "feature",
            "approved": False,
            "created": created,
            "updated": created,
        }
        (track_dir / "meta.yaml").write_text(
            yaml.dump(meta, default_flow_style=False, sort_keys=False))
        # Add a spec file for completeness
        (track_dir / "spec.md").write_text(f"# {title}\n\nSpec content.\n")
        return meta

    def _create_archived_track(self, track_id, **kwargs):
        """Create a track in the _archive directory."""
        archive_dir = self.tracks_dir / "_archive" / track_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "title": kwargs.get("title", "Archived Track"),
            "status": "archived",
            "type": "feature",
            "approved": False,
            "created": kwargs.get("created", "2026-03-10"),
            "updated": kwargs.get("created", "2026-03-10"),
            "archived_at": "2026-03-15",
            "archive_reason": "completed",
        }
        (archive_dir / "meta.yaml").write_text(
            yaml.dump(meta, default_flow_style=False, sort_keys=False))
        return meta

    # ── compact_tracks() ─────────────────────────────────────────────────

    def test_compact_creates_tarball_and_index(self):
        self._create_track("track_a", status="completed")
        self._create_track("track_b", status="completed")

        tarball = compact_tracks(self.tracks_dir, ["track_a", "track_b"])

        # Tarball exists
        self.assertTrue(tarball.exists())
        self.assertTrue(tarball.name.endswith(".tar.gz"))

        # JSON index exists alongside
        index_path = tarball.with_suffix("").with_suffix(".json")
        self.assertTrue(index_path.exists())

        # Parse the index
        index_data = json.loads(index_path.read_text())
        self.assertEqual(index_data["track_count"], 2)
        self.assertEqual(sorted(index_data["track_ids"]),
                         ["track_a", "track_b"])
        self.assertEqual(index_data["completed"], 2)
        self.assertEqual(index_data["archived"], 0)

    def test_compact_removes_original_directories(self):
        self._create_track("track_a")

        compact_tracks(self.tracks_dir, ["track_a"])

        self.assertFalse((self.tracks_dir / "track_a").exists())

    def test_compact_removes_archive_entries(self):
        self._create_archived_track("track_x")

        compact_tracks(self.tracks_dir, ["track_x"])

        self.assertFalse((self.tracks_dir / "_archive" / "track_x").exists())
        # _archive dir itself should be removed if empty
        self.assertFalse((self.tracks_dir / "_archive").exists())

    def test_compact_tarball_contains_track_files(self):
        self._create_track("track_a", title="Feature A")

        tarball = compact_tracks(self.tracks_dir, ["track_a"])

        with tarfile.open(tarball, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("track_a/meta.yaml", names)
            self.assertIn("track_a/spec.md", names)

    def test_compact_empty_list_raises(self):
        with self.assertRaises(ValueError):
            compact_tracks(self.tracks_dir, [])

    def test_compact_mixed_sources(self):
        """Compact tracks from both main dir and _archive."""
        self._create_track("track_a", status="completed")
        self._create_archived_track("track_b", title="Archived B")

        tarball = compact_tracks(self.tracks_dir,
                                 ["track_a", "track_b"])

        index_path = tarball.with_suffix("").with_suffix(".json")
        index_data = json.loads(index_path.read_text())
        self.assertEqual(index_data["completed"], 1)
        self.assertEqual(index_data["archived"], 1)

    # ── list_compactions() ───────────────────────────────────────────────

    def test_list_compactions_empty(self):
        records = list_compactions(self.tracks_dir)
        self.assertEqual(records, [])

    def test_list_compactions_returns_records(self):
        self._create_track("track_a")
        compact_tracks(self.tracks_dir, ["track_a"])

        records = list_compactions(self.tracks_dir)
        self.assertEqual(len(records), 1)
        self.assertIn("track_a", records[0]["track_ids"])
        self.assertIn("name", records[0])

    def test_list_compactions_returns_all(self):
        # Create two compactions
        self._create_track("track_a")
        compact_tracks(self.tracks_dir, ["track_a"])

        self._create_track("track_b")
        compact_tracks(self.tracks_dir, ["track_b"])

        records = list_compactions(self.tracks_dir)
        self.assertEqual(len(records), 2)
        # Both tracks present across records
        all_ids = set()
        for r in records:
            all_ids.update(r["track_ids"])
        self.assertIn("track_a", all_ids)
        self.assertIn("track_b", all_ids)

    # ── load_compacted_track() ───────────────────────────────────────────

    def test_load_compacted_track_found(self):
        self._create_track("track_a", title="Feature A",
                           created="2026-03-10")

        compact_tracks(self.tracks_dir, ["track_a"])

        meta = load_compacted_track(self.tracks_dir, "track_a")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["title"], "Feature A")
        self.assertEqual(meta["status"], "completed")

    def test_load_compacted_track_not_found(self):
        meta = load_compacted_track(self.tracks_dir, "nonexistent")
        self.assertIsNone(meta)

    def test_load_compacted_track_no_compacted_dir(self):
        meta = load_compacted_track(self.tracks_dir, "anything")
        self.assertIsNone(meta)

    # ── load_all_compacted_tracks() ──────────────────────────────────────

    def test_load_all_compacted_tracks(self):
        self._create_track("track_a", title="A")
        self._create_track("track_b", title="B")

        compact_tracks(self.tracks_dir, ["track_a", "track_b"])

        all_tracks = load_all_compacted_tracks(self.tracks_dir)
        self.assertEqual(len(all_tracks), 2)
        self.assertEqual(all_tracks["track_a"]["title"], "A")
        self.assertEqual(all_tracks["track_b"]["title"], "B")

    def test_load_all_compacted_tracks_multiple_tarballs(self):
        self._create_track("track_a", title="A")
        compact_tracks(self.tracks_dir, ["track_a"])

        self._create_track("track_b", title="B")
        compact_tracks(self.tracks_dir, ["track_b"])

        all_tracks = load_all_compacted_tracks(self.tracks_dir)
        self.assertEqual(len(all_tracks), 2)
        self.assertIn("track_a", all_tracks)
        self.assertIn("track_b", all_tracks)

    def test_load_all_compacted_tracks_empty(self):
        all_tracks = load_all_compacted_tracks(self.tracks_dir)
        self.assertEqual(all_tracks, {})

    # ── extract_compaction() ─────────────────────────────────────────────

    def test_extract_compaction(self):
        self._create_track("track_a", title="Feature A")
        compact_tracks(self.tracks_dir, ["track_a"])

        records = list_compactions(self.tracks_dir)
        name = records[0]["name"]

        tmp_dir = extract_compaction(self.tracks_dir, name)
        try:
            self.assertTrue((tmp_dir / "track_a").is_dir())
            self.assertTrue((tmp_dir / "track_a" / "meta.yaml").exists())
            meta = yaml.safe_load(
                (tmp_dir / "track_a" / "meta.yaml").read_text())
            self.assertEqual(meta["title"], "Feature A")
        finally:
            shutil.rmtree(tmp_dir)

    def test_extract_compaction_not_found(self):
        with self.assertRaises(FileNotFoundError):
            extract_compaction(self.tracks_dir, "nonexistent")

    # ── Round-trip test ──────────────────────────────────────────────────

    def test_roundtrip_compact_then_load(self):
        """Full round-trip: create tracks, compact, load back."""
        original_meta_a = self._create_track(
            "track_a", title="Alpha Feature", status="completed",
            created="2026-03-10")
        original_meta_b = self._create_track(
            "track_b", title="Beta Feature", status="completed",
            created="2026-03-15")

        # Compact
        compact_tracks(self.tracks_dir, ["track_a", "track_b"])

        # Original dirs should be gone
        self.assertFalse((self.tracks_dir / "track_a").exists())
        self.assertFalse((self.tracks_dir / "track_b").exists())

        # Load individual
        meta_a = load_compacted_track(self.tracks_dir, "track_a")
        self.assertEqual(meta_a["title"], "Alpha Feature")
        self.assertEqual(meta_a["created"], "2026-03-10")

        meta_b = load_compacted_track(self.tracks_dir, "track_b")
        self.assertEqual(meta_b["title"], "Beta Feature")
        self.assertEqual(meta_b["created"], "2026-03-15")

        # Load all
        all_tracks = load_all_compacted_tracks(self.tracks_dir)
        self.assertEqual(len(all_tracks), 2)

        # List compactions
        records = list_compactions(self.tracks_dir)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["track_count"], 2)

        # Extract and verify files
        name = records[0]["name"]
        tmp_dir = extract_compaction(self.tracks_dir, name)
        try:
            self.assertTrue((tmp_dir / "track_a" / "spec.md").exists())
            self.assertTrue((tmp_dir / "track_b" / "spec.md").exists())
        finally:
            shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    unittest.main()
