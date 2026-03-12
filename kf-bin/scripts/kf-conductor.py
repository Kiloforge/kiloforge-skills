#!/usr/bin/env python3
"""kf-conductor — Tmux-based multi-agent orchestration for Kiloforge.

Spawns independent Claude Code worker agents in tmux windows, each operating
in its own git worktree. Workers execute kf tracks autonomously and self-terminate.

USAGE:
    kf-conductor spawn <worker> <track-id> [--timeout MINUTES]
    kf-conductor dispatch [--timeout MINUTES] [--limit N]
    kf-conductor status [--json]
    kf-conductor kill <worker>
    kf-conductor cleanup [--all | --completed | --failed]

SUBCOMMANDS:
    spawn       Spawn a single worker in a tmux window
    dispatch    Run kf-dispatch and spawn workers for all assignments
    status      Show status of all conductor-managed workers
    kill        Kill a running worker
    cleanup     Clean up finished workers (reset worktrees, remove status files)

PREREQUISITES:
    - Must be running inside a tmux session
    - claude CLI must be available on PATH
    - Git worktrees must exist (worker-N or developer-N)

EXIT CODES:
    0  Success
    1  Error
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path so lib/ is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import git
from lib.config import Config

BIN_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def conductor_dir() -> Path:
    """Return the kf-conductor directory under the git common dir."""
    common = git.git_common_dir()
    if not common:
        print("ERROR: not in a git repository", file=sys.stderr)
        sys.exit(1)
    d = Path(common) / "kf-conductor"
    d.mkdir(parents=True, exist_ok=True)
    return d


def status_file(worker: str) -> Path:
    return conductor_dir() / f"{worker}.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_tmux():
    """Verify we are inside a tmux session."""
    if not os.environ.get("TMUX"):
        print("ERROR: Not inside a tmux session.", file=sys.stderr)
        print("Start tmux first: tmux new -s kf", file=sys.stderr)
        sys.exit(1)


def tmux_session() -> str:
    """Get the current tmux session name."""
    result = subprocess.run(
        ["tmux", "display-message", "-p", "#S"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "0"


def tmux_window_exists(name: str) -> bool:
    """Check if a tmux window with this name exists."""
    result = subprocess.run(
        ["tmux", "list-windows", "-F", "#{window_name}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False
    return name in result.stdout.strip().splitlines()


def tmux_pane_pid(window_name: str) -> int | None:
    """Get the PID of the process running in a tmux window's pane."""
    result = subprocess.run(
        ["tmux", "list-panes", "-t", window_name, "-F", "#{pane_pid}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def find_timeout_cmd() -> str:
    """Find the timeout command (GNU coreutils). On macOS it may be gtimeout."""
    for cmd in ["timeout", "gtimeout"]:
        if shutil.which(cmd):
            return cmd
    return ""


def worktree_path_for(worker: str) -> str | None:
    """Find the worktree path for a given worker name."""
    for wt in git.worktree_list():
        folder = os.path.basename(wt["path"])
        if folder == worker:
            return wt["path"]
    return None


def get_max_workers() -> int:
    """Read max_workers from config.yaml, defaulting to 4."""
    # Try project config first
    toplevel = git.toplevel()
    if toplevel:
        cfg_path = Path(toplevel) / ".agent" / "kf" / "config.yaml"
        if cfg_path.exists():
            cfg = Config(cfg_path)
            try:
                return int(cfg.get("max_workers"))
            except (ValueError, KeyError):
                pass
    return 4


def count_running_workers() -> int:
    """Count currently running conductor-managed workers."""
    cdir = conductor_dir()
    count = 0
    for sf in cdir.glob("*.json"):
        data = refresh_status(sf.stem)
        if data and data.get("state") == "running":
            count += 1
    return count


def read_status(worker: str) -> dict | None:
    """Read a worker's status file."""
    sf = status_file(worker)
    if not sf.exists():
        return None
    try:
        return json.loads(sf.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_status(worker: str, data: dict):
    """Write a worker's status file."""
    sf = status_file(worker)
    sf.write_text(json.dumps(data, indent=2) + "\n")


def refresh_status(worker: str) -> dict | None:
    """Read status and update state based on liveness."""
    data = read_status(worker)
    if not data or data.get("state") != "running":
        return data

    # Check if the tmux window still exists
    if not tmux_window_exists(data.get("tmux_window", worker)):
        # Window gone — check exit code from status file (set by wrapper)
        # If still "running", the wrapper didn't get to update it
        if data["state"] == "running":
            data["state"] = "completed"  # Assume success if window closed cleanly
            data["finished"] = now_iso()
            write_status(worker, data)
        return data

    # Window exists — check pane PID
    pane_pid = data.get("pane_pid")
    if pane_pid and not pid_alive(pane_pid):
        data["state"] = "completed"
        data["finished"] = now_iso()
        write_status(worker, data)

    return data


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_spawn(args):
    check_tmux()

    worker = args.worker
    track_id = args.track_id
    timeout_min = args.timeout

    # Check max_workers limit
    max_w = args.max_workers if hasattr(args, "max_workers") and args.max_workers else get_max_workers()
    running = count_running_workers()
    if running >= max_w:
        print(f"ERROR: Already at max workers ({running}/{max_w}). "
              f"Wait for workers to finish or increase max_workers in config.yaml.",
              file=sys.stderr)
        return 1

    # Validate worktree exists
    wt_path = worktree_path_for(worker)
    if not wt_path:
        print(f"ERROR: Worktree '{worker}' not found.", file=sys.stderr)
        print("Available worktrees:", file=sys.stderr)
        for wt in git.worktree_list():
            folder = os.path.basename(wt["path"])
            if folder.startswith("worker-") or folder.startswith("developer-"):
                print(f"  {folder}  ({wt['path']})", file=sys.stderr)
        return 1

    # Check if worker already has a running task
    existing = read_status(worker)
    if existing and existing.get("state") == "running":
        if tmux_window_exists(worker):
            print(f"ERROR: Worker '{worker}' is already running (track: {existing.get('track_id')})", file=sys.stderr)
            return 1

    # Check if track is already claimed by another worktree
    claim_check = subprocess.run(
        [os.path.join(BIN_DIR, "kf-claim.py"), "find", track_id],
        capture_output=True, text=True,
    )
    if claim_check.returncode == 0 and "not claimed" not in claim_check.stdout:
        print(f"ERROR: Track '{track_id}' is already claimed.", file=sys.stderr)
        print(claim_check.stdout.strip(), file=sys.stderr)
        return 1

    # Build the claude prompt
    prompt = f"/kf-developer {track_id}"

    # Build the command to run in the tmux window
    timeout_cmd = find_timeout_cmd()
    timeout_sec = timeout_min * 60 if timeout_min else 0

    # The claude command
    claude_cmd = f'claude -p "{prompt}" --dangerously-skip-permissions'

    if timeout_sec and timeout_cmd:
        inner_cmd = f'{timeout_cmd} --kill-after=10 {timeout_sec} {claude_cmd}'
    else:
        inner_cmd = claude_cmd

    # Wrapper: run claude, capture exit code, update status file
    sf = str(status_file(worker))
    wrapper = (
        f'cd {wt_path} && '
        f'{inner_cmd}; '
        f'EC=$?; '
        f'python3 -c "'
        f"import json,sys,datetime;"
        f"f='{sf}';"
        f"d=json.load(open(f));"
        f"d['exit_code']=int(sys.argv[1]);"
        f"d['state']='completed' if int(sys.argv[1])==0 else ('timeout' if int(sys.argv[1])==124 else 'failed');"
        f"d['finished']=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ');"
        f"open(f,'w').write(json.dumps(d,indent=2)+'\\n')"
        f'" $EC'
    )

    # Write initial status
    status_data = {
        "worker": worker,
        "track_id": track_id,
        "tmux_session": tmux_session(),
        "tmux_window": worker,
        "pane_pid": None,
        "timeout_seconds": timeout_sec,
        "started": now_iso(),
        "finished": None,
        "exit_code": None,
        "state": "running",
    }
    write_status(worker, status_data)

    # Spawn tmux window
    result = subprocess.run(
        ["tmux", "new-window", "-n", worker, wrapper],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to create tmux window: {result.stderr}", file=sys.stderr)
        status_file(worker).unlink(missing_ok=True)
        return 1

    # Capture pane PID
    pane = tmux_pane_pid(worker)
    if pane:
        status_data["pane_pid"] = pane
        write_status(worker, status_data)

    timeout_str = f" (timeout: {timeout_min}m)" if timeout_min else ""
    print(f"Spawned: {worker} → {track_id}{timeout_str}")
    return 0


def cmd_dispatch(args):
    check_tmux()

    timeout_min = args.timeout
    max_w = args.max_workers if args.max_workers else get_max_workers()

    # Check how many slots are available
    running = count_running_workers()
    available_slots = max_w - running
    if available_slots <= 0:
        print(f"Already at max workers ({running}/{max_w}). Wait for workers to finish.")
        return 0

    # Run kf-dispatch to get assignments
    limit = min(args.limit, available_slots) if args.limit else available_slots
    dispatch_cmd = [os.path.join(BIN_DIR, "kf-dispatch.py"), "--json", "--limit", str(limit)]

    result = subprocess.run(dispatch_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: kf-dispatch failed: {result.stderr}", file=sys.stderr)
        return 1

    try:
        plan = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("ERROR: Could not parse dispatch output", file=sys.stderr)
        return 1

    assignments = plan.get("assignments", [])
    if not assignments:
        print("No assignments — all workers busy or no available tracks.")
        if plan.get("blocked_tracks"):
            print(f"  {len(plan['blocked_tracks'])} track(s) blocked on dependencies")
        return 0

    print(f"Dispatching {len(assignments)} worker(s) (max: {max_w}, running: {running})...")
    print()

    success = 0
    for a in assignments:
        worker = a["worker"]
        track_id = a["track_id"]

        # Re-check limit before each spawn (a previous spawn counts)
        if count_running_workers() >= max_w:
            print(f"  Reached max workers ({max_w}), stopping dispatch")
            break

        # Build spawn args
        spawn_args = argparse.Namespace(
            worker=worker,
            track_id=track_id,
            timeout=timeout_min,
            max_workers=max_w,
        )
        rc = cmd_spawn(spawn_args)
        if rc == 0:
            success += 1
        else:
            print(f"  FAILED to spawn {worker} for {track_id}")

        # Small delay between spawns to avoid tmux race conditions
        time.sleep(0.5)

    print()
    print(f"Dispatched {success}/{len(assignments)} worker(s)")
    return 0 if success > 0 else 1


def cmd_status(args):
    cdir = conductor_dir()
    workers = []

    for sf in sorted(cdir.glob("*.json")):
        worker = sf.stem
        data = refresh_status(worker)
        if data:
            workers.append(data)

    if not workers:
        print("No conductor-managed workers.")
        return 0

    if args.json:
        print(json.dumps(workers, indent=2))
        return 0

    # Table output
    fmt = "%-18s %-45s %-12s %s"
    print(fmt % ("WORKER", "TRACK", "STATE", "ELAPSED"))
    print(fmt % ("------", "-----", "-----", "-------"))

    for w in workers:
        state = w.get("state", "?")
        started = w.get("started", "")
        finished = w.get("finished")

        # Compute elapsed time
        elapsed = ""
        if started:
            try:
                st = datetime.fromisoformat(started.replace("Z", "+00:00"))
                if finished:
                    et = datetime.fromisoformat(finished.replace("Z", "+00:00"))
                else:
                    et = datetime.now(timezone.utc)
                delta = et - st
                minutes = int(delta.total_seconds() // 60)
                seconds = int(delta.total_seconds() % 60)
                elapsed = f"{minutes}m{seconds:02d}s"
            except (ValueError, TypeError):
                pass

        # State with indicator
        state_display = {
            "running": "● running",
            "completed": "✓ completed",
            "failed": "✗ failed",
            "timeout": "⏱ timeout",
            "killed": "⊘ killed",
        }.get(state, state)

        print(fmt % (
            w.get("worker", "?"),
            w.get("track_id", "?"),
            state_display,
            elapsed,
        ))

    # Summary
    running = sum(1 for w in workers if w.get("state") == "running")
    done = sum(1 for w in workers if w.get("state") in ("completed", "failed", "timeout", "killed"))
    max_w = get_max_workers()
    print()
    print(f"{running} running, {done} finished (max_workers: {max_w})")
    return 0


def cmd_kill(args):
    worker = args.worker
    data = read_status(worker)

    if not data:
        print(f"No status file for worker '{worker}'", file=sys.stderr)
        return 1

    if data.get("state") != "running":
        print(f"Worker '{worker}' is not running (state: {data.get('state')})")
        return 0

    # Kill the tmux window
    if tmux_window_exists(worker):
        subprocess.run(["tmux", "kill-window", "-t", worker], capture_output=True)

    # Update status
    data["state"] = "killed"
    data["finished"] = now_iso()
    write_status(worker, data)

    # Release claim
    subprocess.run(
        [os.path.join(BIN_DIR, "kf-claim.py"), "release", "--worktree", worker],
        capture_output=True, text=True,
    )

    print(f"Killed: {worker} (track: {data.get('track_id', '?')})")
    return 0


def cmd_cleanup(args):
    cdir = conductor_dir()
    cleaned = 0

    for sf in sorted(cdir.glob("*.json")):
        worker = sf.stem
        data = refresh_status(worker)
        if not data:
            continue

        state = data.get("state", "")

        # Skip running workers unless --all
        if state == "running" and not args.all:
            continue

        # Filter by state
        if args.completed and state != "completed":
            continue
        if args.failed and state not in ("failed", "timeout"):
            continue

        # Kill if still running (--all mode)
        if state == "running" and tmux_window_exists(worker):
            subprocess.run(["tmux", "kill-window", "-t", worker], capture_output=True)

        # Release claim if held
        subprocess.run(
            [os.path.join(BIN_DIR, "kf-claim.py"), "release", "--worktree", worker],
            capture_output=True, text=True,
        )

        # Reset worktree to home branch
        wt_path = worktree_path_for(worker)
        if wt_path:
            subprocess.run(
                ["git", "-C", wt_path, "checkout", worker],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", wt_path, "clean", "-fd"],
                capture_output=True, text=True,
            )

        # Remove status file
        sf.unlink(missing_ok=True)
        print(f"Cleaned: {worker} (was: {state}, track: {data.get('track_id', '?')})")
        cleaned += 1

    if cleaned:
        print(f"\nCleaned {cleaned} worker(s)")
    else:
        print("Nothing to clean")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Tmux-based multi-agent orchestration for Kiloforge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # spawn
    p_spawn = sub.add_parser("spawn", help="Spawn a worker in a tmux window")
    p_spawn.add_argument("worker", help="Worker name (must match a worktree)")
    p_spawn.add_argument("track_id", help="Track ID to implement")
    p_spawn.add_argument("--timeout", type=int, default=30, help="Timeout in minutes (default: 30)")
    p_spawn.add_argument("--max-workers", type=int, default=0, help="Override max_workers from config (0=use config)")

    # dispatch
    p_dispatch = sub.add_parser("dispatch", help="Auto-dispatch workers from kf-dispatch plan")
    p_dispatch.add_argument("--timeout", type=int, default=30, help="Timeout per worker in minutes (default: 30)")
    p_dispatch.add_argument("--max-workers", type=int, default=0, help="Override max_workers from config (0=use config)")
    p_dispatch.add_argument("--limit", type=int, default=0, help="Max assignments from dispatch (0=fill to max_workers)")

    # status
    p_status = sub.add_parser("status", help="Show worker status")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")

    # kill
    p_kill = sub.add_parser("kill", help="Kill a running worker")
    p_kill.add_argument("worker", help="Worker name to kill")

    # cleanup
    p_cleanup = sub.add_parser("cleanup", help="Clean up finished workers")
    p_cleanup.add_argument("--all", action="store_true", help="Clean all (including running — kills them first)")
    p_cleanup.add_argument("--completed", action="store_true", help="Clean only completed workers")
    p_cleanup.add_argument("--failed", action="store_true", help="Clean only failed/timed-out workers")

    # help
    sub.add_parser("help", help="Show help")

    args = parser.parse_args()

    if not args.command or args.command == "help":
        parser.print_help()
        return 0

    handlers = {
        "spawn": cmd_spawn,
        "dispatch": cmd_dispatch,
        "status": cmd_status,
        "kill": cmd_kill,
        "cleanup": cmd_cleanup,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
