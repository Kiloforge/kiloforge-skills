#!/usr/bin/env python3
"""kf-approve-tui — Curses-based TUI for Kiloforge conductor control.

Central control panel for the Kiloforge conductor. Displays tracks in three
sections (Backlog, Approved, In-Progress), shows worker status, and provides
manager controls (start/stop/suspend/resume). Watches for commits on the
primary branch and auto-refreshes.

USAGE:
    kf-approve-tui [--ref BRANCH]

KEYS:
    ↑/↓ or j/k    Navigate tracks
    SPACE          Toggle approval on highlighted track
    ENTER          View full track details
    a              Approve all backlog tracks
    u              Unapprove all approved tracks
    s              Save approval changes
    r              Refresh from primary branch

    F5             Start manager (dispatch loop)
    F6             Suspend manager (pause dispatching)
    F7             Resume manager
    F8             Stop manager (graceful shutdown)

    h / ?          Show help panel with all keybindings
    q              Quit (prompts to save if unsaved changes)
"""

import curses
import json
import os
import subprocess
import sys
import time
import threading
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run(cmd):
    """Run a shell command, return stdout or empty string."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return r.stdout.strip()
    except Exception:
        return ""


def run_script(name, *args):
    """Run a sibling script, return (returncode, stdout, stderr)."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, name)] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def get_primary_branch():
    pb = run(f"{SCRIPT_DIR}/kf-primary-branch.py")
    return pb if pb else "main"


def get_head_commit(ref):
    return run(f"git rev-parse {ref} 2>/dev/null")


def git_common_dir():
    return run("git rev-parse --git-common-dir 2>/dev/null")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_tracks(ref):
    output = run(f"git show {ref}:.agent/kf/tracks.yaml 2>/dev/null")
    if not output:
        return {}
    try:
        data = yaml.safe_load(output)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def load_deps(ref):
    output = run(f"git show {ref}:.agent/kf/tracks/deps.yaml 2>/dev/null")
    if not output:
        return {}
    try:
        data = yaml.safe_load(output)
        if not isinstance(data, dict):
            return {}
        return {k: (v if isinstance(v, list) else []) for k, v in data.items()}
    except yaml.YAMLError:
        return {}


def load_track_detail(ref, track_id):
    output = run(f"git show {ref}:.agent/kf/tracks/{track_id}/track.yaml 2>/dev/null")
    return output if output else "(no track.yaml found)"


def load_claims():
    output = run(f"{SCRIPT_DIR}/kf-claim.py list --json")
    if not output:
        return {}
    try:
        claims = json.loads(output)
        return {c.get("track_id"): c.get("worktree", "?") for c in claims if c.get("track_id")}
    except (json.JSONDecodeError, TypeError):
        return {}


def load_manager_state():
    """Read conductor manager state."""
    common = git_common_dir()
    if not common:
        return None
    mgr_file = Path(common) / "kf-conductor" / "_manager.json"
    if not mgr_file.exists():
        return None
    try:
        return json.loads(mgr_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_lock_state():
    """Check if the merge lock is currently held."""
    common = git_common_dir()
    if not common:
        return None
    lock_dir = Path(common) / "merge.lock"
    if lock_dir.is_dir():
        info_file = lock_dir / "info"
        if info_file.exists():
            try:
                return info_file.read_text().strip()
            except OSError:
                return "locked (unknown holder)"
        return "locked"
    return None


def load_worker_statuses():
    """Read all conductor worker status files."""
    common = git_common_dir()
    if not common:
        return []
    cond_dir = Path(common) / "kf-conductor"
    if not cond_dir.is_dir():
        return []
    workers = []
    for sf in sorted(cond_dir.glob("*.json")):
        if sf.name.startswith("_"):
            continue
        try:
            data = json.loads(sf.read_text())
            workers.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return workers


# ---------------------------------------------------------------------------
# Manager control
# ---------------------------------------------------------------------------

def manager_control(action):
    """Send a control command to the conductor manager."""
    common = git_common_dir()
    if not common:
        return False, "Not in a git repo"
    mgr_file = Path(common) / "kf-conductor" / "_manager.json"
    if not mgr_file.exists():
        if action == "start":
            return _start_manager()
        return False, "Manager not running"

    try:
        data = json.loads(mgr_file.read_text())
    except (json.JSONDecodeError, OSError):
        if action == "start":
            return _start_manager()
        return False, "Cannot read manager state"

    current = data.get("state", "stopped")

    if action == "start":
        if current == "running":
            return False, "Already running"
        return _start_manager()
    elif action == "stop":
        if current == "stopped":
            return False, "Already stopped"
        data["state"] = "stopping"
        mgr_file.write_text(json.dumps(data, indent=2) + "\n")
        return True, "Stopping..."
    elif action == "suspend":
        if current != "running":
            return False, f"Cannot suspend (state: {current})"
        data["state"] = "suspended"
        mgr_file.write_text(json.dumps(data, indent=2) + "\n")
        return True, "Suspended"
    elif action == "resume":
        if current != "suspended":
            return False, f"Cannot resume (state: {current})"
        data["state"] = "running"
        mgr_file.write_text(json.dumps(data, indent=2) + "\n")
        return True, "Resumed"
    return False, f"Unknown action: {action}"


def _start_manager():
    """Start the conductor manager in a new tmux window."""
    conductor = os.path.join(SCRIPT_DIR, "kf-conductor.py")
    result = subprocess.run(
        ["tmux", "new-window", "-n", "kf-manager", "-d",
         f"{sys.executable} {conductor} start --timeout 30"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, f"Failed to start: {result.stderr.strip()}"
    return True, "Manager started in kf-manager window"


# ---------------------------------------------------------------------------
# Track state model
# ---------------------------------------------------------------------------

class TrackState:
    def __init__(self, ref):
        self.ref = ref
        self.tracks = {}
        self.deps = {}
        self.claims = {}      # track_id -> worktree name
        self.changes = {}
        self.last_commit = ""
        self.manager = None
        self.workers = []
        self.lock_info = None  # None = unlocked, str = holder info
        self.refresh()

    def refresh(self):
        self.tracks = load_tracks(self.ref)
        self.deps = load_deps(self.ref)
        self.claims = load_claims()
        self.last_commit = get_head_commit(self.ref)
        self.manager = load_manager_state()
        self.workers = load_worker_statuses()
        self.lock_info = load_lock_state()

    def has_changes(self):
        return bool(self.changes)

    def manager_state(self):
        if not self.manager:
            return "stopped"
        return self.manager.get("state", "stopped")

    def is_approved(self, track_id):
        if track_id in self.changes:
            return self.changes[track_id]
        info = self.tracks.get(track_id, {})
        return bool(info.get("approved", False))

    def toggle_approval(self, track_id):
        self.changes[track_id] = not self.is_approved(track_id)

    def approve_all_backlog(self):
        for tid, info in self.tracks.items():
            if info.get("status") == "pending" and not self.is_approved(tid):
                self.changes[tid] = True

    def unapprove_all(self):
        for tid, info in self.tracks.items():
            if info.get("status") == "pending" and self.is_approved(tid):
                self.changes[tid] = False

    def sections(self):
        backlog = []
        approved = []
        in_progress = []

        for tid, info in sorted(self.tracks.items()):
            if not isinstance(info, dict):
                continue
            status = info.get("status", "")
            entry = {"id": tid, **info}

            if status == "in-progress" or tid in self.claims:
                worker = self.claims.get(tid, "")
                entry["_worker"] = worker
                in_progress.append(entry)
            elif status == "pending":
                if self.is_approved(tid):
                    approved.append(entry)
                else:
                    backlog.append(entry)

        return backlog, approved, in_progress

    def save(self):
        if not self.changes:
            return True, "No changes to save"

        lock_script = os.path.join(SCRIPT_DIR, "kf-merge-lock.py")
        track_script = os.path.join(SCRIPT_DIR, "kf-track.py")

        rc = subprocess.run(
            [lock_script, "acquire", "--holder", "tui-approval", "--timeout", "0"],
            capture_output=True, text=True,
        )
        if rc.returncode != 0:
            return False, f"Lock held — try again shortly"

        try:
            for tid, approved in self.changes.items():
                cmd = "approve" if approved else "disapprove"
                subprocess.run(
                    [track_script, cmd, tid],
                    capture_output=True, text=True,
                )

            subprocess.run(["git", "add", ".agent/kf/tracks.yaml"],
                           capture_output=True, text=True)
            ids = ", ".join(sorted(self.changes.keys()))
            subprocess.run(
                ["git", "commit", "-m",
                 f"chore(kf): update track approvals\n\nTracks: {ids}"],
                capture_output=True, text=True,
            )
            self.changes.clear()
            self.refresh()
            return True, "Saved"
        finally:
            subprocess.run(
                [lock_script, "release", "--holder", "tui-approval"],
                capture_output=True, text=True,
            )


# ---------------------------------------------------------------------------
# Commit watcher
# ---------------------------------------------------------------------------

class CommitWatcher:
    def __init__(self, state: TrackState, interval: float = 3.0):
        self.state = state
        self.interval = interval
        self._stop = threading.Event()
        self._changed = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def check(self):
        if self._changed.is_set():
            self._changed.clear()
            return True
        return False

    def _poll(self):
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            if self._stop.is_set():
                break
            new_commit = get_head_commit(self.state.ref)
            if new_commit and new_commit != self.state.last_commit:
                self._changed.set()


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------

def show_help_panel(stdscr):
    """Show a modal help overlay with all keybindings."""
    help_lines = [
        "",
        "  NAVIGATION",
        "  ──────────────────────────────────────",
        "  ↑ / k          Move up",
        "  ↓ / j          Move down",
        "",
        "  TRACK ACTIONS",
        "  ──────────────────────────────────────",
        "  SPACE           Toggle approval on track",
        "  ENTER           View full track details",
        "  a               Approve all backlog tracks",
        "  u               Unapprove all approved tracks",
        "  s               Save approval changes",
        "  r               Refresh from primary branch",
        "",
        "  MANAGER CONTROLS",
        "  ──────────────────────────────────────",
        "  F5              Start manager (dispatch loop)",
        "  F6              Suspend manager (pause dispatch)",
        "  F7              Resume manager",
        "  F8              Stop manager (graceful shutdown)",
        "",
        "  OTHER",
        "  ──────────────────────────────────────",
        "  h / ?           Show this help panel",
        "  q               Quit (prompts if unsaved)",
        "",
    ]

    h, w = stdscr.getmaxyx()
    box_w = min(50, w - 4)
    box_h = min(len(help_lines) + 2, h - 2)
    start_y = max(0, (h - box_h) // 2)
    start_x = max(0, (w - box_w) // 2)

    while True:
        # Draw box background
        for y in range(box_h):
            try:
                stdscr.addnstr(start_y + y, start_x, " " * box_w, box_w, curses.A_REVERSE)
            except curses.error:
                pass

        # Title
        title = " Help — Keybindings "
        try:
            stdscr.addnstr(start_y, start_x + (box_w - len(title)) // 2, title, box_w,
                           curses.A_REVERSE | curses.A_BOLD)
        except curses.error:
            pass

        # Content
        for i, line in enumerate(help_lines[:box_h - 2]):
            try:
                stdscr.addnstr(start_y + 1 + i, start_x, line[:box_w].ljust(box_w), box_w,
                               curses.A_REVERSE)
            except curses.error:
                pass

        # Footer
        close_text = " Press any key to close "
        try:
            stdscr.addnstr(start_y + box_h - 1, start_x + (box_w - len(close_text)) // 2,
                           close_text, box_w, curses.A_REVERSE | curses.A_DIM)
        except curses.error:
            pass

        stdscr.refresh()
        key = stdscr.getch()
        if key != -1:
            break


def show_detail_view(stdscr, state, track_id):
    content = load_track_detail(state.ref, track_id)
    lines = content.split("\n")
    scroll = 0
    h, w = stdscr.getmaxyx()

    while True:
        stdscr.clear()
        title = f" Track: {track_id} "
        stdscr.attron(curses.A_BOLD | curses.A_REVERSE)
        stdscr.addnstr(0, 0, title.center(w), w - 1)
        stdscr.attroff(curses.A_BOLD | curses.A_REVERSE)

        visible = h - 3
        for i, line in enumerate(lines[scroll:scroll + visible]):
            try:
                stdscr.addnstr(i + 2, 1, line[:w - 2], w - 2)
            except curses.error:
                pass

        footer = " [↑/↓] Scroll  [PgUp/PgDn] Page  [q/ESC] Back "
        try:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(h - 1, 0, footer.ljust(w), w - 1)
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass

        stdscr.refresh()
        key = stdscr.getch()

        if key in (ord("q"), 27):
            break
        elif key == curses.KEY_UP or key == ord("k"):
            scroll = max(0, scroll - 1)
        elif key == curses.KEY_DOWN or key == ord("j"):
            scroll = min(max(0, len(lines) - visible), scroll + 1)
        elif key == curses.KEY_PPAGE:
            scroll = max(0, scroll - visible)
        elif key == curses.KEY_NPAGE:
            scroll = min(max(0, len(lines) - visible), scroll + visible)


# ---------------------------------------------------------------------------
# Main TUI
# ---------------------------------------------------------------------------

def safe_addnstr(stdscr, y, x, text, maxlen, attr=0):
    try:
        stdscr.addnstr(y, x, text, maxlen, attr)
    except curses.error:
        pass


def tui_main(stdscr, ref):
    curses.curs_set(0)
    curses.use_default_colors()

    curses.init_pair(1, curses.COLOR_GREEN, -1)     # approved / running
    curses.init_pair(2, curses.COLOR_YELLOW, -1)     # backlog / suspended
    curses.init_pair(3, curses.COLOR_CYAN, -1)       # in-progress
    curses.init_pair(4, curses.COLOR_RED, -1)         # changed / stopped
    curses.init_pair(5, curses.COLOR_WHITE, -1)       # section header
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)     # worker info

    C_GREEN = curses.color_pair(1)
    C_YELLOW = curses.color_pair(2)
    C_CYAN = curses.color_pair(3)
    C_RED = curses.color_pair(4)
    C_SECTION = curses.color_pair(5) | curses.A_BOLD
    C_WORKER = curses.color_pair(6)

    state = TrackState(ref)
    watcher = CommitWatcher(state)
    watcher.start()

    cursor = 0
    scroll = 0
    status_msg = ""
    status_time = 0

    def manager_color():
        s = state.manager_state()
        if s == "running":
            return C_GREEN
        elif s == "suspended":
            return C_YELLOW
        elif s in ("stopping", "stopped"):
            return C_RED
        return 0

    def flat_list():
        backlog, approved, in_progress = state.sections()
        rows = []
        if backlog:
            rows.append(("section", f"── BACKLOG ({len(backlog)}) ──", None))
            for t in backlog:
                rows.append(("backlog", t, t["id"] in state.changes))
        if approved:
            rows.append(("section", f"── APPROVED ({len(approved)}) ──", None))
            for t in approved:
                rows.append(("approved", t, t["id"] in state.changes))
        if in_progress:
            rows.append(("section", f"── IN-PROGRESS ({len(in_progress)}) ──", None))
            for t in in_progress:
                rows.append(("in_progress", t, False))
        if not rows:
            rows.append(("section", "── NO TRACKS ──", None))
        return rows

    stdscr.timeout(500)

    while True:
        # Auto-refresh on new commits
        if watcher.check():
            state.refresh()
            status_msg = "↻ Refreshed (new commit)"
            status_time = time.time()

        # Periodically refresh manager/worker/lock state (every cycle, it's cheap)
        state.manager = load_manager_state()
        state.workers = load_worker_statuses()
        state.lock_info = load_lock_state()

        rows = flat_list()
        h, w = stdscr.getmaxyx()

        # Clamp cursor
        selectable = [i for i, r in enumerate(rows) if r[0] != "section"]
        if not selectable:
            cursor = 0
        elif cursor >= len(selectable):
            cursor = len(selectable) - 1

        cursor_row = selectable[cursor] if selectable else -1

        # Header takes 3 lines, footer 2 lines
        header_lines = 3
        footer_lines = 2
        visible = h - header_lines - footer_lines

        if cursor_row >= scroll + visible:
            scroll = cursor_row - visible + 1
        if cursor_row < scroll:
            scroll = cursor_row
        scroll = max(0, scroll)

        # === Draw ===
        stdscr.clear()

        # --- Header (3 lines) ---
        mgr_state = state.manager_state().upper()
        mgr_col = manager_color()

        # Line 0: title bar
        changed_str = f"  [{len(state.changes)} unsaved]" if state.has_changes() else ""
        title_bar = f" KILOFORGE CONDUCTOR{changed_str} "
        stdscr.attron(curses.A_BOLD | curses.A_REVERSE)
        safe_addnstr(stdscr, 0, 0, title_bar.ljust(w), w - 1)
        stdscr.attroff(curses.A_BOLD | curses.A_REVERSE)

        # Line 1: manager state + worker summary
        running_workers = [w for w in state.workers if w.get("state") == "running"]
        completed_workers = [w for w in state.workers if w.get("state") == "completed"]
        mgr_line = f" Manager: "
        safe_addnstr(stdscr, 1, 0, mgr_line, w - 1, curses.A_BOLD)
        safe_addnstr(stdscr, 1, len(mgr_line), mgr_state, w - 1, mgr_col | curses.A_BOLD)

        worker_summary = f"  Workers: {len(running_workers)} running, {len(completed_workers)} completed"
        safe_addnstr(stdscr, 1, len(mgr_line) + len(mgr_state), worker_summary, w - 1, C_WORKER)

        # Lock indicator on line 1
        lock_offset = len(mgr_line) + len(mgr_state) + len(worker_summary)
        if state.lock_info:
            lock_str = "  🔒 LOCKED"
            safe_addnstr(stdscr, 1, lock_offset, lock_str, w - 1, C_RED | curses.A_BOLD)

        # Line 2: active worker details
        if running_workers:
            parts = []
            for rw in running_workers[:4]:  # show up to 4
                parts.append(f"{rw.get('worker','?')}→{rw.get('track_id','?')}")
            worker_detail = " Active: " + "  ".join(parts)
            if len(running_workers) > 4:
                worker_detail += f"  +{len(running_workers)-4} more"
            safe_addnstr(stdscr, 2, 0, worker_detail[:w - 1], w - 1, C_CYAN)
        else:
            safe_addnstr(stdscr, 2, 0, " No active workers", w - 1, curses.A_DIM)

        # --- Track rows ---
        for idx in range(scroll, min(len(rows), scroll + visible)):
            y = idx - scroll + header_lines
            if y >= h - footer_lines:
                break

            kind, data, changed = rows[idx]
            is_selected = (idx == cursor_row)

            if kind == "section":
                attr = C_SECTION
                line = f"  {data}"
            else:
                tid = data["id"]
                title = data.get("title", "")
                ttype = data.get("type", "")[:10]
                deps = state.deps.get(tid, [])
                dep_str = f"deps:{len(deps)}" if deps else ""

                if kind == "backlog":
                    checkbox = "[ ]"
                    attr = C_YELLOW
                elif kind == "approved":
                    checkbox = "[✓]"
                    attr = C_GREEN
                else:
                    checkbox = "[~]"
                    attr = C_CYAN
                    worker = data.get("_worker", "")
                    if worker:
                        dep_str = f"@{worker}"

                marker = "*" if changed else " "
                # Truncate title to fit
                meta_len = 3 + 1 + 45 + 1 + 10 + 1 + 8 + 1  # marker+checkbox+tid+type+dep+spaces
                max_title = max(0, w - meta_len - 2)
                title_trunc = title[:max_title]
                line = f" {marker}{checkbox} {tid:<45} {ttype:<10} {dep_str:<8} {title_trunc}"

            if is_selected:
                attr |= curses.A_REVERSE

            safe_addnstr(stdscr, y, 0, line[:w - 1].ljust(w - 1), w - 1, attr)

        # --- Status line ---
        if status_msg and (time.time() - status_time) > 5:
            status_msg = ""
        status_y = h - 2
        safe_addnstr(stdscr, status_y, 0, f"  {status_msg}".ljust(w - 1), w - 1,
                      C_RED if status_msg else 0)

        # --- Footer ---
        footer = " [SPC]Toggle [RET]Detail [a]All [u]None [s]Save [r]Refresh  [F5]Start [F6]Pause [F7]Resume [F8]Stop  [?]Help [q]Quit "
        stdscr.attron(curses.A_REVERSE)
        safe_addnstr(stdscr, h - 1, 0, footer[:w - 1].ljust(w - 1), w - 1)
        stdscr.attroff(curses.A_REVERSE)

        stdscr.refresh()

        # === Input ===
        key = stdscr.getch()

        if key == -1:
            continue

        # Navigation
        elif key == curses.KEY_UP or key == ord("k"):
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_DOWN or key == ord("j"):
            cursor = min(len(selectable) - 1, cursor + 1) if selectable else 0

        # Toggle approval
        elif key == ord(" "):
            if selectable and cursor < len(selectable):
                row_idx = selectable[cursor]
                kind, data, _ = rows[row_idx]
                if kind in ("backlog", "approved"):
                    state.toggle_approval(data["id"])

        # Detail view
        elif key in (curses.KEY_ENTER, 10, 13):
            if selectable and cursor < len(selectable):
                row_idx = selectable[cursor]
                kind, data, _ = rows[row_idx]
                if kind != "section":
                    stdscr.timeout(-1)
                    show_detail_view(stdscr, state, data["id"])
                    stdscr.timeout(500)

        # Bulk actions
        elif key == ord("a"):
            state.approve_all_backlog()
            status_msg = "All backlog tracks approved (unsaved)"
            status_time = time.time()
        elif key == ord("u"):
            state.unapprove_all()
            status_msg = "All tracks unapproved (unsaved)"
            status_time = time.time()

        # Save
        elif key == ord("s"):
            ok, msg = state.save()
            status_msg = msg
            status_time = time.time()

        # Refresh
        elif key == ord("r"):
            state.refresh()
            status_msg = "Refreshed"
            status_time = time.time()

        # Help panel
        elif key == ord("h") or key == ord("?"):
            stdscr.timeout(-1)
            show_help_panel(stdscr)
            stdscr.timeout(500)

        # Manager controls
        elif key == curses.KEY_F5:
            ok, msg = manager_control("start")
            status_msg = f"Start: {msg}"
            status_time = time.time()
        elif key == curses.KEY_F6:
            ok, msg = manager_control("suspend")
            status_msg = f"Suspend: {msg}"
            status_time = time.time()
        elif key == curses.KEY_F7:
            ok, msg = manager_control("resume")
            status_msg = f"Resume: {msg}"
            status_time = time.time()
        elif key == curses.KEY_F8:
            ok, msg = manager_control("stop")
            status_msg = f"Stop: {msg}"
            status_time = time.time()

        # Quit
        elif key == ord("q"):
            if state.has_changes():
                safe_addnstr(stdscr, status_y, 0,
                             "  Unsaved changes! [s] Save & quit  [q] Discard  [c] Cancel".ljust(w - 1),
                             w - 1, C_RED)
                stdscr.refresh()
                stdscr.timeout(-1)
                confirm = stdscr.getch()
                stdscr.timeout(500)
                if confirm == ord("s"):
                    ok, msg = state.save()
                    if ok:
                        break
                    status_msg = msg
                    status_time = time.time()
                elif confirm == ord("q"):
                    break
            else:
                break

    watcher.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kiloforge conductor control TUI")
    parser.add_argument("--ref", default=None, help="Branch to read track state from")
    args = parser.parse_args()

    ref = args.ref or get_primary_branch()
    curses.wrapper(lambda stdscr: tui_main(stdscr, ref))


if __name__ == "__main__":
    main()
