#!/usr/bin/env python3
"""Tests for lib/tracks.py TracksRegistry.

Run: python3 -m pytest kf-bin/tests/ -v
  or: python3 kf-bin/tests/test_tracks_registry.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts dir to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lib.tracks import TracksRegistry, today_iso, _dump_meta, _conflict_pair_key


class TestTracksRegistryFilesystem(unittest.TestCase):
    """Test TracksRegistry with filesystem-based per-track meta.yaml files."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tracks_dir = self.tmpdir / "tracks"
        self.tracks_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_meta(self, track_id, data):
        track_dir = self.tracks_dir / track_id
        track_dir.mkdir(parents=True, exist_ok=True)
        (track_dir / "meta.yaml").write_text(_dump_meta(data))

    def test_empty_dir(self):
        reg = TracksRegistry(self.tracks_dir)
        self.assertEqual(reg.ids(), [])
        self.assertEqual(reg.all_entries(), {})

    def test_load_single_track(self):
        self._write_meta("track_001", {
            "title": "Test Feature",
            "status": "pending",
            "type": "feature",
            "approved": False,
            "created": "2026-03-21",
            "updated": "2026-03-21",
        })
        reg = TracksRegistry(self.tracks_dir)
        self.assertTrue(reg.exists("track_001"))
        self.assertFalse(reg.exists("nonexistent"))
        self.assertEqual(reg.get_field("track_001", "title"), "Test Feature")
        self.assertEqual(reg.get_field("track_001", "status"), "pending")

    def test_load_multiple_tracks(self):
        for i in range(5):
            self._write_meta(f"track_{i:03d}", {
                "title": f"Track {i}",
                "status": "pending",
                "type": "feature",
                "created": "2026-03-21",
                "updated": "2026-03-21",
            })
        reg = TracksRegistry(self.tracks_dir)
        self.assertEqual(len(reg.ids()), 5)

    def test_skips_underscore_dirs(self):
        self._write_meta("track_001", {
            "title": "Active",
            "status": "pending",
            "type": "feature",
            "created": "2026-03-21",
            "updated": "2026-03-21",
        })
        # _archive should be skipped
        (self.tracks_dir / "_archive" / "old_track").mkdir(parents=True)
        (self.tracks_dir / "_archive" / "old_track" / "meta.yaml").write_text(
            _dump_meta({"title": "Old", "status": "archived", "type": "feature",
                        "created": "2025-01-01", "updated": "2025-01-01"}))
        reg = TracksRegistry(self.tracks_dir)
        self.assertEqual(len(reg.ids()), 1)
        self.assertTrue(reg.exists("track_001"))

    def test_add_and_save(self):
        reg = TracksRegistry(self.tracks_dir)
        entry = reg.add("new_track", "New Feature", type_="feature",
                        deps=["dep_a", "dep_b"])
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["title"], "New Feature")

        reg.save(track_ids=["new_track"])

        # Verify file was written
        meta_path = self.tracks_dir / "new_track" / "meta.yaml"
        self.assertTrue(meta_path.exists())

        # Re-load and verify
        reg2 = TracksRegistry(self.tracks_dir)
        self.assertTrue(reg2.exists("new_track"))
        self.assertEqual(reg2.get_field("new_track", "title"), "New Feature")
        self.assertEqual(reg2.get_deps("new_track"), ["dep_a", "dep_b"])

    def test_add_duplicate_raises(self):
        reg = TracksRegistry(self.tracks_dir)
        reg.add("track_001", "First")
        with self.assertRaises(ValueError):
            reg.add("track_001", "Duplicate")

    def test_set_field_and_save(self):
        self._write_meta("track_001", {
            "title": "Test",
            "status": "pending",
            "type": "feature",
            "created": "2026-03-21",
            "updated": "2026-03-21",
        })
        reg = TracksRegistry(self.tracks_dir)
        reg.set_field("track_001", "status", "in-progress")
        reg.save(track_ids=["track_001"])

        reg2 = TracksRegistry(self.tracks_dir)
        self.assertEqual(reg2.get_field("track_001", "status"), "in-progress")
        # updated should be refreshed
        self.assertEqual(reg2.get_field("track_001", "updated"), today_iso())

    def test_set_field_nonexistent_raises(self):
        reg = TracksRegistry(self.tracks_dir)
        with self.assertRaises(KeyError):
            reg.set_field("nonexistent", "status", "pending")

    def test_update_status(self):
        self._write_meta("track_001", {
            "title": "Test",
            "status": "pending",
            "type": "feature",
            "created": "2026-03-21",
            "updated": "2026-03-21",
        })
        reg = TracksRegistry(self.tracks_dir)
        reg.update_status("track_001", "completed")
        self.assertEqual(reg.get_field("track_001", "status"), "completed")

    def test_update_status_invalid(self):
        self._write_meta("track_001", {
            "title": "Test",
            "status": "pending",
            "type": "feature",
            "created": "2026-03-21",
            "updated": "2026-03-21",
        })
        reg = TracksRegistry(self.tracks_dir)
        with self.assertRaises(ValueError):
            reg.update_status("track_001", "invalid")

    def test_update_status_archived_sets_date(self):
        self._write_meta("track_001", {
            "title": "Test",
            "status": "pending",
            "type": "feature",
            "created": "2026-03-21",
            "updated": "2026-03-21",
        })
        reg = TracksRegistry(self.tracks_dir)
        reg.update_status("track_001", "archived")
        self.assertEqual(reg.get_field("track_001", "archived_at"), today_iso())

    def test_list_by_status(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        self._write_meta("t2", {"title": "B", "status": "in-progress",
                                "type": "bug", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        self._write_meta("t3", {"title": "C", "status": "completed",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        reg = TracksRegistry(self.tracks_dir)
        pending = reg.list_by_status("pending")
        self.assertEqual(list(pending.keys()), ["t1"])
        active = reg.list_active()
        self.assertEqual(sorted(active.keys()), ["t1", "t2"])

    def test_get_returns_none_for_missing(self):
        reg = TracksRegistry(self.tracks_dir)
        self.assertIsNone(reg.get("nonexistent"))
        self.assertIsNone(reg.get_field("nonexistent", "status"))


class TestDeps(unittest.TestCase):
    """Test dependency management."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tracks_dir = self.tmpdir / "tracks"
        self.tracks_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_meta(self, track_id, data):
        track_dir = self.tracks_dir / track_id
        track_dir.mkdir(parents=True, exist_ok=True)
        (track_dir / "meta.yaml").write_text(_dump_meta(data))

    def test_get_deps_empty(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        reg = TracksRegistry(self.tracks_dir)
        self.assertEqual(reg.get_deps("t1"), [])

    def test_get_deps_with_values(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "deps": ["t0", "t_base"]})
        reg = TracksRegistry(self.tracks_dir)
        self.assertEqual(reg.get_deps("t1"), ["t0", "t_base"])

    def test_add_dep(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        reg = TracksRegistry(self.tracks_dir)
        reg.add_dep("t1", "t0")
        reg.add_dep("t1", "t_base")
        reg.add_dep("t1", "t0")  # duplicate — should not add twice
        self.assertEqual(reg.get_deps("t1"), ["t0", "t_base"])

    def test_remove_dep(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "deps": ["t0", "t_base"]})
        reg = TracksRegistry(self.tracks_dir)
        reg.remove_dep("t1", "t0")
        self.assertEqual(reg.get_deps("t1"), ["t_base"])

    def test_deps_satisfied(self):
        self._write_meta("base", {"title": "Base", "status": "completed",
                                  "type": "feature", "created": "2026-03-21",
                                  "updated": "2026-03-21"})
        self._write_meta("child", {"title": "Child", "status": "pending",
                                   "type": "feature", "created": "2026-03-21",
                                   "updated": "2026-03-21",
                                   "deps": ["base"]})
        reg = TracksRegistry(self.tracks_dir)
        self.assertTrue(reg.deps_satisfied("child"))

    def test_deps_not_satisfied(self):
        self._write_meta("base", {"title": "Base", "status": "pending",
                                  "type": "feature", "created": "2026-03-21",
                                  "updated": "2026-03-21"})
        self._write_meta("child", {"title": "Child", "status": "pending",
                                   "type": "feature", "created": "2026-03-21",
                                   "updated": "2026-03-21",
                                   "deps": ["base"]})
        reg = TracksRegistry(self.tracks_dir)
        self.assertFalse(reg.deps_satisfied("child"))

    def test_dep_summary(self):
        self._write_meta("base", {"title": "Base", "status": "completed",
                                  "type": "feature", "created": "2026-03-21",
                                  "updated": "2026-03-21"})
        self._write_meta("other", {"title": "Other", "status": "pending",
                                   "type": "feature", "created": "2026-03-21",
                                   "updated": "2026-03-21"})
        self._write_meta("child", {"title": "Child", "status": "pending",
                                   "type": "feature", "created": "2026-03-21",
                                   "updated": "2026-03-21",
                                   "deps": ["base", "other"]})
        reg = TracksRegistry(self.tracks_dir)
        self.assertEqual(reg.dep_summary("child"), "1/2")

    def test_dep_summary_no_deps(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        reg = TracksRegistry(self.tracks_dir)
        self.assertEqual(reg.dep_summary("t1"), "-")

    def test_all_deps(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "deps": ["t0"]})
        self._write_meta("t2", {"title": "B", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        reg = TracksRegistry(self.tracks_dir)
        graph = reg.all_deps()
        self.assertIn("t1", graph)
        self.assertNotIn("t2", graph)  # no deps = not in graph


class TestConflicts(unittest.TestCase):
    """Test conflict pair management."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tracks_dir = self.tmpdir / "tracks"
        self.tracks_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_meta(self, track_id, data):
        track_dir = self.tracks_dir / track_id
        track_dir.mkdir(parents=True, exist_ok=True)
        (track_dir / "meta.yaml").write_text(_dump_meta(data))

    def test_add_conflict(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        self._write_meta("t2", {"title": "B", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        reg = TracksRegistry(self.tracks_dir)
        reg.add_conflict("t1", "t2", risk="high", note="overlap")

        # Both sides should have the conflict
        c1 = reg.get_conflicts("t1")
        c2 = reg.get_conflicts("t2")
        self.assertEqual(len(c1), 1)
        self.assertEqual(c1[0]["peer"], "t2")
        self.assertEqual(c1[0]["risk"], "high")
        self.assertEqual(len(c2), 1)
        self.assertEqual(c2[0]["peer"], "t1")

    def test_add_conflict_self_raises(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        reg = TracksRegistry(self.tracks_dir)
        with self.assertRaises(ValueError):
            reg.add_conflict("t1", "t1")

    def test_remove_conflict(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "conflicts": [{"peer": "t2", "risk": "high",
                                               "note": "", "added": "2026-03-21"}]})
        self._write_meta("t2", {"title": "B", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "conflicts": [{"peer": "t1", "risk": "high",
                                               "note": "", "added": "2026-03-21"}]})
        reg = TracksRegistry(self.tracks_dir)
        reg.remove_conflict("t1", "t2")
        self.assertEqual(reg.get_conflicts("t1"), [])
        self.assertEqual(reg.get_conflicts("t2"), [])

    def test_clean_conflicts(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "conflicts": [
                                    {"peer": "t2", "risk": "high",
                                     "note": "", "added": "2026-03-21"},
                                    {"peer": "t3", "risk": "low",
                                     "note": "", "added": "2026-03-21"},
                                ]})
        reg = TracksRegistry(self.tracks_dir)
        peers = reg.clean_conflicts("t1")
        self.assertEqual(sorted(peers), ["t2", "t3"])
        self.assertEqual(reg.get_conflicts("t1"), [])

    def test_all_conflict_pairs_deduplicates(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "conflicts": [{"peer": "t2", "risk": "high",
                                               "note": "x", "added": "2026-03-21"}]})
        self._write_meta("t2", {"title": "B", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "conflicts": [{"peer": "t1", "risk": "high",
                                               "note": "x", "added": "2026-03-21"}]})
        reg = TracksRegistry(self.tracks_dir)
        pairs = reg.all_conflict_pairs()
        self.assertEqual(len(pairs), 1)
        key = "t1/t2"
        self.assertIn(key, pairs)

    def test_all_conflict_pairs_excludes_inactive(self):
        self._write_meta("t1", {"title": "A", "status": "pending",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21",
                                "conflicts": [{"peer": "t2", "risk": "high",
                                               "note": "", "added": "2026-03-21"}]})
        self._write_meta("t2", {"title": "B", "status": "completed",
                                "type": "feature", "created": "2026-03-21",
                                "updated": "2026-03-21"})
        reg = TracksRegistry(self.tracks_dir)
        pairs = reg.all_conflict_pairs()
        self.assertEqual(len(pairs), 0)


class TestLegacyFallback(unittest.TestCase):
    """Test backward compatibility with legacy tracks.yaml format."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.kf_dir = self.tmpdir / ".agent" / "kf"
        self.kf_dir.mkdir(parents=True)
        self.tracks_dir = self.kf_dir / "tracks"
        self.tracks_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_from_legacy_tracks_only(self):
        tracks_file = self.kf_dir / "tracks.yaml"
        tracks_file.write_text(
            "# header\n"
            'track_a: {"title":"Feature A","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
            'track_b: {"title":"Bug B","status":"completed","type":"bug",'
            '"created":"2026-03-20","updated":"2026-03-21"}\n'
        )
        reg = TracksRegistry.from_legacy(tracks_file)
        self.assertEqual(len(reg.ids()), 2)
        self.assertEqual(reg.get_field("track_a", "title"), "Feature A")
        self.assertEqual(reg.get_field("track_b", "status"), "completed")

    def test_from_legacy_with_deps(self):
        tracks_file = self.kf_dir / "tracks.yaml"
        tracks_file.write_text(
            'track_a: {"title":"A","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
            'track_b: {"title":"B","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
        )
        deps_file = self.tracks_dir / "deps.yaml"
        deps_file.write_text(
            "# deps\n"
            "track_b:\n"
            "  - track_a\n"
        )
        reg = TracksRegistry.from_legacy(tracks_file, deps_file)
        self.assertEqual(reg.get_deps("track_b"), ["track_a"])
        self.assertEqual(reg.get_deps("track_a"), [])

    def test_from_legacy_with_conflicts(self):
        tracks_file = self.kf_dir / "tracks.yaml"
        tracks_file.write_text(
            'track_a: {"title":"A","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
            'track_b: {"title":"B","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
        )
        conflicts_file = self.tracks_dir / "conflicts.yaml"
        conflicts_file.write_text(
            "# conflicts\n"
            'track_a/track_b: {"risk":"high","note":"overlap","added":"2026-03-21"}\n'
        )
        reg = TracksRegistry.from_legacy(tracks_file, deps_file=None,
                                         conflicts_file=conflicts_file)
        c_a = reg.get_conflicts("track_a")
        c_b = reg.get_conflicts("track_b")
        self.assertEqual(len(c_a), 1)
        self.assertEqual(c_a[0]["peer"], "track_b")
        self.assertEqual(len(c_b), 1)
        self.assertEqual(c_b[0]["peer"], "track_a")

    def test_filesystem_legacy_fallback(self):
        """TracksRegistry falls back to tracks.yaml when no meta.yaml exists."""
        tracks_file = self.kf_dir / "tracks.yaml"
        tracks_file.write_text(
            'track_a: {"title":"A","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
        )
        reg = TracksRegistry(self.tracks_dir)
        self.assertTrue(reg.exists("track_a"))


class TestGitRef(unittest.TestCase):
    """Test TracksRegistry.from_ref() with a real git repo."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.orig_dir = os.getcwd()
        os.chdir(self.tmpdir)
        subprocess.run(["git", "init"], capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       capture_output=True, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"],
                       capture_output=True, check=True)

    def tearDown(self):
        os.chdir(self.orig_dir)
        shutil.rmtree(self.tmpdir)

    def test_from_ref_with_meta_yaml(self):
        """Test reading per-track meta.yaml from a git ref."""
        tracks_dir = self.tmpdir / ".agent" / "kf" / "tracks" / "track_001"
        tracks_dir.mkdir(parents=True)
        (tracks_dir / "meta.yaml").write_text(_dump_meta({
            "title": "Test Feature",
            "status": "pending",
            "type": "feature",
            "approved": False,
            "created": "2026-03-21",
            "updated": "2026-03-21",
            "deps": ["dep_a"],
        }))
        subprocess.run(["git", "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       capture_output=True, check=True)

        reg = TracksRegistry.from_ref("HEAD")
        self.assertTrue(reg.exists("track_001"))
        self.assertEqual(reg.get_field("track_001", "title"), "Test Feature")
        self.assertEqual(reg.get_deps("track_001"), ["dep_a"])

    def test_from_ref_multiple_tracks(self):
        """Test batch reading multiple tracks."""
        for i in range(10):
            track_dir = (self.tmpdir / ".agent" / "kf" / "tracks"
                         / f"track_{i:03d}")
            track_dir.mkdir(parents=True)
            (track_dir / "meta.yaml").write_text(_dump_meta({
                "title": f"Track {i}",
                "status": "pending" if i % 2 == 0 else "completed",
                "type": "feature",
                "created": "2026-03-21",
                "updated": "2026-03-21",
            }))
        subprocess.run(["git", "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       capture_output=True, check=True)

        reg = TracksRegistry.from_ref("HEAD")
        self.assertEqual(len(reg.ids()), 10)
        pending = reg.list_by_status("pending")
        self.assertEqual(len(pending), 5)

    def test_from_ref_legacy_fallback(self):
        """Test fallback to tracks.yaml when no meta.yaml files exist."""
        kf_dir = self.tmpdir / ".agent" / "kf"
        kf_dir.mkdir(parents=True)
        (kf_dir / "tracks").mkdir()
        (kf_dir / "tracks.yaml").write_text(
            '# header\n'
            'legacy_track: {"title":"Legacy","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
        )
        subprocess.run(["git", "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       capture_output=True, check=True)

        reg = TracksRegistry.from_ref("HEAD")
        self.assertTrue(reg.exists("legacy_track"))
        self.assertEqual(reg.get_field("legacy_track", "title"), "Legacy")

    def test_from_ref_is_readonly(self):
        """Registry from git ref should raise on save."""
        kf_dir = self.tmpdir / ".agent" / "kf"
        kf_dir.mkdir(parents=True)
        (kf_dir / "tracks").mkdir()
        (kf_dir / "tracks.yaml").write_text(
            'track_a: {"title":"A","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
        )
        subprocess.run(["git", "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       capture_output=True, check=True)

        reg = TracksRegistry.from_ref("HEAD")
        reg.set_field("track_a", "status", "completed")
        with self.assertRaises(RuntimeError):
            reg.save()


    def test_from_ref_non_ascii_content(self):
        """Non-ASCII chars (em dash, accents) must not corrupt batch parsing.

        Regression test: git cat-file --batch reports sizes in bytes but
        Python text mode slices by characters. Multi-byte UTF-8 chars cause
        position drift, silently dropping tracks after the one with non-ASCII.
        """
        kf_dir = self.tmpdir / ".agent" / "kf"
        tracks_dir = kf_dir / "tracks"
        tracks_dir.mkdir(parents=True)

        # Track A: ASCII only
        (tracks_dir / "track-a_20260322000000Z").mkdir()
        (tracks_dir / "track-a_20260322000000Z" / "meta.yaml").write_text(
            'title: "Track A"\nstatus: pending\ntype: feature\n'
            'created: "2026-03-22"\nupdated: "2026-03-22"\n'
        )

        # Track B: contains em dash (—), accented chars (é), and other multi-byte
        (tracks_dir / "track-b_20260322000001Z").mkdir()
        (tracks_dir / "track-b_20260322000001Z" / "meta.yaml").write_text(
            'title: "Track B"\nstatus: pending\ntype: feature\n'
            'created: "2026-03-22"\nupdated: "2026-03-22"\n'
            'conflicts:\n'
            '- peer: track-a_20260322000000Z\n'
            '  risk: medium\n'
            '  note: "Both touch adapters — different areas but potential merge conflicts"\n'
            '  added: "2026-03-22"\n'
        )

        # Track C: after the non-ASCII track — this is the one that gets dropped
        (tracks_dir / "track-c_20260322000002Z").mkdir()
        (tracks_dir / "track-c_20260322000002Z" / "meta.yaml").write_text(
            'title: "Track C — the résumé endpoint"\nstatus: pending\ntype: feature\n'
            'created: "2026-03-22"\nupdated: "2026-03-22"\n'
        )

        subprocess.run(["git", "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "tracks with non-ascii"],
                       capture_output=True, check=True)

        reg = TracksRegistry.from_ref("HEAD")
        self.assertEqual(len(reg.ids()), 3,
                         f"Expected 3 tracks but got {len(reg.ids())}: {reg.ids()}")
        self.assertIn("track-a_20260322000000Z", reg.ids())
        self.assertIn("track-b_20260322000001Z", reg.ids())
        self.assertIn("track-c_20260322000002Z", reg.ids())

        # Verify content survived
        b = reg.get("track-b_20260322000001Z")
        self.assertIsNotNone(b)
        self.assertEqual(len(b.get("conflicts", [])), 1)
        self.assertIn("—", b["conflicts"][0]["note"])

        c = reg.get("track-c_20260322000002Z")
        self.assertIsNotNone(c)
        self.assertIn("résumé", c["title"])

    def test_from_ref_mixed_missing_and_non_ascii(self):
        """Tracks without meta.yaml followed by non-ASCII tracks.

        This is the exact scenario that caused the original bug:
        some dirs have no meta.yaml (missing), then a track with
        multi-byte chars, then more tracks after it.
        """
        kf_dir = self.tmpdir / ".agent" / "kf"
        tracks_dir = kf_dir / "tracks"
        tracks_dir.mkdir(parents=True)

        # Track with no meta.yaml (just track.yaml content)
        (tracks_dir / "legacy-track_20260320000000Z").mkdir()
        (tracks_dir / "legacy-track_20260320000000Z" / "track.yaml").write_text(
            'id: legacy\ntitle: "Legacy"\n'
        )

        # Track with non-ASCII meta.yaml
        (tracks_dir / "new-track_20260322000001Z").mkdir()
        (tracks_dir / "new-track_20260322000001Z" / "meta.yaml").write_text(
            'title: "Héllo wörld — special chars"\nstatus: pending\ntype: feature\n'
            'created: "2026-03-22"\nupdated: "2026-03-22"\n'
        )

        # Track after the non-ASCII one
        (tracks_dir / "post-track_20260322000002Z").mkdir()
        (tracks_dir / "post-track_20260322000002Z" / "meta.yaml").write_text(
            'title: "Post track"\nstatus: pending\ntype: feature\n'
            'created: "2026-03-22"\nupdated: "2026-03-22"\n'
        )

        subprocess.run(["git", "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "mixed"],
                       capture_output=True, check=True)

        reg = TracksRegistry.from_ref("HEAD")
        # legacy-track has no meta.yaml so it's skipped
        self.assertEqual(len(reg.ids()), 2,
                         f"Expected 2 tracks but got {len(reg.ids())}: {reg.ids()}")
        self.assertIn("new-track_20260322000001Z", reg.ids())
        self.assertIn("post-track_20260322000002Z", reg.ids())


class TestSaveRoundtrip(unittest.TestCase):
    """Test save/load roundtrip preserves all data."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tracks_dir = self.tmpdir / "tracks"
        self.tracks_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_full_roundtrip(self):
        reg = TracksRegistry(self.tracks_dir)
        reg.add("t1", "Feature One", type_="feature",
                deps=["t0"], approved=True)
        reg.add("t2", "Bug Two", type_="bug")
        reg.add_conflict("t1", "t2", risk="medium", note="shared module")
        reg.save()

        reg2 = TracksRegistry(self.tracks_dir)
        self.assertEqual(reg2.get_field("t1", "title"), "Feature One")
        self.assertEqual(reg2.get_field("t1", "approved"), True)
        self.assertEqual(reg2.get_deps("t1"), ["t0"])
        conflicts = reg2.get_conflicts("t1")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["peer"], "t2")
        self.assertEqual(conflicts[0]["risk"], "medium")

    def test_dirty_tracking(self):
        """Only dirty tracks are saved."""
        reg = TracksRegistry(self.tracks_dir)
        reg.add("t1", "A")
        reg.add("t2", "B")
        reg.save()  # saves both

        reg2 = TracksRegistry(self.tracks_dir)
        reg2.set_field("t1", "status", "completed")
        # Only t1 is dirty
        self.assertIn("t1", reg2._dirty)
        self.assertNotIn("t2", reg2._dirty)


class TestConflictPairKey(unittest.TestCase):
    def test_canonical_order(self):
        self.assertEqual(_conflict_pair_key("b", "a"), "a/b")
        self.assertEqual(_conflict_pair_key("a", "b"), "a/b")
        self.assertEqual(_conflict_pair_key("z", "a"), "a/z")


class TestMigration(unittest.TestCase):
    """Test migration from legacy to per-track meta.yaml."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.kf_dir = self.tmpdir / ".agent" / "kf"
        self.kf_dir.mkdir(parents=True)
        self.tracks_dir = self.kf_dir / "tracks"
        self.tracks_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_migrate_creates_meta_files(self):
        tracks_file = self.kf_dir / "tracks.yaml"
        tracks_file.write_text(
            'track_a: {"title":"A","status":"pending","type":"feature",'
            '"created":"2026-03-21","updated":"2026-03-21"}\n'
            'track_b: {"title":"B","status":"completed","type":"bug",'
            '"created":"2026-03-20","updated":"2026-03-21"}\n'
        )
        deps_file = self.tracks_dir / "deps.yaml"
        deps_file.write_text("track_a:\n  - track_b\n")

        # Load from legacy
        reg = TracksRegistry.from_legacy(tracks_file, deps_file)
        reg.tracks_dir = self.tracks_dir

        # Save all tracks
        reg.save(track_ids=list(reg.ids()))

        # Verify meta.yaml files exist
        self.assertTrue((self.tracks_dir / "track_a" / "meta.yaml").exists())
        self.assertTrue((self.tracks_dir / "track_b" / "meta.yaml").exists())

        # Re-load from meta.yaml and verify
        reg2 = TracksRegistry(self.tracks_dir)
        self.assertEqual(reg2.get_field("track_a", "title"), "A")
        self.assertEqual(reg2.get_field("track_b", "status"), "completed")
        self.assertEqual(reg2.get_deps("track_a"), ["track_b"])


if __name__ == "__main__":
    unittest.main()
