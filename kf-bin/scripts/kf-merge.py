#!/usr/bin/env python3
"""kf-merge — Unified merge protocol for Kiloforge agents.

Merges the current branch into the primary branch using a lock-protected,
rebase-based, fast-forward-only strategy. Lock is always released on error.

USAGE:
  kf-merge [OPTIONS]

OPTIONS:
  --holder NAME         Lock holder identity (default: basename of cwd)
  --timeout SECONDS     Lock acquisition timeout; 0=fail if held (default: 0)
  --verify CMD          Verification command to run post-rebase (skip if omitted)
  --registry-cmd CMD    Registry update command to run post-rebase. Runs against
                        clean rebased state, then stages and commits registry files.
                        Used by architects to avoid registry conflicts.
  --reapply CMD         Re-apply track state after conflict resolution during rebase.
                        Used by developers when registry conflicts occur.
  --conflict-strategy   How to resolve track state conflicts during rebase:
                          "theirs" (default) — accept primary branch version
                          "ours"  — accept current branch version (for reports)
  --cleanup-branch NAME Delete this branch after merge (implementation branch)

EXIT CODES:
  0  Merge succeeded
  1  Merge failed (lock released, safe to retry)
  2  Lock held by another worker (not acquired)
  3  Unresolved conflicts — lock STILL HELD, agent must resolve and retry

FLOWS:
  Metadata merge (architect — commit track content first, registry under lock):
    kf-merge --holder architect-1 --timeout 0 \
      --registry-cmd ".agent/kf/bin/kf-track.py add X --title '...' --type feature"

  Implementation merge (developer — with verification):
    kf-merge --holder developer-1 --timeout 300 \
      --verify "make test && make build && make lint" \
      --reapply ".agent/kf/bin/kf-track.py update X --status completed" \
      --cleanup-branch kf/feature/my-track
"""

import argparse
import atexit
import os
import re
import subprocess
import sys
import threading

# ── Globals ──────────────────────────────────────────────────────────────────

KF_BIN = os.path.dirname(os.path.realpath(__file__))

# Legacy centralized state files (still resolved during migration period)
LEGACY_STATE_FILES = [
    ".agent/kf/tracks.yaml",
    ".agent/kf/tracks/deps.yaml",
    ".agent/kf/tracks/conflicts.yaml",
]
REPORT_FILES = ".agent/kf/_reports/"

# Maximum rebase conflict resolution rounds (safety bound)
MAX_REBASE_ROUNDS = 50

lock_acquired = False
heartbeat_stop = threading.Event()
heartbeat_thread = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def run(cmd, *, check=True, capture=False, cwd=None, shell=False):
    """Run a command, returning CompletedProcess."""
    kwargs = dict(check=check, cwd=cwd)
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if shell:
        return subprocess.run(cmd, shell=True, **kwargs)
    return subprocess.run(cmd, **kwargs)


def run_quiet(cmd, **kwargs):
    """Run a command, suppressing stderr, not raising on failure."""
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
        **kwargs,
    )


def cleanup():
    """Release heartbeat and lock on any exit."""
    global lock_acquired, heartbeat_thread

    # Stop heartbeat thread
    if heartbeat_thread is not None:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=5)
        heartbeat_thread = None

    # Release lock
    if lock_acquired:
        run_quiet([os.path.join(KF_BIN, "kf-merge-lock.py"), "release"])
        lock_acquired = False
        print("Lock released.")


atexit.register(cleanup)


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def heartbeat_loop():
    """Background loop: send heartbeat every 30 seconds until stopped."""
    lock_script = os.path.join(KF_BIN, "kf-merge-lock.py")
    while not heartbeat_stop.wait(30):
        run_quiet([lock_script, "heartbeat"])


def _is_state_file(filepath: str) -> bool:
    """Check if a file is a track state file (auto-resolvable during rebase).

    Matches:
      - Per-track meta.yaml files: .agent/kf/tracks/*/meta.yaml
      - Legacy centralized files: tracks.yaml, deps.yaml, conflicts.yaml
    """
    # Per-track meta files (new format)
    if (filepath.endswith("/meta.yaml")
            and filepath.startswith(".agent/kf/tracks/")):
        return True
    # Spec snapshot (event-sourced, accept primary on conflict)
    if filepath == ".agent/kf/spec.yaml":
        return True
    # Legacy centralized files
    if filepath in LEGACY_STATE_FILES:
        return True
    return False


def _get_conflicting_files() -> list[str]:
    """Return list of files with unresolved merge conflicts."""
    result = run_quiet(["git", "diff", "--name-only", "--diff-filter=U"])
    if not result.stdout:
        return []
    return [f for f in result.stdout.strip().splitlines() if f.strip()]


def _resolve_state_conflicts(conflict_strategy: str, state_files: list[str]):
    """Resolve state file conflicts by accepting primary branch version.

    During rebase, git reverses --ours/--theirs semantics:
      --ours   = branch being rebased ONTO (primary — latest state)
      --theirs = commit being REPLAYED (worker's old commit — stale)
    To accept primary branch's version of state files, use --ours.
    """
    for f in state_files:
        r = run_quiet(["git", "checkout", "--ours", f])
        if r.returncode == 0:
            run_quiet(["git", "add", f])


def _resolve_report_conflicts():
    """For 'ours' strategy, keep worker's report files."""
    r = run_quiet(["git", "checkout", "--theirs", REPORT_FILES])
    if r.returncode == 0:
        run_quiet(["git", "add", REPORT_FILES])


def _stage_state_files():
    """Stage all track state files (per-track meta + spec + legacy)."""
    # Stage per-track meta.yaml files
    run_quiet(["git", "add", ".agent/kf/tracks/*/meta.yaml"])
    # Stage spec snapshot
    run_quiet(["git", "add", ".agent/kf/spec.yaml"])
    # Stage legacy files if they exist
    for f in LEGACY_STATE_FILES:
        run_quiet(["git", "add", f])


# ── Argument parsing ─────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Unified merge protocol for Kiloforge agents.",
        add_help=True,
    )
    parser.add_argument(
        "--holder", default=os.path.basename(os.getcwd()),
        help="Lock holder identity (default: basename of cwd)",
    )
    parser.add_argument(
        "--timeout", type=int, default=0,
        help="Lock acquisition timeout; 0=fail if held (default: 0)",
    )
    parser.add_argument(
        "--verify", dest="verify_cmd", default="",
        help="Verification command to run post-rebase",
    )
    parser.add_argument(
        "--registry-cmd", dest="registry_cmd", default="",
        help="Registry update command to run post-rebase",
    )
    parser.add_argument(
        "--reapply", dest="reapply_cmd", default="",
        help="Re-apply track state after conflict resolution during rebase",
    )
    parser.add_argument(
        "--conflict-strategy", dest="conflict_strategy", default="theirs",
        choices=["theirs", "ours"],
        help='How to resolve track state conflicts: "theirs" (default) or "ours"',
    )
    parser.add_argument(
        "--cleanup-branch", dest="cleanup_branch", default="",
        help="Delete this branch after merge",
    )
    return parser.parse_args()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    global lock_acquired, heartbeat_thread

    args = parse_args()

    # ── Step 1: Resolve primary branch ───────────────────────────────────────
    result = run(
        [os.path.join(KF_BIN, "kf-primary-branch.py")],
        capture=True,
    )
    primary_branch = result.stdout.strip()

    result = run(["git", "branch", "--show-current"], capture=True)
    current_branch = result.stdout.strip()

    if current_branch == primary_branch:
        die(f"Cannot merge: you are already on the primary branch ({primary_branch}). "
            f"Agents must work on their own branch, not the primary branch directly.")

    print(f"Merge: {current_branch} -> {primary_branch} (holder: {args.holder})")

    # ── Step 2: Locate primary branch worktree ───────────────────────────────
    result = run(["git", "worktree", "list"], capture=True)
    main_worktree = ""
    first_worktree = ""
    for line in result.stdout.splitlines():
        if not first_worktree:
            first_worktree = line.split()[0]
        if re.search(rf"\[{re.escape(primary_branch)}\]", line):
            main_worktree = line.split()[0]
            break
    if not main_worktree:
        main_worktree = first_worktree

    print(f"Primary worktree: {main_worktree}")

    # ── Step 3: Acquire branch lock ──────────────────────────────────────────
    print(f"Acquiring branch lock (timeout: {args.timeout}s)...")
    lock_result = run(
        [os.path.join(KF_BIN, "kf-merge-lock.py"), "acquire", "--timeout", str(args.timeout)],
        check=False,
    )
    if lock_result.returncode != 0:
        print("BRANCH LOCK HELD — another worker is merging. Retry later.")
        sys.exit(2)

    lock_acquired = True
    print("Lock acquired.")

    # ── Step 4: Start heartbeat ──────────────────────────────────────────────
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    # ── Step 4b: Check for uncommitted spec drafts ────────────────────────
    spec_dir = os.path.join(os.getcwd(), ".agent", "kf", "spec")
    if os.path.isdir(spec_dir):
        drafts = [f for f in os.listdir(spec_dir) if f.startswith("_draft-")]
        if drafts:
            print("WARNING: Uncommitted spec drafts found:", file=sys.stderr)
            for d in drafts:
                print(f"  {d}", file=sys.stderr)
            print("Finalize with `kf-track spec op finalize` or discard.",
                  file=sys.stderr)
            die("Cannot merge with uncommitted spec drafts. "
                "Finalize or discard them first.")

    # ── Step 5: Rebase onto primary branch (with conflict resolution loop) ───
    #
    # If the worker branch has multiple commits that each conflict on state
    # files, the rebase needs multiple rounds of resolution. Each round
    # resolves state file conflicts and continues until the rebase completes
    # or a non-state-file conflict is encountered.
    #
    print(f"Rebasing onto {primary_branch}...")

    had_conflicts = False
    env_no_editor = os.environ.copy()
    env_no_editor["GIT_EDITOR"] = "true"  # prevent interactive editor

    for rebase_round in range(MAX_REBASE_ROUNDS):
        if rebase_round == 0:
            rebase_result = run_quiet(["git", "rebase", primary_branch])
        else:
            rebase_result = run_quiet(
                ["git", "rebase", "--continue"], env=env_no_editor)

        if rebase_result.returncode == 0:
            break  # rebase completed

        had_conflicts = True
        conflicting = _get_conflicting_files()

        if not conflicting:
            # No conflicts listed but rebase failed — unexpected
            die("Rebase failed with no identifiable conflicts. "
                "Inspect with `git status` and resolve manually.")

        state_conflicts = [f for f in conflicting if _is_state_file(f)]
        non_state_conflicts = [f for f in conflicting if not _is_state_file(f)]

        # Handle report files for "ours" strategy
        if args.conflict_strategy == "ours" and non_state_conflicts:
            report_conflicts = [f for f in non_state_conflicts
                                if f.startswith(REPORT_FILES)]
            if report_conflicts:
                _resolve_report_conflicts()
                non_state_conflicts = [f for f in non_state_conflicts
                                       if not f.startswith(REPORT_FILES)]

        if non_state_conflicts:
            # Resolve state files first so the agent only has source conflicts
            if state_conflicts:
                print(f"Rebase conflict (round {rebase_round + 1}) — "
                      f"auto-resolving {len(state_conflicts)} state file(s)...")
                _resolve_state_conflicts(args.conflict_strategy, state_conflicts)

            # Non-state file conflicts remain — bail for agent resolution
            print("ERROR: Rebase has unresolved non-state file conflicts.",
                  file=sys.stderr)
            print("", file=sys.stderr)
            print("Conflicting files:", file=sys.stderr)
            for f in non_state_conflicts:
                print(f"  {f}", file=sys.stderr)
            print("", file=sys.stderr)
            print("The merge lock is STILL HELD. Resolve the conflicts, then:",
                  file=sys.stderr)
            print("  1. git add <resolved files>", file=sys.stderr)
            print("  2. git rebase --continue", file=sys.stderr)
            print("  3. Re-run kf-merge (it will skip lock acquire since you "
                  "hold it)", file=sys.stderr)
            print("", file=sys.stderr)
            print("Or abort: git rebase --abort && kf-merge-lock release",
                  file=sys.stderr)
            # Prevent atexit from releasing the lock
            lock_acquired = False
            sys.exit(3)

        # All conflicts are state files — auto-resolve
        print(f"Rebase conflict (round {rebase_round + 1}) — "
              f"auto-resolving {len(state_conflicts)} state file(s)...")
        _resolve_state_conflicts(args.conflict_strategy, state_conflicts)

    else:
        die(f"Rebase loop exceeded {MAX_REBASE_ROUNDS} rounds — aborting. "
            f"Run `git rebase --abort && kf-merge-lock release` to recover.")

    # Re-apply track state changes after conflict resolution (developer flow)
    if had_conflicts and args.reapply_cmd:
        print("Re-applying track state changes...")
        r = run(args.reapply_cmd, shell=True, check=False)
        if r.returncode != 0:
            die(f"Reapply command failed: {args.reapply_cmd}")
        _stage_state_files()
        run_quiet(["git", "commit", "--amend", "--no-edit"],
                  env=env_no_editor)

    print("Rebase complete.")

    # ── Step 6: Registry update (metadata merge — post-rebase, conflict-free)
    if args.registry_cmd:
        print("Updating registry against rebased state...")
        r = run(args.registry_cmd, shell=True, check=False)
        if r.returncode != 0:
            die(f"Registry command failed: {args.registry_cmd}")
        _stage_state_files()
        run_quiet(["git", "commit", "-m", "chore(kf): update track registry"])
        print("Registry updated.")

    # ── Step 7: Post-rebase verification (implementation merge) ──────────────
    if args.verify_cmd:
        print(f"Running verification: {args.verify_cmd}")
        r = run(args.verify_cmd, shell=True, check=False)
        if r.returncode != 0:
            die("Verification failed. Fix issues and retry.")
        print("Verification passed.")

    # ── Step 8: Fast-forward merge ───────────────────────────────────────────
    print(f"Merging into {primary_branch} (fast-forward only)...")
    ff_result = run(
        ["git", "-C", main_worktree, "merge", current_branch, "--ff-only"],
        check=False,
    )
    if ff_result.returncode != 0:
        die("Fast-forward merge failed — primary branch has diverged.")

    print("MERGE SUCCEEDED.")

    # ── Step 9: Cleanup ──────────────────────────────────────────────────────
    # Lock + heartbeat released by atexit handler

    if args.cleanup_branch:
        run_quiet(["git", "branch", "-d", args.cleanup_branch])

    print("Done.")


if __name__ == "__main__":
    main()
