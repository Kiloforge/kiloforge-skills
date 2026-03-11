#!/usr/bin/env python3
# kf-merge-lock — Cross-worktree merge lock helper
#
# Encapsulates the dual-mode (HTTP/mkdir) merge lock protocol.
# Used by kf-developer and kf-architect skills to coordinate merges.
#
# USAGE:
#   kf-merge-lock acquire [--holder NAME] [--timeout SECONDS] [--ttl SECONDS] [--pid PID]
#   kf-merge-lock release [--holder NAME]
#   kf-merge-lock heartbeat [--holder NAME] [--ttl SECONDS]
#   kf-merge-lock status
#   kf-merge-lock help
#
# ENVIRONMENT:
#   KF_ORCH_URL     Orchestrator URL (default: http://localhost:39517)
#   KF_LOCK_HOLDER  Default holder name (default: basename of $PWD)
#
# MERGE PROTOCOL — REBASE CONFLICT RESOLUTION
#
#   During `git rebase`, --ours and --theirs have REVERSED semantics:
#     --ours  = the branch being rebased ONTO (e.g., main — the latest state)
#     --theirs = the commit being REPLAYED (the worker's old commit — stale)
#
#   To accept main's version of track state files during rebase conflicts:
#     git checkout --ours .agent/kf/tracks.yaml .agent/kf/tracks/deps.yaml
#     git checkout --ours .agent/kf/tracks/conflicts.yaml
#
#   WRONG: git checkout --theirs (accepts the stale worker commit, reverts
#   other workers' completions — this caused track state regressions).
#
# MAIN WORKTREE CLEANUP
#
#   Before running `git -C <main-worktree> merge --ff-only`, ensure the main
#   worktree is clean. Previous failed merges or stash pops can leave dirty
#   state that blocks ff-merge. Use:
#     git -C <main-worktree> reset --hard HEAD
#   before the merge attempt if the worktree might be dirty.

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ORCH_URL = os.environ.get("KF_ORCH_URL", "http://localhost:39517")
DEFAULT_HOLDER = os.environ.get("KF_LOCK_HOLDER", os.path.basename(os.getcwd()))
DEFAULT_TTL = 120
DEFAULT_TIMEOUT = 0
STALE_THRESHOLD = 240  # 2x default TTL — auto-clean mkdir locks older than this


# --- Mode detection ---

def is_orch_running() -> bool:
    """Check if the orchestrator is reachable via health endpoint."""
    try:
        req = urllib.request.Request(f"{ORCH_URL}/health", method="GET")
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def get_lock_dir() -> Path:
    """Return the path to the mkdir-mode lock directory."""
    try:
        common_dir = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
    except subprocess.CalledProcessError:
        try:
            common_dir = subprocess.check_output(
                ["git", "rev-parse", "--git-dir"],
                stderr=subprocess.DEVNULL, text=True
            ).strip()
        except subprocess.CalledProcessError:
            common_dir = ".git"
    return Path(common_dir) / "merge.lock"


# --- HTTP mode helpers ---

def _http_json_request(method: str, path: str, data: dict | None = None,
                       timeout: int = 10) -> tuple[int, str]:
    """Make an HTTP request and return (status_code, body)."""
    url = f"{ORCH_URL}{path}"
    body_bytes = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body_bytes, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception:
        return 0, ""


def http_acquire(holder: str, ttl: int, timeout: int) -> int:
    max_time = timeout + 10 if timeout > 0 else 10
    status, body = _http_json_request(
        "POST", "/api/locks/merge/acquire",
        {"holder": holder, "ttl_seconds": ttl, "timeout_seconds": timeout},
        timeout=max_time,
    )
    if status == 0:
        print(f"ERROR: Failed to connect to orchestrator at {ORCH_URL}", file=sys.stderr)
        return 1
    if status == 200:
        print("Merge lock acquired (HTTP mode)")
        return 0
    # Lock held by someone else
    current_holder = "unknown"
    try:
        parsed = json.loads(body)
        current_holder = parsed.get("current_holder", "unknown")
    except Exception:
        pass
    print(f"MERGE LOCK HELD by {current_holder}", file=sys.stderr)
    return 1


def http_release(holder: str) -> int:
    _http_json_request("DELETE", "/api/locks/merge", {"holder": holder})
    print("Lock released (HTTP mode)")
    return 0


def http_heartbeat(holder: str, ttl: int) -> int:
    status, _ = _http_json_request(
        "POST", "/api/locks/merge/heartbeat",
        {"holder": holder, "ttl_seconds": ttl},
    )
    if status == 0 or status >= 400:
        print("WARNING: Heartbeat failed", file=sys.stderr)
        return 1
    return 0


def http_status() -> int:
    status, body = _http_json_request("GET", "/api/locks")
    if status == 0:
        print("Mode:   HTTP (orchestrator unreachable)")
        return 1

    try:
        data = json.loads(body)
    except Exception:
        data = []

    # data could be a list of lock objects or a dict
    locks = data if isinstance(data, list) else [data] if data else []
    count = len(locks)

    print("Mode:   HTTP")
    if count == 0:
        print("Status: No active locks")
        return 0

    print(f"Locks:  {count} active")
    print()
    # Show first merge lock
    for lock in locks:
        if isinstance(lock, dict):
            holder = lock.get("holder", "")
            expires = lock.get("expires_at", "")
            if holder:
                print("  Scope:   merge")
                print(f"  Holder:  {holder}")
                print(f"  Expires: {expires}")
                break
    return 0


# --- mkdir mode helpers ---

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_lock_info(lock_dir: Path, pid: int, holder: str) -> None:
    info_file = lock_dir / "info"
    info_file.write_text(f"{pid} {_utcnow_iso()} {holder}\n")


def _parse_lock_info(lock_dir: Path) -> tuple[str, str, str] | None:
    """Return (pid_str, timestamp, holder) or None."""
    info_file = lock_dir / "info"
    if not info_file.is_file():
        return None
    try:
        text = info_file.read_text().strip()
    except OSError:
        return None
    parts = text.split(None, 2)
    if len(parts) < 3:
        return None
    return parts[0], parts[1], parts[2]


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive via os.kill(pid, 0)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _parse_iso_timestamp(ts: str) -> float | None:
    """Parse an ISO 8601 UTC timestamp to epoch seconds."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _mkdir_check_stale(lock_dir: Path) -> bool:
    """Check if a lock is stale (dead PID + old enough). Returns True if cleaned."""
    info = _parse_lock_info(lock_dir)
    if info is None:
        return False

    pid_str, timestamp, holder = info

    # Validate PID is numeric
    if not pid_str.isdigit():
        return False
    lock_pid = int(pid_str)

    # Check if PID is alive
    if _pid_alive(lock_pid):
        return False  # Process alive — lock is valid

    # PID is dead — check age threshold
    lock_epoch = _parse_iso_timestamp(timestamp)
    if lock_epoch is None:
        return False
    now_epoch = time.time()
    age = int(now_epoch - lock_epoch)

    if age >= STALE_THRESHOLD:
        print(
            f"Auto-cleaning stale lock: holder={holder} pid={lock_pid} age={age}s (dead process)",
            file=sys.stderr,
        )
        shutil.rmtree(lock_dir, ignore_errors=True)
        return True

    # PID dead but lock is recent — don't auto-clean (might be a race)
    print(
        f"WARNING: Lock holder PID {lock_pid} is dead (age={age}s < {STALE_THRESHOLD}s threshold) — not auto-cleaning",
        file=sys.stderr,
    )
    return False


def _try_mkdir(lock_dir: Path) -> bool:
    """Attempt atomic mkdir. Returns True on success."""
    try:
        lock_dir.mkdir(parents=False, exist_ok=False)
        return True
    except FileExistsError:
        return False


def mkdir_acquire(holder: str, timeout: int, pid: int) -> int:
    lock_dir = get_lock_dir()

    if _try_mkdir(lock_dir):
        _write_lock_info(lock_dir, pid, holder)
        print("Merge lock acquired (mkdir mode)")
        return 0

    # Lock exists — check for stale lock
    if _mkdir_check_stale(lock_dir):
        if _try_mkdir(lock_dir):
            _write_lock_info(lock_dir, pid, holder)
            print("Merge lock acquired (mkdir mode, stale lock cleaned)")
            return 0

    # Lock is held — poll if timeout > 0
    if timeout == 0:
        info = _parse_lock_info(lock_dir)
        info_str = f"{info[0]} {info[1]} {info[2]}" if info else "unknown"
        print(f"MERGE LOCK HELD — {info_str}", file=sys.stderr)
        return 1

    # Polling loop
    elapsed = 0
    interval = 10
    while elapsed < timeout:
        print(f"MERGE LOCK HELD — waiting... ({elapsed}s/{timeout}s)", file=sys.stderr)
        time.sleep(interval)
        elapsed += interval

        _mkdir_check_stale(lock_dir)

        if _try_mkdir(lock_dir):
            _write_lock_info(lock_dir, pid, holder)
            print(f"Merge lock acquired (mkdir mode) after {elapsed}s")
            return 0

    print(f"MERGE LOCK TIMEOUT — could not acquire after {timeout}s", file=sys.stderr)
    return 1


def mkdir_release(holder: str) -> int:
    lock_dir = get_lock_dir()

    if not lock_dir.is_dir():
        print("Lock released (mkdir mode, already unlocked)")
        return 0

    # Validate holder before releasing
    info = _parse_lock_info(lock_dir)
    if info is not None:
        lock_holder = info[2]
        if lock_holder and lock_holder != holder:
            print(
                f"ERROR: Cannot release lock — held by '{lock_holder}', not '{holder}'",
                file=sys.stderr,
            )
            return 1

    shutil.rmtree(lock_dir, ignore_errors=True)
    print("Lock released (mkdir mode)")
    return 0


def mkdir_heartbeat(holder: str) -> int:
    lock_dir = get_lock_dir()
    info_file = lock_dir / "info"
    if lock_dir.is_dir() and info_file.is_file():
        info = _parse_lock_info(lock_dir)
        if info is not None:
            original_pid = info[0]
            try:
                info_file.write_text(f"{original_pid} {_utcnow_iso()} {holder}\n")
            except OSError:
                pass
    return 0


def mkdir_status() -> int:
    lock_dir = get_lock_dir()

    print("Mode:   mkdir (fallback)")

    if not lock_dir.is_dir():
        print("Status: No active lock")
        return 0

    info = _parse_lock_info(lock_dir)
    if info is None:
        print("Status: LOCKED")
        print()
        print("  Info: unknown")
        return 0

    pid_str, timestamp, holder = info

    print("Status: LOCKED")
    print()
    print(f"  Holder:    {holder}")
    print(f"  PID:       {pid_str}")
    print(f"  Acquired:  {timestamp}")

    if not pid_str.isdigit():
        print("  PID alive: unknown (not a valid PID)")
    elif _pid_alive(int(pid_str)):
        print("  PID alive: yes")
    else:
        print("  PID alive: NO — process is dead (lock may be stale)")

    return 0


# --- Main command dispatch ---

def cmd_acquire(args: list[str]) -> int:
    holder = DEFAULT_HOLDER
    ttl = DEFAULT_TTL
    timeout = DEFAULT_TIMEOUT
    pid = os.getppid()

    i = 0
    while i < len(args):
        if args[i] == "--holder" and i + 1 < len(args):
            holder = args[i + 1]; i += 2
        elif args[i] == "--ttl" and i + 1 < len(args):
            ttl = int(args[i + 1]); i += 2
        elif args[i] == "--timeout" and i + 1 < len(args):
            timeout = int(args[i + 1]); i += 2
        elif args[i] == "--pid" and i + 1 < len(args):
            pid = int(args[i + 1]); i += 2
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            return 1

    if is_orch_running():
        return http_acquire(holder, ttl, timeout)
    else:
        return mkdir_acquire(holder, timeout, pid)


def cmd_release(args: list[str]) -> int:
    holder = DEFAULT_HOLDER

    i = 0
    while i < len(args):
        if args[i] == "--holder" and i + 1 < len(args):
            holder = args[i + 1]; i += 2
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            return 1

    if is_orch_running():
        return http_release(holder)
    else:
        return mkdir_release(holder)


def cmd_heartbeat(args: list[str]) -> int:
    holder = DEFAULT_HOLDER
    ttl = DEFAULT_TTL

    i = 0
    while i < len(args):
        if args[i] == "--holder" and i + 1 < len(args):
            holder = args[i + 1]; i += 2
        elif args[i] == "--ttl" and i + 1 < len(args):
            ttl = int(args[i + 1]); i += 2
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            return 1

    if is_orch_running():
        return http_heartbeat(holder, ttl)
    else:
        return mkdir_heartbeat(holder)


def cmd_status() -> int:
    print("===============================================")
    print("           MERGE LOCK STATUS")
    print("===============================================")
    print()

    if is_orch_running():
        rc = http_status()
    else:
        rc = mkdir_status()

    print()
    print("===============================================")
    return rc


def cmd_help() -> int:
    print("""\
kf-merge-lock — Cross-worktree merge lock helper

USAGE:
  kf-merge-lock acquire [--holder NAME] [--timeout SECONDS] [--ttl SECONDS] [--pid PID]
  kf-merge-lock release [--holder NAME]
  kf-merge-lock heartbeat [--holder NAME] [--ttl SECONDS]
  kf-merge-lock status
  kf-merge-lock help

OPTIONS:
  --holder NAME     Lock holder identity (default: basename of $PWD)
  --timeout SECONDS Acquire timeout: 0=non-blocking, >0=poll/wait (default: 0)
  --ttl SECONDS     Lock TTL in seconds (default: 120)
  --pid PID         PID to record for stale detection (default: parent PID)

MODES:
  HTTP   — Preferred when orchestrator is running ($KF_ORCH_URL/health responds)
           Uses TTL, heartbeat, server-side long-poll.
  mkdir  — Fallback when orchestrator unavailable.
           Uses PID + timestamp for stale detection. Auto-cleans dead-PID locks
           older than 240s.

ENVIRONMENT:
  KF_ORCH_URL      Orchestrator URL (default: http://localhost:39517)
  KF_LOCK_HOLDER   Default holder name (default: basename of $PWD)

EXAMPLES:
  kf-merge-lock acquire --holder developer-1 --timeout 300
  kf-merge-lock heartbeat --holder developer-1
  kf-merge-lock status
  kf-merge-lock release --holder developer-1""")
    return 0


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "help"
    rest = args[1:] if len(args) > 1 else []

    commands = {
        "acquire": lambda: cmd_acquire(rest),
        "release": lambda: cmd_release(rest),
        "heartbeat": lambda: cmd_heartbeat(rest),
        "status": lambda: cmd_status(),
        "help": lambda: cmd_help(),
        "--help": lambda: cmd_help(),
        "-h": lambda: cmd_help(),
    }

    if cmd in commands:
        return commands[cmd]()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        cmd_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
