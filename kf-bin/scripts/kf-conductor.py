#!/usr/bin/env python3
"""kf-conductor — Tmux-based multi-agent orchestration for Kiloforge.

Spawns independent Claude Code worker agents in tmux windows, each operating
in its own git worktree. Workers execute kf tracks autonomously and self-terminate.

A persistent manager loop chews through the track queue, auto-dispatching
new workers as others complete, respecting max_workers concurrency limits.

USAGE:
    kf-conductor start [--timeout MINUTES]     Start the manager loop
    kf-conductor stop                          Graceful shutdown (finish current, no new)
    kf-conductor suspend                       Pause dispatching (workers keep running)
    kf-conductor resume                        Resume dispatching
    kf-conductor status [--json]               Show manager + worker status
    kf-conductor spawn <worker> <track-id>     Manually spawn a single worker
    kf-conductor kill <worker>                 Kill a running worker
    kf-conductor cleanup [--all|--completed]   Clean up finished workers

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

# Manager states
STATE_RUNNING = "running"
STATE_SUSPENDED = "suspended"
STATE_STOPPING = "stopping"
STATE_STOPPED = "stopped"

# Default poll interval for the manager loop
POLL_INTERVAL = 5  # seconds


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


def manager_file() -> Path:
    return conductor_dir() / "_manager.json"


def worker_status_file(worker: str) -> Path:
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
    result = subprocess.run(
        ["tmux", "display-message", "-p", "#S"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "0"


def tmux_window_exists(name: str) -> bool:
    result = subprocess.run(
        ["tmux", "list-windows", "-F", "#{window_name}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False
    return name in result.stdout.strip().splitlines()


def tmux_pane_pid(window_name: str) -> int | None:
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
    for cmd in ["timeout", "gtimeout"]:
        if shutil.which(cmd):
            return cmd
    return ""


def worktree_path_for(worker: str) -> str | None:
    for wt in git.worktree_list():
        if os.path.basename(wt["path"]) == worker:
            return wt["path"]
    return None


def get_max_workers() -> int:
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


# ---------------------------------------------------------------------------
# Manager state
# ---------------------------------------------------------------------------

def read_manager() -> dict | None:
    mf = manager_file()
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_manager(data: dict):
    manager_file().write_text(json.dumps(data, indent=2) + "\n")


def manager_is_alive() -> bool:
    """Check if the manager process is still running."""
    data = read_manager()
    if not data:
        return False
    pid = data.get("pid")
    if not pid:
        return False
    return pid_alive(pid)


def signal_manager(new_state: str) -> bool:
    """Signal the manager to change state. Returns True if manager is alive."""
    data = read_manager()
    if not data:
        print("Manager is not running.", file=sys.stderr)
        return False
    if not pid_alive(data.get("pid", 0)):
        print("Manager process is dead. Cleaning up.", file=sys.stderr)
        data["state"] = STATE_STOPPED
        write_manager(data)
        return False
    data["state"] = new_state
    write_manager(data)
    return True


# ---------------------------------------------------------------------------
# Worker status
# ---------------------------------------------------------------------------

def read_worker_status(worker: str) -> dict | None:
    sf = worker_status_file(worker)
    if not sf.exists():
        return None
    try:
        return json.loads(sf.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_worker_status(worker: str, data: dict):
    worker_status_file(worker).write_text(json.dumps(data, indent=2) + "\n")


def refresh_worker(worker: str) -> dict | None:
    """Read status and update state based on liveness."""
    data = read_worker_status(worker)
    if not data or data.get("state") != "running":
        return data

    if not tmux_window_exists(data.get("tmux_window", worker)):
        if data["state"] == "running":
            data["state"] = "completed"
            data["finished"] = now_iso()
            write_worker_status(worker, data)
        return data

    pane = data.get("pane_pid")
    if pane and not pid_alive(pane):
        data["state"] = "completed"
        data["finished"] = now_iso()
        write_worker_status(worker, data)

    return data


def count_running_workers() -> int:
    count = 0
    for sf in conductor_dir().glob("*.json"):
        if sf.name.startswith("_"):
            continue  # skip _manager.json
        data = refresh_worker(sf.stem)
        if data and data.get("state") == "running":
            count += 1
    return count


def all_worker_statuses() -> list[dict]:
    workers = []
    for sf in sorted(conductor_dir().glob("*.json")):
        if sf.name.startswith("_"):
            continue
        data = refresh_worker(sf.stem)
        if data:
            workers.append(data)
    return workers


# ---------------------------------------------------------------------------
# Spawn logic
# ---------------------------------------------------------------------------

def spawn_worker(worker: str, track_id: str, timeout_min: int) -> int:
    """Spawn a single worker. Returns 0 on success, 1 on failure."""
    wt_path = worktree_path_for(worker)
    if not wt_path:
        print(f"  ERROR: Worktree '{worker}' not found.", file=sys.stderr)
        return 1

    # Check if worker already running
    existing = read_worker_status(worker)
    if existing and existing.get("state") == "running":
        if tmux_window_exists(worker):
            print(f"  ERROR: Worker '{worker}' already running.", file=sys.stderr)
            return 1

    # Check if track already claimed
    claim_check = subprocess.run(
        [os.path.join(BIN_DIR, "kf-claim.py"), "find", track_id],
        capture_output=True, text=True,
    )
    if claim_check.returncode == 0 and "not claimed" not in claim_check.stdout:
        print(f"  ERROR: Track '{track_id}' already claimed.", file=sys.stderr)
        return 1

    # Build command
    prompt = f"/kf-developer {track_id}"
    timeout_cmd = find_timeout_cmd()
    timeout_sec = timeout_min * 60 if timeout_min else 0

    claude_cmd = f'claude -p "{prompt}" --dangerously-skip-permissions'
    if timeout_sec and timeout_cmd:
        inner_cmd = f'{timeout_cmd} --kill-after=10 {timeout_sec} {claude_cmd}'
    else:
        inner_cmd = claude_cmd

    sf = str(worker_status_file(worker))
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
    write_worker_status(worker, status_data)

    result = subprocess.run(
        ["tmux", "new-window", "-n", worker, wrapper],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: tmux window failed: {result.stderr}", file=sys.stderr)
        worker_status_file(worker).unlink(missing_ok=True)
        return 1

    pane = tmux_pane_pid(worker)
    if pane:
        status_data["pane_pid"] = pane
        write_worker_status(worker, status_data)

    return 0


def run_dispatch(max_w: int, timeout_min: int) -> int:
    """Run one dispatch cycle. Returns number of workers spawned."""
    running = count_running_workers()
    available_slots = max_w - running
    if available_slots <= 0:
        return 0

    dispatch_cmd = [
        os.path.join(BIN_DIR, "kf-dispatch.py"),
        "--json", "--limit", str(available_slots),
    ]
    result = subprocess.run(dispatch_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0

    try:
        plan = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 0

    spawned = 0
    for a in plan.get("assignments", []):
        if count_running_workers() >= max_w:
            break
        rc = spawn_worker(a["worker"], a["track_id"], timeout_min)
        if rc == 0:
            timeout_str = f" (timeout: {timeout_min}m)" if timeout_min else ""
            print(f"  Spawned: {a['worker']} → {a['track_id']}{timeout_str}")
            spawned += 1
        time.sleep(0.5)

    return spawned


def auto_cleanup_completed():
    """Silently clean up completed workers so they can be re-used."""
    for sf in sorted(conductor_dir().glob("*.json")):
        if sf.name.startswith("_"):
            continue
        worker = sf.stem
        data = refresh_worker(worker)
        if not data:
            continue
        if data.get("state") not in ("completed", "failed", "timeout", "killed"):
            continue

        # Release claim
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

        sf.unlink(missing_ok=True)
        state = data.get("state", "?")
        track = data.get("track_id", "?")
        print(f"  Cleaned: {worker} ({state}, track: {track})")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_start(args):
    """Start the manager loop — runs in the foreground."""
    check_tmux()

    # Check if already running
    if manager_is_alive():
        mgr = read_manager()
        print(f"Manager already running (pid: {mgr.get('pid')}, state: {mgr.get('state')})")
        return 1

    timeout_min = args.timeout
    max_w = args.max_workers if args.max_workers else get_max_workers()

    mgr_data = {
        "pid": os.getpid(),
        "state": STATE_RUNNING,
        "started": now_iso(),
        "timeout_minutes": timeout_min,
        "max_workers": max_w,
    }
    write_manager(mgr_data)

    print(f"Conductor manager started (pid: {os.getpid()}, max_workers: {max_w}, timeout: {timeout_min}m)")
    print(f"Use 'kf-conductor.py suspend/resume/stop' from another window to control.")
    print()

    try:
        _manager_loop(max_w, timeout_min)
    except KeyboardInterrupt:
        print("\nInterrupted — shutting down.")
    finally:
        mgr_data = read_manager() or mgr_data
        mgr_data["state"] = STATE_STOPPED
        mgr_data["stopped"] = now_iso()
        write_manager(mgr_data)
        print("Manager stopped.")

    return 0


def _manager_loop(max_w: int, timeout_min: int):
    """The main manager loop. Polls for work and dispatches."""
    cycle = 0
    while True:
        # Read current manager state (may have been changed by suspend/resume/stop)
        mgr = read_manager()
        if not mgr:
            break
        state = mgr.get("state", STATE_RUNNING)

        if state == STATE_STOPPING:
            running = count_running_workers()
            if running == 0:
                print("All workers finished. Stopping.")
                break
            if cycle % 12 == 0:  # Log every ~60s
                print(f"  Stopping... waiting for {running} worker(s) to finish")

        elif state == STATE_SUSPENDED:
            if cycle % 12 == 0:
                running = count_running_workers()
                print(f"  Suspended — {running} worker(s) still running")

        elif state == STATE_RUNNING:
            # Clean up finished workers
            auto_cleanup_completed()

            # Dispatch new work
            spawned = run_dispatch(max_w, timeout_min)

            # Log periodically
            if spawned > 0 or cycle % 12 == 0:
                running = count_running_workers()
                print(f"  [{now_iso()}] running: {running}/{max_w}")

        else:
            break  # Unknown state

        cycle += 1
        time.sleep(POLL_INTERVAL)


def cmd_stop(args):
    if signal_manager(STATE_STOPPING):
        print("Manager signaled to stop (will finish current workers, then exit).")
    return 0


def cmd_suspend(args):
    if signal_manager(STATE_SUSPENDED):
        print("Manager suspended — running workers will continue, no new dispatches.")
    return 0


def cmd_resume(args):
    if signal_manager(STATE_RUNNING):
        print("Manager resumed — dispatching new workers.")
    return 0


def cmd_spawn(args):
    check_tmux()

    max_w = args.max_workers if hasattr(args, "max_workers") and args.max_workers else get_max_workers()
    running = count_running_workers()
    if running >= max_w:
        print(f"ERROR: At max workers ({running}/{max_w}).", file=sys.stderr)
        return 1

    rc = spawn_worker(args.worker, args.track_id, args.timeout)
    if rc == 0:
        timeout_str = f" (timeout: {args.timeout}m)" if args.timeout else ""
        print(f"Spawned: {args.worker} → {args.track_id}{timeout_str}")
    return rc


def cmd_dispatch(args):
    """One-shot dispatch (not the manager loop)."""
    check_tmux()

    max_w = args.max_workers if args.max_workers else get_max_workers()
    running = count_running_workers()
    available = max_w - running

    if available <= 0:
        print(f"At max workers ({running}/{max_w}).")
        return 0

    print(f"Dispatching (max: {max_w}, running: {running}, slots: {available})...")
    spawned = run_dispatch(max_w, args.timeout)
    print(f"Dispatched {spawned} worker(s)")
    return 0 if spawned > 0 else 0


def cmd_status(args):
    workers = all_worker_statuses()
    mgr = read_manager()

    # Manager status
    if mgr:
        mgr_state = mgr.get("state", "?")
        mgr_alive = pid_alive(mgr.get("pid", 0))
        if not mgr_alive and mgr_state not in (STATE_STOPPED,):
            mgr_state = "dead"

        if args.json:
            pass  # included in JSON below
        else:
            state_display = {
                STATE_RUNNING: "● running",
                STATE_SUSPENDED: "⏸ suspended",
                STATE_STOPPING: "◼ stopping",
                STATE_STOPPED: "○ stopped",
                "dead": "✗ dead",
            }.get(mgr_state, mgr_state)
            print(f"Manager: {state_display} (pid: {mgr.get('pid', '?')})")
            print()

    if args.json:
        out = {
            "manager": mgr,
            "workers": workers,
            "max_workers": get_max_workers(),
        }
        print(json.dumps(out, indent=2))
        return 0

    if not workers:
        print("No workers.")
        return 0

    fmt = "%-18s %-40s %-14s %s"
    print(fmt % ("WORKER", "TRACK", "STATE", "ELAPSED"))
    print(fmt % ("------", "-----", "-----", "-------"))

    for w in workers:
        state = w.get("state", "?")
        started = w.get("started", "")
        finished = w.get("finished")

        elapsed = ""
        if started:
            try:
                st = datetime.fromisoformat(started.replace("Z", "+00:00"))
                et = datetime.fromisoformat(finished.replace("Z", "+00:00")) if finished else datetime.now(timezone.utc)
                delta = et - st
                minutes = int(delta.total_seconds() // 60)
                seconds = int(delta.total_seconds() % 60)
                elapsed = f"{minutes}m{seconds:02d}s"
            except (ValueError, TypeError):
                pass

        state_display = {
            "running": "● running",
            "completed": "✓ completed",
            "failed": "✗ failed",
            "timeout": "⏱ timeout",
            "killed": "⊘ killed",
        }.get(state, state)

        print(fmt % (w.get("worker", "?"), w.get("track_id", "?"), state_display, elapsed))

    running = sum(1 for w in workers if w.get("state") == "running")
    done = sum(1 for w in workers if w.get("state") != "running")
    max_w = get_max_workers()
    print()
    print(f"{running} running, {done} finished (max_workers: {max_w})")
    return 0


def cmd_kill(args):
    data = read_worker_status(args.worker)
    if not data:
        print(f"No status for worker '{args.worker}'", file=sys.stderr)
        return 1

    if data.get("state") != "running":
        print(f"Worker '{args.worker}' is not running (state: {data.get('state')})")
        return 0

    if tmux_window_exists(args.worker):
        subprocess.run(["tmux", "kill-window", "-t", args.worker], capture_output=True)

    data["state"] = "killed"
    data["finished"] = now_iso()
    write_worker_status(args.worker, data)

    subprocess.run(
        [os.path.join(BIN_DIR, "kf-claim.py"), "release", "--worktree", args.worker],
        capture_output=True, text=True,
    )

    print(f"Killed: {args.worker} (track: {data.get('track_id', '?')})")
    return 0


def cmd_cleanup(args):
    cleaned = 0
    for sf in sorted(conductor_dir().glob("*.json")):
        if sf.name.startswith("_"):
            continue
        worker = sf.stem
        data = refresh_worker(worker)
        if not data:
            continue

        state = data.get("state", "")
        if state == "running" and not args.all:
            continue
        if args.completed and state != "completed":
            continue
        if args.failed and state not in ("failed", "timeout"):
            continue

        if state == "running" and tmux_window_exists(worker):
            subprocess.run(["tmux", "kill-window", "-t", worker], capture_output=True)

        subprocess.run(
            [os.path.join(BIN_DIR, "kf-claim.py"), "release", "--worktree", worker],
            capture_output=True, text=True,
        )

        wt_path = worktree_path_for(worker)
        if wt_path:
            subprocess.run(["git", "-C", wt_path, "checkout", worker], capture_output=True, text=True)
            subprocess.run(["git", "-C", wt_path, "clean", "-fd"], capture_output=True, text=True)

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

    # start (manager loop)
    p_start = sub.add_parser("start", help="Start the manager loop (runs in foreground)")
    p_start.add_argument("--timeout", type=int, default=30, help="Timeout per worker in minutes (default: 30)")
    p_start.add_argument("--max-workers", type=int, default=0, help="Override max_workers from config")

    # stop / suspend / resume
    sub.add_parser("stop", help="Signal manager to stop after current workers finish")
    sub.add_parser("suspend", help="Pause dispatching (running workers continue)")
    sub.add_parser("resume", help="Resume dispatching")

    # status
    p_status = sub.add_parser("status", help="Show manager + worker status")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")

    # spawn (manual, one-shot)
    p_spawn = sub.add_parser("spawn", help="Manually spawn a single worker")
    p_spawn.add_argument("worker", help="Worker name (must match a worktree)")
    p_spawn.add_argument("track_id", help="Track ID to implement")
    p_spawn.add_argument("--timeout", type=int, default=30, help="Timeout in minutes (default: 30)")
    p_spawn.add_argument("--max-workers", type=int, default=0, help="Override max_workers")

    # dispatch (one-shot, no loop)
    p_dispatch = sub.add_parser("dispatch", help="One-shot dispatch (no loop)")
    p_dispatch.add_argument("--timeout", type=int, default=30, help="Timeout per worker (default: 30)")
    p_dispatch.add_argument("--max-workers", type=int, default=0, help="Override max_workers")

    # kill
    p_kill = sub.add_parser("kill", help="Kill a running worker")
    p_kill.add_argument("worker", help="Worker name to kill")

    # cleanup
    p_cleanup = sub.add_parser("cleanup", help="Clean up finished workers")
    p_cleanup.add_argument("--all", action="store_true", help="Clean all (kills running workers)")
    p_cleanup.add_argument("--completed", action="store_true", help="Clean only completed")
    p_cleanup.add_argument("--failed", action="store_true", help="Clean only failed/timed-out")

    sub.add_parser("help", help="Show help")

    args = parser.parse_args()

    if not args.command or args.command == "help":
        parser.print_help()
        return 0

    handlers = {
        "start": cmd_start,
        "stop": cmd_stop,
        "suspend": cmd_suspend,
        "resume": cmd_resume,
        "status": cmd_status,
        "spawn": cmd_spawn,
        "dispatch": cmd_dispatch,
        "kill": cmd_kill,
        "cleanup": cmd_cleanup,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
