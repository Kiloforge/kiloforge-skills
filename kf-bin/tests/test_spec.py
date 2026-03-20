#!/usr/bin/env python3
"""Tests for lib/spec.py — product specification management.

Run: python3 -m unittest kf-bin/tests/test_spec.py -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lib.spec import (
    SpecSnapshot, SpecOp, materialize, snapshot_from_materialized,
    load_spec_ops, create_spec_op, validate_spec_refs, validate_spec_ops,
    draft_add, draft_load, draft_list, draft_finalize, draft_discard,
    check_uncommitted_drafts,
    parent_id, children_of, tree_under, today_iso,
    SPEC_OP_ACTIONS, TRACK_REF_ACTIONS,
)


class TestHierarchyHelpers(unittest.TestCase):

    def test_parent_id(self):
        self.assertEqual(parent_id("auth.oauth2.pkce"), "auth.oauth2")
        self.assertEqual(parent_id("auth.oauth2"), "auth")
        self.assertEqual(parent_id("auth"), "")

    def test_children_of(self):
        items = {
            "auth": {"title": "Auth"},
            "auth.oauth2": {"title": "OAuth2"},
            "auth.mfa": {"title": "MFA"},
            "auth.oauth2.pkce": {"title": "PKCE"},
            "api": {"title": "API"},
        }
        children = children_of(items, "auth")
        self.assertEqual(sorted(children.keys()), ["auth.mfa", "auth.oauth2"])

    def test_tree_under(self):
        items = {
            "auth": {"title": "Auth"},
            "auth.oauth2": {"title": "OAuth2"},
            "auth.oauth2.pkce": {"title": "PKCE"},
            "api": {"title": "API"},
        }
        tree = tree_under(items, "auth")
        self.assertEqual(sorted(tree.keys()),
                         ["auth", "auth.oauth2", "auth.oauth2.pkce"])

    def test_tree_under_empty_prefix(self):
        items = {"a": {}, "b": {}}
        self.assertEqual(tree_under(items, ""), items)


class TestSpecSnapshot(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_snapshot(self):
        snap = SpecSnapshot()
        self.assertEqual(snap.version, 1)
        self.assertEqual(snap.items, {})

    def test_add_item(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2 Authentication",
                      category="auth", priority="high",
                      description="OAuth2-based auth")
        self.assertTrue(snap.has_item("auth.oauth2"))
        item = snap.get_item("auth.oauth2")
        self.assertEqual(item["title"], "OAuth2 Authentication")
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["category"], "auth")

    def test_add_item_auto_category(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2")
        self.assertEqual(snap.get_item("auth.oauth2")["category"], "auth")

    def test_add_duplicate_raises(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2")
        with self.assertRaises(ValueError):
            snap.add_item("auth.oauth2", "Duplicate")

    def test_save_and_load(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2", description="Auth flow")
        snap.add_item("api.rate-limiting", "Rate Limiting", priority="medium")

        path = self.tmpdir / "spec.yaml"
        snap.save(path)
        self.assertTrue(path.exists())

        loaded = SpecSnapshot.load(path)
        self.assertEqual(len(loaded.items), 2)
        self.assertEqual(loaded.get_item("auth.oauth2")["title"], "OAuth2")
        self.assertEqual(loaded.get_item("api.rate-limiting")["priority"],
                         "medium")

    def test_load_nonexistent(self):
        snap = SpecSnapshot.load(self.tmpdir / "nope.yaml")
        self.assertEqual(snap.items, {})

    def test_from_text(self):
        text = """
version: 2
snapshot_date: "2026-03-21"
snapshot_after_tracks: [track_a]
items:
  auth.oauth2:
    title: OAuth2
    status: active
    category: auth
"""
        snap = SpecSnapshot.from_text(text)
        self.assertEqual(snap.version, 2)
        self.assertEqual(snap.snapshot_after_tracks, ["track_a"])
        self.assertTrue(snap.has_item("auth.oauth2"))

    def test_list_items_filter(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2", category="auth")
        snap.add_item("api.rate", "Rate Limiting", category="api")
        snap.items["auth.oauth2"]["status"] = "fulfilled"

        active = snap.list_items(status="active")
        self.assertEqual(list(active.keys()), ["api.rate"])

        auth = snap.list_items(category="auth")
        self.assertEqual(list(auth.keys()), ["auth.oauth2"])

    def test_categories(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "A", category="auth")
        snap.add_item("api.rate", "B", category="api")
        snap.add_item("auth.mfa", "C", category="auth")
        self.assertEqual(snap.categories(), ["api", "auth"])

    def test_top_level_groups(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "A")
        snap.add_item("auth.mfa", "B")
        snap.add_item("api.rate", "C")
        self.assertEqual(snap.top_level_groups(), ["api", "auth"])


class TestMaterialize(unittest.TestCase):

    def _base_snapshot(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2", category="auth",
                      priority="high", description="OAuth2 flow")
        snap.add_item("api.rate-limiting", "Rate Limiting", category="api",
                      priority="medium")
        return snap

    def test_no_tracks(self):
        base = self._base_snapshot()
        result = materialize(base)
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")

    def test_fulfills(self):
        base = self._base_snapshot()
        tracks = {
            "track_auth": {
                "created": "2026-03-21",
                "spec_refs": [
                    {"action": "fulfills", "item": "auth.oauth2"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        self.assertEqual(result.get_item("auth.oauth2")["status"], "fulfilled")
        self.assertEqual(result.get_item("auth.oauth2")["fulfilled_by"],
                         "track_auth")

    def test_adds(self):
        base = self._base_snapshot()
        tracks = {
            "track_mfa": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "adds", "item": "auth.mfa",
                     "title": "MFA", "priority": "high",
                     "description": "TOTP-based MFA"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        self.assertEqual(len(result.items), 3)
        mfa = result.get_item("auth.mfa")
        self.assertEqual(mfa["title"], "MFA")
        self.assertEqual(mfa["added_by"], "track_mfa")
        self.assertEqual(mfa["category"], "auth")  # auto-derived

    def test_adds_idempotent(self):
        """If item already exists, adds is a no-op."""
        base = self._base_snapshot()
        tracks = {
            "track_dup": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "adds", "item": "auth.oauth2",
                     "title": "Different Title"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        # Original title preserved (first creator wins)
        self.assertEqual(result.get_item("auth.oauth2")["title"], "OAuth2")

    def test_modifies(self):
        base = self._base_snapshot()
        tracks = {
            "track_mod": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "modifies", "item": "api.rate-limiting",
                     "description": "Sliding window algorithm"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        item = result.get_item("api.rate-limiting")
        self.assertEqual(item["description"], "Sliding window algorithm")
        self.assertEqual(item["modified_by"], "track_mod")
        # Title unchanged
        self.assertEqual(item["title"], "Rate Limiting")

    def test_deprecates(self):
        base = self._base_snapshot()
        tracks = {
            "track_dep": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "deprecates", "item": "auth.oauth2"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        self.assertEqual(result.get_item("auth.oauth2")["status"],
                         "deprecated")
        self.assertEqual(result.get_item("auth.oauth2")["deprecated_by"],
                         "track_dep")

    def test_moves(self):
        base = self._base_snapshot()
        tracks = {
            "track_mv": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "moves", "item": "auth.oauth2",
                     "to": "identity.oauth2"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        self.assertFalse(result.has_item("auth.oauth2"))
        self.assertTrue(result.has_item("identity.oauth2"))
        item = result.get_item("identity.oauth2")
        self.assertEqual(item["title"], "OAuth2")
        self.assertEqual(item["moved_by"], "track_mv")
        self.assertEqual(item["moved_from"], "auth.oauth2")
        self.assertEqual(item["category"], "identity")

    def test_moves_with_children(self):
        snap = SpecSnapshot()
        snap.add_item("legacy.auth", "Auth", category="legacy")
        snap.add_item("legacy.auth.session", "Session Auth",
                      category="legacy")
        snap.add_item("legacy.auth.token", "Token Auth", category="legacy")
        snap.add_item("other.thing", "Other", category="other")

        tracks = {
            "track_mv": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "moves", "item": "legacy.auth",
                     "to": "identity.auth"},
                ],
            }
        }
        result = materialize(snap, tracks=tracks)
        self.assertFalse(result.has_item("legacy.auth"))
        self.assertFalse(result.has_item("legacy.auth.session"))
        self.assertFalse(result.has_item("legacy.auth.token"))
        self.assertTrue(result.has_item("identity.auth"))
        self.assertTrue(result.has_item("identity.auth.session"))
        self.assertTrue(result.has_item("identity.auth.token"))
        self.assertTrue(result.has_item("other.thing"))
        # Verify category updated
        self.assertEqual(
            result.get_item("identity.auth.session")["category"], "identity")

    def test_relates_to_no_change(self):
        base = self._base_snapshot()
        tracks = {
            "track_rel": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "relates-to", "item": "auth.oauth2"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        # No status change
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")

    def test_skips_baked_tracks(self):
        base = self._base_snapshot()
        base.snapshot_after_tracks = ["track_old"]
        tracks = {
            "track_old": {
                "created": "2026-03-20",
                "spec_refs": [
                    {"action": "fulfills", "item": "auth.oauth2"},
                ],
            },
            "track_new": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "fulfills", "item": "api.rate-limiting"},
                ],
            },
        }
        result = materialize(base, tracks=tracks)
        # track_old's fulfills should be skipped (already baked)
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")
        # track_new's fulfills should apply
        self.assertEqual(result.get_item("api.rate-limiting")["status"],
                         "fulfilled")

    def test_ordering_by_created_date(self):
        """Tracks are applied in created-date order."""
        snap = SpecSnapshot()
        snap.add_item("auth.flow", "Auth Flow")
        tracks = {
            "track_b": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "modifies", "item": "auth.flow",
                     "description": "Second"},
                ],
            },
            "track_a": {
                "created": "2026-03-21",
                "spec_refs": [
                    {"action": "modifies", "item": "auth.flow",
                     "description": "First"},
                ],
            },
        }
        result = materialize(snap, tracks=tracks)
        # track_a applied first (earlier date), then track_b overwrites
        self.assertEqual(result.get_item("auth.flow")["description"], "Second")

    def test_multiple_refs_per_track(self):
        base = self._base_snapshot()
        tracks = {
            "track_multi": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "fulfills", "item": "auth.oauth2"},
                    {"action": "adds", "item": "auth.mfa",
                     "title": "MFA", "description": "TOTP"},
                    {"action": "deprecates", "item": "api.rate-limiting"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        self.assertEqual(result.get_item("auth.oauth2")["status"], "fulfilled")
        self.assertTrue(result.has_item("auth.mfa"))
        self.assertEqual(result.get_item("api.rate-limiting")["status"],
                         "deprecated")

    def test_unknown_action_ignored(self):
        base = self._base_snapshot()
        tracks = {
            "track_x": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "future-action", "item": "auth.oauth2"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")

    def test_ref_to_nonexistent_item(self):
        """Actions on non-existent items are no-ops (except adds)."""
        base = self._base_snapshot()
        tracks = {
            "track_x": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "fulfills", "item": "nonexistent.item"},
                    {"action": "modifies", "item": "nonexistent.item",
                     "title": "X"},
                    {"action": "deprecates", "item": "nonexistent.item"},
                    {"action": "moves", "item": "nonexistent.item",
                     "to": "new.place"},
                ],
            }
        }
        result = materialize(base, tracks=tracks)
        self.assertEqual(len(result.items), 2)  # no new items created


class TestSnapshotFromMaterialized(unittest.TestCase):

    def test_creates_new_version(self):
        mat = SpecSnapshot()
        mat.version = 3
        mat.add_item("auth.oauth2", "OAuth2")
        mat.items["auth.oauth2"]["status"] = "fulfilled"
        mat.snapshot_after_tracks = ["old_track"]

        snap = snapshot_from_materialized(mat, ["new_track_a", "new_track_b"])
        self.assertEqual(snap.version, 4)
        self.assertEqual(snap.snapshot_date, today_iso())
        self.assertIn("old_track", snap.snapshot_after_tracks)
        self.assertIn("new_track_a", snap.snapshot_after_tracks)
        self.assertIn("new_track_b", snap.snapshot_after_tracks)
        self.assertEqual(snap.get_item("auth.oauth2")["status"], "fulfilled")


class TestSaveLoadRoundtrip(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_full_roundtrip(self):
        snap = SpecSnapshot()
        snap.version = 2
        snap.snapshot_after_tracks = ["track_a", "track_b"]
        snap.add_item("auth.oauth2", "OAuth2", category="auth",
                      priority="high", description="OAuth2 flow")
        snap.add_item("api.rate-limiting", "Rate Limiting", category="api")
        snap.items["auth.oauth2"]["fulfilled_by"] = "track_a"
        snap.items["auth.oauth2"]["status"] = "fulfilled"

        path = self.tmpdir / "spec.yaml"
        snap.save(path)

        loaded = SpecSnapshot.load(path)
        self.assertEqual(loaded.version, 2)
        self.assertEqual(sorted(loaded.snapshot_after_tracks),
                         ["track_a", "track_b"])
        self.assertEqual(loaded.get_item("auth.oauth2")["status"], "fulfilled")
        self.assertEqual(loaded.get_item("auth.oauth2")["fulfilled_by"],
                         "track_a")


class TestSpecOp(unittest.TestCase):
    """Test standalone spec operation files."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.spec_dir = self.tmpdir / "spec"
        self.spec_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_create_and_load(self):
        op = SpecOp(name="test-op", author="architect-1",
                    description="Initial spec")
        op.add_operation("adds", "auth.oauth2",
                         title="OAuth2", priority="high",
                         description="OAuth2 auth flow")
        op.add_operation("adds", "api.rate-limiting",
                         title="Rate Limiting")

        path = self.spec_dir / "test-op.yaml"
        op.save(path)

        loaded = SpecOp.load(path)
        self.assertEqual(loaded.author, "architect-1")
        self.assertEqual(len(loaded.operations), 2)
        self.assertEqual(loaded.operations[0]["action"], "adds")
        self.assertEqual(loaded.operations[0]["item"], "auth.oauth2")

    def test_create_spec_op_with_timestamp(self):
        ops = [
            {"action": "adds", "item": "auth.oauth2", "title": "OAuth2"},
        ]
        path = create_spec_op(self.spec_dir, ops,
                              author="test", slug="init")
        self.assertTrue(path.exists())
        # Filename has timestamp + random hash + slug
        self.assertIn("-init.yaml", path.name)
        # Should have the random hash (6 hex chars)
        parts = path.stem.split("-")
        self.assertTrue(len(parts) >= 4)  # date-time-hash-slug

    def test_load_spec_ops_sorted(self):
        # Create ops with ordered filenames
        for i, name in enumerate(["20260321-a.yaml", "20260322-b.yaml",
                                   "20260320-c.yaml"]):
            op = SpecOp(name=name.replace(".yaml", ""))
            op.add_operation("adds", f"item.{i}", title=f"Item {i}")
            op.save(self.spec_dir / name)

        ops = load_spec_ops(self.spec_dir)
        self.assertEqual(len(ops), 3)
        # Should be sorted by filename
        self.assertEqual(ops[0].name, "20260320-c")
        self.assertEqual(ops[1].name, "20260321-a")
        self.assertEqual(ops[2].name, "20260322-b")


class TestMaterializeWithOps(unittest.TestCase):
    """Test materialization with spec operation files."""

    def test_ops_applied_before_tracks(self):
        snap = SpecSnapshot()
        # Spec op adds an item
        op = SpecOp(name="init")
        op.add_operation("adds", "auth.oauth2",
                         title="OAuth2", priority="high")
        # Track fulfills it
        tracks = {
            "track_a": {
                "created": "2026-03-22",
                "spec_refs": [
                    {"action": "fulfills", "item": "auth.oauth2"},
                ],
            }
        }
        result = materialize(snap, spec_ops=[op], tracks=tracks)
        self.assertTrue(result.has_item("auth.oauth2"))
        self.assertEqual(result.get_item("auth.oauth2")["status"], "fulfilled")

    def test_ops_only_no_tracks(self):
        snap = SpecSnapshot()
        op = SpecOp(name="init")
        op.add_operation("adds", "auth.oauth2", title="OAuth2")
        op.add_operation("adds", "api.rate", title="Rate Limiting")
        result = materialize(snap, spec_ops=[op])
        self.assertEqual(len(result.items), 2)

    def test_baked_ops_skipped(self):
        snap = SpecSnapshot()
        snap.snapshot_after_ops = ["old-op"]
        snap.add_item("auth.oauth2", "OAuth2")

        old_op = SpecOp(name="old-op")
        old_op.add_operation("deprecates", "auth.oauth2")

        result = materialize(snap, spec_ops=[old_op])
        # Should be skipped — already baked
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")

    def test_unfulfills_action(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2")
        snap.items["auth.oauth2"]["status"] = "fulfilled"
        snap.items["auth.oauth2"]["fulfilled_by"] = "old_track"

        op = SpecOp(name="revert")
        op.add_operation("unfulfills", "auth.oauth2",
                         reason="Implementation was incomplete")

        result = materialize(snap, spec_ops=[op])
        item = result.get_item("auth.oauth2")
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["unfulfill_reason"],
                         "Implementation was incomplete")
        self.assertNotIn("fulfilled_by", item)

    def test_unfulfills_noop_if_not_fulfilled(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2")  # status=active

        op = SpecOp(name="revert")
        op.add_operation("unfulfills", "auth.oauth2", reason="test")

        result = materialize(snap, spec_ops=[op])
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")


class TestValidation(unittest.TestCase):
    """Test spec_refs and spec_ops validation."""

    def _base_spec(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2")
        snap.add_item("api.rate", "Rate Limiting")
        snap.items["auth.oauth2"]["status"] = "fulfilled"
        return snap

    def test_valid_track_refs(self):
        spec = self._base_spec()
        refs = [
            {"action": "fulfills", "item": "api.rate"},
            {"action": "relates-to", "item": "auth.oauth2"},
        ]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(errors, [])

    def test_track_ref_rejects_structural_actions(self):
        spec = self._base_spec()
        refs = [
            {"action": "adds", "item": "new.thing", "title": "New"},
            {"action": "modifies", "item": "auth.oauth2", "title": "Changed"},
            {"action": "deprecates", "item": "api.rate"},
        ]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(len(errors), 3)
        for err in errors:
            self.assertIn("spec operation", err)

    def test_track_ref_missing_item(self):
        spec = self._base_spec()
        refs = [{"action": "fulfills", "item": "nonexistent"}]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(len(errors), 1)
        self.assertIn("not found", errors[0])

    def test_track_ref_fulfills_deprecated(self):
        spec = self._base_spec()
        spec.items["api.rate"]["status"] = "deprecated"
        refs = [{"action": "fulfills", "item": "api.rate"}]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(len(errors), 1)
        self.assertIn("deprecated", errors[0])

    def test_valid_spec_ops(self):
        spec = self._base_spec()
        ops = [
            {"action": "adds", "item": "auth.mfa", "title": "MFA"},
            {"action": "modifies", "item": "api.rate",
             "description": "Updated"},
            {"action": "unfulfills", "item": "auth.oauth2",
             "reason": "Incomplete"},
        ]
        errors = validate_spec_ops(spec, ops)
        self.assertEqual(errors, [])

    def test_spec_ops_rejects_track_actions(self):
        spec = self._base_spec()
        ops = [
            {"action": "fulfills", "item": "api.rate"},
            {"action": "relates-to", "item": "auth.oauth2"},
        ]
        errors = validate_spec_ops(spec, ops)
        self.assertEqual(len(errors), 2)
        for err in errors:
            self.assertIn("track spec_refs", err)

    def test_spec_ops_unfulfills_requires_reason(self):
        spec = self._base_spec()
        ops = [{"action": "unfulfills", "item": "auth.oauth2"}]
        errors = validate_spec_ops(spec, ops)
        self.assertIn("requires 'reason'",
                       " ".join(errors))

    def test_spec_ops_unfulfills_requires_fulfilled_status(self):
        spec = self._base_spec()
        ops = [{"action": "unfulfills", "item": "api.rate",
                "reason": "test"}]
        errors = validate_spec_ops(spec, ops)
        self.assertIn("not fulfilled", " ".join(errors))

    def test_spec_ops_adds_duplicate(self):
        spec = self._base_spec()
        ops = [{"action": "adds", "item": "auth.oauth2", "title": "Dup"}]
        errors = validate_spec_ops(spec, ops)
        self.assertIn("already exists", " ".join(errors))

    def test_spec_ops_moves_validation(self):
        spec = self._base_spec()
        # Missing 'to'
        ops = [{"action": "moves", "item": "auth.oauth2"}]
        errors = validate_spec_ops(spec, ops)
        self.assertIn("requires 'to'", " ".join(errors))

        # Target already exists
        ops = [{"action": "moves", "item": "auth.oauth2", "to": "api.rate"}]
        errors = validate_spec_ops(spec, ops)
        self.assertIn("already exists", " ".join(errors))


class TestDraftWorkflow(unittest.TestCase):
    """Test draft accumulation, finalization, and safety checks."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.spec_dir = self.tmpdir / "spec"
        self.spec_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_draft_add_creates_file(self):
        path = draft_add(self.spec_dir, "architect-1", "adds", "auth.oauth2",
                         title="OAuth2", priority="high")
        self.assertTrue(path.exists())
        self.assertIn("_draft-architect-1", path.name)

    def test_draft_add_accumulates(self):
        draft_add(self.spec_dir, "architect-1", "adds", "auth.oauth2",
                  title="OAuth2")
        draft_add(self.spec_dir, "architect-1", "adds", "api.rate",
                  title="Rate Limiting")
        draft_add(self.spec_dir, "architect-1", "adds", "auth.mfa",
                  title="MFA")

        op = draft_load(self.spec_dir, "architect-1")
        self.assertIsNotNone(op)
        self.assertEqual(len(op.operations), 3)

    def test_draft_survives_reload(self):
        """Simulate context compression — draft persists on disk."""
        draft_add(self.spec_dir, "arch-1", "adds", "auth.oauth2",
                  title="OAuth2")
        draft_add(self.spec_dir, "arch-1", "adds", "api.rate",
                  title="Rate")

        # Simulate restart: clear Python state, reload from disk
        op = draft_load(self.spec_dir, "arch-1")
        self.assertEqual(len(op.operations), 2)

        # Continue accumulating
        draft_add(self.spec_dir, "arch-1", "modifies", "api.rate",
                  description="Updated")
        op = draft_load(self.spec_dir, "arch-1")
        self.assertEqual(len(op.operations), 3)

    def test_draft_finalize(self):
        draft_add(self.spec_dir, "arch-1", "adds", "auth.oauth2",
                  title="OAuth2")
        draft_add(self.spec_dir, "arch-1", "adds", "api.rate",
                  title="Rate")

        path = draft_finalize(self.spec_dir, "arch-1",
                              description="Initial spec")
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertFalse(path.name.startswith("_draft-"))

        # Draft should be gone
        self.assertIsNone(draft_load(self.spec_dir, "arch-1"))

        # Finalized file should have the operations
        op = SpecOp.load(path)
        self.assertEqual(len(op.operations), 2)
        self.assertEqual(op.author, "arch-1")

    def test_draft_finalize_no_draft(self):
        result = draft_finalize(self.spec_dir, "nonexistent")
        self.assertIsNone(result)

    def test_draft_discard(self):
        draft_add(self.spec_dir, "arch-1", "adds", "auth.oauth2",
                  title="OAuth2")
        self.assertTrue(draft_discard(self.spec_dir, "arch-1"))
        self.assertIsNone(draft_load(self.spec_dir, "arch-1"))

    def test_draft_discard_nonexistent(self):
        self.assertFalse(draft_discard(self.spec_dir, "nonexistent"))

    def test_draft_list(self):
        draft_add(self.spec_dir, "arch-1", "adds", "a.b", title="A")
        draft_add(self.spec_dir, "arch-2", "adds", "c.d", title="C")

        drafts = draft_list(self.spec_dir)
        self.assertEqual(len(drafts), 2)
        holders = [h for h, _ in drafts]
        self.assertIn("arch-1", holders)
        self.assertIn("arch-2", holders)

    def test_load_spec_ops_excludes_drafts(self):
        """Drafts are not included in materialization."""
        # Create a finalized op
        create_spec_op(self.spec_dir,
                       [{"action": "adds", "item": "a.b", "title": "A"}],
                       author="test")
        # Create a draft
        draft_add(self.spec_dir, "arch-1", "adds", "c.d", title="C")

        ops = load_spec_ops(self.spec_dir)
        self.assertEqual(len(ops), 1)  # only finalized

    def test_check_uncommitted_drafts(self):
        draft_add(self.spec_dir, "arch-1", "adds", "a.b", title="A")
        warnings = check_uncommitted_drafts(self.spec_dir)
        self.assertEqual(len(warnings), 1)
        self.assertIn("_draft-arch-1", warnings[0])
        self.assertIn("Finalize", warnings[0])

    def test_check_uncommitted_drafts_none(self):
        warnings = check_uncommitted_drafts(self.spec_dir)
        self.assertEqual(warnings, [])

    def test_multiple_holders_independent(self):
        """Different holders have independent drafts."""
        draft_add(self.spec_dir, "arch-1", "adds", "a.b", title="A")
        draft_add(self.spec_dir, "arch-2", "adds", "c.d", title="C")

        op1 = draft_load(self.spec_dir, "arch-1")
        op2 = draft_load(self.spec_dir, "arch-2")
        self.assertEqual(len(op1.operations), 1)
        self.assertEqual(len(op2.operations), 1)

        # Finalize only arch-1
        draft_finalize(self.spec_dir, "arch-1")
        self.assertIsNone(draft_load(self.spec_dir, "arch-1"))
        self.assertIsNotNone(draft_load(self.spec_dir, "arch-2"))


if __name__ == "__main__":
    unittest.main()
