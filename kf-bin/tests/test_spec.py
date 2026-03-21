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
    fulfillment_status, spec_item_tracks, spec_refs_for_track,
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
    """Test materialization — only spec operations change spec state."""

    def _base_snapshot(self):
        snap = SpecSnapshot()
        snap.add_item("auth.oauth2", "OAuth2", category="auth",
                      priority="high", description="OAuth2 flow")
        snap.add_item("api.rate-limiting", "Rate Limiting", category="api",
                      priority="medium")
        return snap

    def test_no_ops(self):
        base = self._base_snapshot()
        result = materialize(base)
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")

    def test_fulfills_via_op(self):
        base = self._base_snapshot()
        op = SpecOp(name="fulfill-auth")
        op.add_operation("fulfills", "auth.oauth2")
        result = materialize(base, spec_ops=[op])
        self.assertEqual(result.get_item("auth.oauth2")["status"], "fulfilled")
        self.assertEqual(result.get_item("auth.oauth2")["fulfilled_by"],
                         "op:fulfill-auth")

    def test_adds_via_op(self):
        base = self._base_snapshot()
        op = SpecOp(name="add-mfa")
        op.add_operation("adds", "auth.mfa",
                         title="MFA", priority="high",
                         description="TOTP-based MFA")
        result = materialize(base, spec_ops=[op])
        self.assertEqual(len(result.items), 3)
        mfa = result.get_item("auth.mfa")
        self.assertEqual(mfa["title"], "MFA")
        self.assertEqual(mfa["added_by"], "op:add-mfa")
        self.assertEqual(mfa["category"], "auth")  # auto-derived

    def test_adds_idempotent(self):
        """If item already exists, adds is a no-op."""
        base = self._base_snapshot()
        op = SpecOp(name="dup")
        op.add_operation("adds", "auth.oauth2", title="Different Title")
        result = materialize(base, spec_ops=[op])
        # Original title preserved (first creator wins)
        self.assertEqual(result.get_item("auth.oauth2")["title"], "OAuth2")

    def test_modifies(self):
        base = self._base_snapshot()
        op = SpecOp(name="mod")
        op.add_operation("modifies", "api.rate-limiting",
                         description="Sliding window algorithm")
        result = materialize(base, spec_ops=[op])
        item = result.get_item("api.rate-limiting")
        self.assertEqual(item["description"], "Sliding window algorithm")
        self.assertEqual(item["modified_by"], "op:mod")
        self.assertEqual(item["title"], "Rate Limiting")

    def test_deprecates(self):
        base = self._base_snapshot()
        op = SpecOp(name="dep")
        op.add_operation("deprecates", "auth.oauth2")
        result = materialize(base, spec_ops=[op])
        self.assertEqual(result.get_item("auth.oauth2")["status"],
                         "deprecated")

    def test_moves(self):
        base = self._base_snapshot()
        op = SpecOp(name="mv")
        op.add_operation("moves", "auth.oauth2", to="identity.oauth2")
        result = materialize(base, spec_ops=[op])
        self.assertFalse(result.has_item("auth.oauth2"))
        self.assertTrue(result.has_item("identity.oauth2"))
        item = result.get_item("identity.oauth2")
        self.assertEqual(item["title"], "OAuth2")
        self.assertEqual(item["moved_by"], "op:mv")
        self.assertEqual(item["moved_from"], "auth.oauth2")
        self.assertEqual(item["category"], "identity")

    def test_moves_with_children(self):
        snap = SpecSnapshot()
        snap.add_item("legacy.auth", "Auth", category="legacy")
        snap.add_item("legacy.auth.session", "Session Auth",
                      category="legacy")
        snap.add_item("legacy.auth.token", "Token Auth", category="legacy")
        snap.add_item("other.thing", "Other", category="other")

        op = SpecOp(name="mv")
        op.add_operation("moves", "legacy.auth", to="identity.auth")
        result = materialize(snap, spec_ops=[op])
        self.assertFalse(result.has_item("legacy.auth"))
        self.assertFalse(result.has_item("legacy.auth.session"))
        self.assertTrue(result.has_item("identity.auth"))
        self.assertTrue(result.has_item("identity.auth.session"))
        self.assertTrue(result.has_item("identity.auth.token"))
        self.assertTrue(result.has_item("other.thing"))
        self.assertEqual(
            result.get_item("identity.auth.session")["category"], "identity")

    def test_skips_baked_ops(self):
        base = self._base_snapshot()
        base.snapshot_after_ops = ["old-op"]
        old_op = SpecOp(name="old-op")
        old_op.add_operation("fulfills", "auth.oauth2")
        new_op = SpecOp(name="new-op")
        new_op.add_operation("fulfills", "api.rate-limiting")
        result = materialize(base, spec_ops=[old_op, new_op])
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")
        self.assertEqual(result.get_item("api.rate-limiting")["status"],
                         "fulfilled")

    def test_multiple_ops_in_order(self):
        """Ops applied in list order (which is filename sort order)."""
        snap = SpecSnapshot()
        snap.add_item("auth.flow", "Auth Flow")
        op1 = SpecOp(name="20260321-first")
        op1.add_operation("modifies", "auth.flow", description="First")
        op2 = SpecOp(name="20260322-second")
        op2.add_operation("modifies", "auth.flow", description="Second")
        result = materialize(snap, spec_ops=[op1, op2])
        self.assertEqual(result.get_item("auth.flow")["description"], "Second")

    def test_multiple_operations_per_op_file(self):
        base = self._base_snapshot()
        op = SpecOp(name="batch")
        op.add_operation("fulfills", "auth.oauth2")
        op.add_operation("adds", "auth.mfa", title="MFA", description="TOTP")
        op.add_operation("deprecates", "api.rate-limiting")
        result = materialize(base, spec_ops=[op])
        self.assertEqual(result.get_item("auth.oauth2")["status"], "fulfilled")
        self.assertTrue(result.has_item("auth.mfa"))
        self.assertEqual(result.get_item("api.rate-limiting")["status"],
                         "deprecated")

    def test_unknown_action_ignored(self):
        base = self._base_snapshot()
        op = SpecOp(name="future")
        op.add_operation("future-action", "auth.oauth2")
        result = materialize(base, spec_ops=[op])
        self.assertEqual(result.get_item("auth.oauth2")["status"], "active")

    def test_op_on_nonexistent_item(self):
        """Actions on non-existent items are no-ops (except adds)."""
        base = self._base_snapshot()
        op = SpecOp(name="bad")
        op.add_operation("fulfills", "nonexistent.item")
        op.add_operation("modifies", "nonexistent.item", title="X")
        op.add_operation("deprecates", "nonexistent.item")
        op.add_operation("moves", "nonexistent.item", to="new.place")
        result = materialize(base, spec_ops=[op])
        self.assertEqual(len(result.items), 2)


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

    def test_ops_add_then_fulfill(self):
        snap = SpecSnapshot()
        # First op adds an item
        op1 = SpecOp(name="01-init")
        op1.add_operation("adds", "auth.oauth2",
                          title="OAuth2", priority="high")
        # Second op fulfills it (after assessment)
        op2 = SpecOp(name="02-fulfill")
        op2.add_operation("fulfills", "auth.oauth2")
        result = materialize(snap, spec_ops=[op1, op2])
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
            {"action": "required-for", "item": "api.rate"},
            {"action": "relates-to", "item": "auth.oauth2"},
        ]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(errors, [])

    def test_track_ref_rejects_spec_op_actions(self):
        spec = self._base_spec()
        refs = [
            {"action": "adds", "item": "new.thing", "title": "New"},
            {"action": "fulfills", "item": "api.rate"},
            {"action": "modifies", "item": "auth.oauth2", "title": "Changed"},
            {"action": "deprecates", "item": "api.rate"},
        ]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(len(errors), 4)
        for err in errors:
            self.assertIn("spec operation", err)

    def test_track_ref_missing_item(self):
        spec = self._base_spec()
        refs = [{"action": "required-for", "item": "nonexistent"}]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(len(errors), 1)
        self.assertIn("not found", errors[0])

    def test_track_ref_required_for_deprecated(self):
        spec = self._base_spec()
        spec.items["api.rate"]["status"] = "deprecated"
        refs = [{"action": "required-for", "item": "api.rate"}]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(len(errors), 1)
        self.assertIn("deprecated", errors[0])

    def test_valid_spec_ops(self):
        spec = self._base_spec()
        ops = [
            {"action": "adds", "item": "auth.mfa", "title": "MFA"},
            {"action": "fulfills", "item": "api.rate"},
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
            {"action": "required-for", "item": "api.rate"},
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


class TestItemTypes(unittest.TestCase):
    """Test product vs technical spec item types."""

    def test_auto_derive_product_type(self):
        snap = SpecSnapshot()
        snap.add_item("product.cats.browse", "Browse Cats")
        item = snap.get_item("product.cats.browse")
        self.assertEqual(item["type"], "product")
        self.assertEqual(item["category"], "cats")

    def test_auto_derive_technical_type(self):
        snap = SpecSnapshot()
        snap.add_item("tech.api.cursor-pagination", "Cursor Pagination")
        item = snap.get_item("tech.api.cursor-pagination")
        self.assertEqual(item["type"], "technical")
        self.assertEqual(item["category"], "api")

    def test_explicit_type_override(self):
        snap = SpecSnapshot()
        snap.add_item("custom.thing", "Custom", type_="technical")
        self.assertEqual(snap.get_item("custom.thing")["type"], "technical")

    def test_materialize_preserves_type(self):
        snap = SpecSnapshot()
        op = SpecOp(name="init")
        op.add_operation("adds", "product.cats.browse",
                         title="Browse Cats", description="User browses cats")
        op.add_operation("adds", "tech.api.cursor-pagination",
                         title="Cursor Pagination",
                         description="Use cursor-based pagination")
        result = materialize(snap, spec_ops=[op])
        self.assertEqual(result.get_item("product.cats.browse")["type"],
                         "product")
        self.assertEqual(
            result.get_item("tech.api.cursor-pagination")["type"],
            "technical")

    def test_constrained_by_ref(self):
        spec = SpecSnapshot()
        spec.add_item("product.cats.browse", "Browse Cats")
        spec.add_item("tech.api.cursor-pagination", "Cursor Pagination")
        refs = [
            {"action": "required-for", "item": "product.cats.browse"},
            {"action": "constrained-by",
             "item": "tech.api.cursor-pagination"},
        ]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(errors, [])

    def test_constrained_by_product_item_warns(self):
        spec = SpecSnapshot()
        spec.add_item("product.cats.browse", "Browse Cats")
        refs = [
            {"action": "constrained-by", "item": "product.cats.browse"},
        ]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(len(errors), 1)
        self.assertIn("technical", errors[0])

    def test_required_for_technical_item_warns(self):
        spec = SpecSnapshot()
        spec.add_item("tech.api.pagination", "Pagination")
        refs = [
            {"action": "required-for", "item": "tech.api.pagination"},
        ]
        errors = validate_spec_refs(spec, refs)
        self.assertEqual(len(errors), 1)
        self.assertIn("product", errors[0])

    def test_fulfillment_status_includes_constrained(self):
        spec = SpecSnapshot()
        spec.add_item("tech.api.pagination", "Pagination")
        tracks = {
            "track_a": {
                "status": "in-progress", "title": "API List",
                "spec_refs": [{"action": "constrained-by",
                               "item": "tech.api.pagination"}],
            },
        }
        result = fulfillment_status(spec, tracks)
        fs = result["tech.api.pagination"]
        self.assertEqual(len(fs["constrained_tracks"]), 1)
        self.assertEqual(fs["constrained_tracks"][0]["id"], "track_a")

    def test_spec_item_tracks_includes_constrained(self):
        spec = SpecSnapshot()
        spec.add_item("tech.db.read-replicas", "Read Replicas")
        tracks = {
            "track_a": {
                "status": "pending", "title": "DB Query Layer",
                "type": "feature",
                "spec_refs": [{"action": "constrained-by",
                               "item": "tech.db.read-replicas"}],
            },
        }
        result = spec_item_tracks(spec, "tech.db.read-replicas", tracks)
        self.assertEqual(len(result["constrained_tracks"]), 1)
        self.assertEqual(result["type"], "technical")


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


class TestFulfillmentStatus(unittest.TestCase):
    """Test fulfillment readiness computation from track spec_refs."""

    def test_no_tracks(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        result = fulfillment_status(spec, {})
        self.assertFalse(result["auth.oauth2"]["has_requirements"])
        self.assertFalse(result["auth.oauth2"]["ready_for_assessment"])
        self.assertEqual(result["auth.oauth2"]["total_required"], 0)

    def test_all_required_tracks_complete(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        tracks = {
            "track_a": {
                "status": "completed", "title": "Auth Part 1",
                "spec_refs": [{"action": "required-for",
                               "item": "auth.oauth2"}],
            },
            "track_b": {
                "status": "completed", "title": "Auth Part 2",
                "spec_refs": [{"action": "required-for",
                               "item": "auth.oauth2"}],
            },
        }
        result = fulfillment_status(spec, tracks)
        fs = result["auth.oauth2"]
        self.assertTrue(fs["has_requirements"])
        self.assertTrue(fs["ready_for_assessment"])
        self.assertEqual(fs["completed_required"], 2)
        self.assertEqual(fs["total_required"], 2)
        # required_tracks now contains dicts with id/status/title
        self.assertEqual(len(fs["required_tracks"]), 2)
        self.assertEqual(fs["required_tracks"][0]["status"], "completed")

    def test_some_tracks_pending(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        tracks = {
            "track_a": {
                "status": "completed", "title": "A",
                "spec_refs": [{"action": "required-for",
                               "item": "auth.oauth2"}],
            },
            "track_b": {
                "status": "pending", "title": "B",
                "spec_refs": [{"action": "required-for",
                               "item": "auth.oauth2"}],
            },
        }
        result = fulfillment_status(spec, tracks)
        fs = result["auth.oauth2"]
        self.assertTrue(fs["has_requirements"])
        self.assertFalse(fs["ready_for_assessment"])
        self.assertEqual(fs["completed_required"], 1)
        self.assertEqual(fs["total_required"], 2)

    def test_related_tracks_included(self):
        """relates-to tracks appear in related_tracks but don't affect readiness."""
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        tracks = {
            "track_req": {
                "status": "completed", "title": "Required",
                "spec_refs": [{"action": "required-for",
                               "item": "auth.oauth2"}],
            },
            "track_rel": {
                "status": "pending", "title": "Related",
                "spec_refs": [{"action": "relates-to",
                               "item": "auth.oauth2"}],
            },
        }
        result = fulfillment_status(spec, tracks)
        fs = result["auth.oauth2"]
        # Required track is complete → ready
        self.assertTrue(fs["ready_for_assessment"])
        # Related track shows up but doesn't block
        self.assertEqual(len(fs["related_tracks"]), 1)
        self.assertEqual(fs["related_tracks"][0]["id"], "track_rel")

    def test_relates_to_only_not_required(self):
        """Only relates-to links → has_requirements is False."""
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        tracks = {
            "track_a": {
                "status": "pending", "title": "A",
                "spec_refs": [{"action": "relates-to",
                               "item": "auth.oauth2"}],
            },
        }
        result = fulfillment_status(spec, tracks)
        self.assertFalse(result["auth.oauth2"]["has_requirements"])
        self.assertEqual(len(result["auth.oauth2"]["related_tracks"]), 1)

    def test_multiple_items(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        spec.add_item("api.rate", "Rate Limiting")
        tracks = {
            "track_auth": {
                "status": "completed", "title": "Auth",
                "spec_refs": [{"action": "required-for",
                               "item": "auth.oauth2"}],
            },
            "track_api": {
                "status": "in-progress", "title": "API",
                "spec_refs": [{"action": "required-for",
                               "item": "api.rate"}],
            },
        }
        result = fulfillment_status(spec, tracks)
        self.assertTrue(result["auth.oauth2"]["ready_for_assessment"])
        self.assertFalse(result["api.rate"]["ready_for_assessment"])

    def test_deprecated_items_excluded(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        spec.items["auth.oauth2"]["status"] = "deprecated"
        result = fulfillment_status(spec, {})
        self.assertNotIn("auth.oauth2", result)

    def test_already_fulfilled_still_shown(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        spec.items["auth.oauth2"]["status"] = "fulfilled"
        result = fulfillment_status(spec, {})
        self.assertEqual(result["auth.oauth2"]["status"], "fulfilled")


class TestSpecItemTracks(unittest.TestCase):
    """Test single spec item query."""

    def test_basic_query(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2", priority="high")
        tracks = {
            "track_a": {
                "status": "completed", "title": "Auth Login", "type": "feature",
                "spec_refs": [{"action": "required-for", "item": "auth.oauth2"}],
            },
            "track_b": {
                "status": "in-progress", "title": "Auth Tokens", "type": "feature",
                "spec_refs": [{"action": "required-for", "item": "auth.oauth2"}],
            },
            "track_c": {
                "status": "pending", "title": "Auth Docs", "type": "chore",
                "spec_refs": [{"action": "relates-to", "item": "auth.oauth2"}],
            },
            "track_d": {
                "status": "completed", "title": "Unrelated", "type": "feature",
                "spec_refs": [{"action": "required-for", "item": "api.other"}],
            },
        }
        result = spec_item_tracks(spec, "auth.oauth2", tracks)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "OAuth2")
        self.assertEqual(result["priority"], "high")
        self.assertEqual(result["total_required"], 2)
        self.assertEqual(result["completed_required"], 1)
        self.assertFalse(result["ready_for_assessment"])
        self.assertEqual(len(result["related_tracks"]), 1)
        self.assertEqual(result["related_tracks"][0]["id"], "track_c")

    def test_nonexistent_item(self):
        spec = SpecSnapshot()
        self.assertIsNone(spec_item_tracks(spec, "nope", {}))

    def test_excludes_archived_by_default(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        tracks = {
            "track_a": {
                "status": "archived", "title": "Old",
                "spec_refs": [{"action": "required-for", "item": "auth.oauth2"}],
            },
            "track_b": {
                "status": "completed", "title": "Current",
                "spec_refs": [{"action": "required-for", "item": "auth.oauth2"}],
            },
        }
        result = spec_item_tracks(spec, "auth.oauth2", tracks)
        self.assertEqual(result["total_required"], 1)
        self.assertEqual(result["required_tracks"][0]["id"], "track_b")

    def test_include_archived(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        tracks = {
            "track_a": {
                "status": "archived", "title": "Old",
                "spec_refs": [{"action": "required-for", "item": "auth.oauth2"}],
            },
            "track_b": {
                "status": "completed", "title": "Current",
                "spec_refs": [{"action": "required-for", "item": "auth.oauth2"}],
            },
        }
        result = spec_item_tracks(spec, "auth.oauth2", tracks,
                                  include_archived=True)
        self.assertEqual(result["total_required"], 2)

    def test_ready_when_all_complete(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2")
        tracks = {
            "track_a": {
                "status": "completed", "title": "A",
                "spec_refs": [{"action": "required-for", "item": "auth.oauth2"}],
            },
        }
        result = spec_item_tracks(spec, "auth.oauth2", tracks)
        self.assertTrue(result["ready_for_assessment"])


class TestSpecRefsForTrack(unittest.TestCase):
    """Test per-track spec ref querying."""

    def test_basic(self):
        track = {
            "spec_refs": [
                {"action": "required-for", "item": "auth.oauth2"},
                {"action": "relates-to", "item": "api.rate"},
            ]
        }
        refs = spec_refs_for_track(track)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["action"], "required-for")
        self.assertEqual(refs[0]["item"], "auth.oauth2")

    def test_enriched_with_spec(self):
        spec = SpecSnapshot()
        spec.add_item("auth.oauth2", "OAuth2 Authentication")
        track = {
            "spec_refs": [
                {"action": "required-for", "item": "auth.oauth2"},
            ]
        }
        refs = spec_refs_for_track(track, spec=spec)
        self.assertEqual(refs[0]["item_title"], "OAuth2 Authentication")
        self.assertEqual(refs[0]["item_status"], "active")

    def test_no_spec_refs(self):
        self.assertEqual(spec_refs_for_track({}), [])
        self.assertEqual(spec_refs_for_track({"spec_refs": []}), [])

    def test_missing_spec_item(self):
        """If spec item doesn't exist, no enrichment."""
        spec = SpecSnapshot()
        track = {
            "spec_refs": [
                {"action": "required-for", "item": "nonexistent"},
            ]
        }
        refs = spec_refs_for_track(track, spec=spec)
        self.assertEqual(len(refs), 1)
        self.assertNotIn("item_title", refs[0])


if __name__ == "__main__":
    unittest.main()
