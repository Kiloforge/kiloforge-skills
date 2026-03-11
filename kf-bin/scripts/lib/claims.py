"""Track claim detection via git branches and orchestrator API."""

import json
import os
import urllib.request
from typing import Optional
from . import git


def branch_scan_claimed() -> list[tuple[str, str]]:
    """Scan git branches for implementation branches.

    Returns list of (track_id, worker_name) tuples.
    Worker name is extracted from worktree folder name if identifiable.
    """
    branches = git.branches_matching(
        "kf/feature/*", "kf/bug/*", "kf/chore/*", "kf/refactor/*",
        "feature/*", "bug/*", "chore/*", "refactor/*",
    )
    if not branches:
        return []

    # Build worktree map: branch -> worktree path
    worktree_map = {}
    for wt in git.worktree_list():
        branch = wt.get("branch", "")
        if branch:
            worktree_map[branch] = wt["path"]

    results = []
    for branch in branches:
        # Extract track ID: strip type prefix (feature/, kf/feature/, etc.)
        parts = branch.split("/")
        track_id = parts[-1] if len(parts) > 1 else branch

        worker = ""
        wt_path = worktree_map.get(branch, "")
        if wt_path:
            worker = os.path.basename(wt_path)

        results.append((track_id, worker))

    return results


def server_query_claims() -> list[tuple[str, str]]:
    """Query orchestrator claim API for claimed tracks.

    Returns list of (track_id, worker_name) tuples.
    Returns empty list if server is unreachable.
    """
    orch_url = os.environ.get("KF_ORCH_URL", "http://localhost:39517")
    try:
        req = urllib.request.Request(
            f"{orch_url}/api/tracks/claims",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=1) as resp:
            data = json.loads(resp.read())
            return [(c["track_id"], c.get("worker", "")) for c in data]
    except Exception:
        return []


def get_claimed_tracks() -> list[tuple[str, str]]:
    """Get claimed tracks: try server first, fall back to branch scan.

    Returns list of (track_id, worker_name) tuples.
    """
    claims = server_query_claims()
    if not claims:
        claims = branch_scan_claimed()
    return claims


def is_track_claimed(track_id: str) -> tuple[bool, Optional[str]]:
    """Check if a specific track is claimed.

    Returns (is_claimed, worker_name).
    """
    claims = get_claimed_tracks()
    for tid, worker in claims:
        if tid == track_id:
            return True, worker
    return False, None
