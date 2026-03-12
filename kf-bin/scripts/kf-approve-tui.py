#!/usr/bin/env python3
"""kf-approve-tui — Curses-based TUI for Kiloforge track approval.

Displays tracks in three sections (Backlog, Approved, In-Progress) and
lets the user toggle approval status. Watches for commits on the primary
branch and auto-refreshes. Saves changes via the merge lock.

USAGE:
    kf-approve-tui [--ref BRANCH]

KEYS:
    ↑/↓ or j/k    Navigate tracks
    SPACE          Toggle approval on highlighted track
    ENTER          View full track details
    a              Approve all backlog tracks
    u              Unapprove all approved tracks
    s              Save changes (acquire lock, commit, release)
    r              Refresh from primary branch
    q              Quit (prompts to save if unsaved changes)

REQUIRES:
    - PyYAML (installed in project venv)
    - Running inside a git repo with .agent/kf/ artifacts
"""

import curses
import json
import os
import subprocess
import sys
import time
import threading

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


def get_primary_branch():
    pb = run(f"{SCRIPT_DIR}/kf-primary-branch.py")
    return pb if pb else "main"


def get_head_commit(ref):
    """Get the HEAD commit hash of a branch."""
    return run(f"git rev-parse {ref} 2>/dev/null")


def load_tracks(ref):
    """Load tracks from the primary branch."""
    output = run(f"git show {ref}:.agent/kf/tracks.yaml 2>/dev/null")
    if not output:
        return {}
    try:
        data = yaml.safe_load(output)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def load_deps(ref):
    """Load dependency graph."""
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
    """Load full track.yaml content for detail view."""
    output = run(f"git show {ref}:.agent/kf/tracks/{track_id}/track.yaml 2>/dev/null")
    return output if output else "(no track.yaml found)"


def load_claims():
    """Get set of currently claimed track IDs."""
    output = run(f"{SCRIPT_DIR}/kf-claim.py list --json")
    if not output:
        return set()
    try:
        claims = json.loads(output)
        return {c.get("track_id") for c in claims if c.get("track_id")}
    except (json.JSONDecodeError, TypeError):
        return set()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class TrackState:
    """Holds all track data and pending approval changes."""

    def __init__(self, ref):
        self.ref = ref
        self.tracks = {}      # track_id -> info dict
        self.deps = {}        # track_id -> [dep_ids]
        self.claims = set()   # claimed track IDs
        self.changes = {}     # track_id -> new approved bool (pending save)
        self.last_commit = ""
        self.refresh()

    def refresh(self):
        """Reload from git."""
        self.tracks = load_tracks(self.ref)
        self.deps = load_deps(self.ref)
        self.claims = load_claims()
        self.last_commit = get_head_commit(self.ref)
        # Don't clear changes — keep pending edits across refreshes

    def has_changes(self):
        return bool(self.changes)

    def is_approved(self, track_id):
        if track_id in self.changes:
            return self.changes[track_id]
        info = self.tracks.get(track_id, {})
        return bool(info.get("approved", False))

    def toggle_approval(self, track_id):
        current = self.is_approved(track_id)
        self.changes[track_id] = not current

    def approve_all_backlog(self):
        for tid, info in self.tracks.items():
            if info.get("status") == "pending" and not self.is_approved(tid):
                self.changes[tid] = True

    def unapprove_all(self):
        for tid, info in self.tracks.items():
            if info.get("status") == "pending" and self.is_approved(tid):
                self.changes[tid] = False

    def sections(self):
        """Return tracks grouped into three sections."""
        backlog = []
        approved = []
        in_progress = []

        for tid, info in sorted(self.tracks.items()):
            if not isinstance(info, dict):
                continue
            status = info.get("status", "")
            entry = {"id": tid, **info}

            if status == "in-progress" or tid in self.claims:
                in_progress.append(entry)
            elif status == "pending":
                if self.is_approved(tid):
                    approved.append(entry)
                else:
                    backlog.append(entry)
            # completed/archived tracks are not shown

        return backlog, approved, in_progress

    def save(self):
        """Save pending changes: acquire lock, update tracks, commit, release."""
        if not self.changes:
            return True, "No changes to save"

        lock_script = os.path.join(SCRIPT_DIR, "kf-merge-lock.py")
        track_script = os.path.join(SCRIPT_DIR, "kf-track.py")

        # Acquire merge lock
        rc = subprocess.run(
            [lock_script, "acquire", "--holder", "tui-approval", "--timeout", "30"],
            capture_output=True, text=True,
        )
        if rc.returncode != 0:
            return False, f"Could not acquire merge lock: {rc.stderr.strip()}"

        try:
            for tid, approved in self.changes.items():
                cmd = "approve" if approved else "disapprove"
                result = subprocess.run(
                    [track_script, cmd, tid],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    return False, f"Failed to {cmd} {tid}: {result.stderr.strip()}"

            # Commit
            subprocess.run(
                ["git", "add", ".agent/kf/tracks.yaml"],
                capture_output=True, text=True,
            )
            ids = ", ".join(sorted(self.changes.keys()))
            subprocess.run(
                ["git", "commit", "-m", f"chore(kf): update track approvals\n\nTracks: {ids}"],
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
# Commit watcher (background thread)
# ---------------------------------------------------------------------------

class CommitWatcher:
    """Watches for new commits on the primary branch."""

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
        """Check if a change was detected (non-blocking). Clears the flag."""
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

def show_detail_view(stdscr, state, track_id):
    """Show full track detail in a scrollable view."""
    content = load_track_detail(state.ref, track_id)
    lines = content.split("\n")
    scroll = 0
    h, w = stdscr.getmaxyx()

    while True:
        stdscr.clear()
        # Header
        title = f" Track: {track_id} "
        stdscr.attron(curses.A_BOLD | curses.A_REVERSE)
        stdscr.addnstr(0, 0, title.center(w), w - 1)
        stdscr.attroff(curses.A_BOLD | curses.A_REVERSE)

        # Content
        visible = h - 3
        for i, line in enumerate(lines[scroll:scroll + visible]):
            try:
                stdscr.addnstr(i + 2, 1, line[:w - 2], w - 2)
            except curses.error:
                pass

        # Footer
        footer = " [↑/↓] Scroll  [q/ESC] Back "
        try:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(h - 1, 0, footer.ljust(w), w - 1)
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass

        stdscr.refresh()
        key = stdscr.getch()

        if key in (ord("q"), 27):  # q or ESC
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

def tui_main(stdscr, ref):
    curses.curs_set(0)
    curses.use_default_colors()

    # Colors
    curses.init_pair(1, curses.COLOR_GREEN, -1)    # approved
    curses.init_pair(2, curses.COLOR_YELLOW, -1)    # backlog
    curses.init_pair(3, curses.COLOR_CYAN, -1)      # in-progress
    curses.init_pair(4, curses.COLOR_RED, -1)        # changed marker
    curses.init_pair(5, curses.COLOR_WHITE, -1)      # section header

    COLOR_APPROVED = curses.color_pair(1)
    COLOR_BACKLOG = curses.color_pair(2)
    COLOR_INPROGRESS = curses.color_pair(3)
    COLOR_CHANGED = curses.color_pair(4)
    COLOR_SECTION = curses.color_pair(5) | curses.A_BOLD

    state = TrackState(ref)
    watcher = CommitWatcher(state)
    watcher.start()

    cursor = 0
    scroll = 0
    status_msg = ""
    status_time = 0

    def flat_list():
        """Build a flat list of displayable rows with section headers."""
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
        return rows

    stdscr.timeout(500)  # 500ms for non-blocking getch (to check watcher)

    while True:
        # Check for branch updates
        if watcher.check():
            state.refresh()
            status_msg = "Refreshed (new commit detected)"
            status_time = time.time()

        rows = flat_list()
        h, w = stdscr.getmaxyx()

        # Clamp cursor
        selectable = [i for i, r in enumerate(rows) if r[0] != "section"]
        if not selectable:
            cursor = 0
        elif cursor >= len(selectable):
            cursor = len(selectable) - 1

        # Map cursor to row index
        cursor_row = selectable[cursor] if selectable else -1

        # Scrolling
        visible = h - 4  # header + footer + status
        if cursor_row >= scroll + visible:
            scroll = cursor_row - visible + 1
        if cursor_row < scroll:
            scroll = cursor_row
        scroll = max(0, scroll)

        # Draw
        stdscr.clear()

        # Header
        header = " KILOFORGE TRACK APPROVAL "
        changed_str = f" ({len(state.changes)} unsaved)" if state.has_changes() else ""
        full_header = header + changed_str
        stdscr.attron(curses.A_BOLD | curses.A_REVERSE)
        try:
            stdscr.addnstr(0, 0, full_header.center(w), w - 1)
        except curses.error:
            pass
        stdscr.attroff(curses.A_BOLD | curses.A_REVERSE)

        # Rows
        for idx in range(scroll, min(len(rows), scroll + visible)):
            y = idx - scroll + 1
            if y >= h - 2:
                break

            kind, data, changed = rows[idx]
            is_selected = (idx == cursor_row)

            if kind == "section":
                attr = COLOR_SECTION
                line = f"  {data}"
            else:
                tid = data["id"]
                title = data.get("title", "")[:w - 60]
                ttype = data.get("type", "")[:10]
                deps = state.deps.get(tid, [])
                dep_str = f"deps:{len(deps)}" if deps else ""

                if kind == "backlog":
                    checkbox = "[ ]"
                    attr = COLOR_BACKLOG
                elif kind == "approved":
                    checkbox = "[✓]"
                    attr = COLOR_APPROVED
                else:  # in_progress
                    checkbox = "[~]"
                    attr = COLOR_INPROGRESS

                change_marker = "*" if changed else " "
                line = f" {change_marker}{checkbox} {tid:<45} {ttype:<10} {dep_str:<8} {title}"

            if is_selected:
                attr |= curses.A_REVERSE

            try:
                stdscr.addnstr(y, 0, line[:w - 1].ljust(w - 1), w - 1, attr)
            except curses.error:
                pass

        # Status message (auto-clear after 5s)
        if status_msg and (time.time() - status_time) > 5:
            status_msg = ""

        status_line = status_msg if status_msg else ""
        try:
            stdscr.addnstr(h - 2, 0, f"  {status_line}", w - 1, COLOR_CHANGED if status_msg else 0)
        except curses.error:
            pass

        # Footer
        footer = " [SPACE] Toggle  [ENTER] Detail  [a] Approve all  [u] Unapprove all  [s] Save  [r] Refresh  [q] Quit "
        try:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(h - 1, 0, footer[:w - 1].ljust(w - 1), w - 1)
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass

        stdscr.refresh()

        # Input
        key = stdscr.getch()

        if key == -1:
            continue  # timeout, loop to check watcher
        elif key == ord("q"):
            if state.has_changes():
                # Prompt to save
                try:
                    stdscr.addnstr(h - 2, 0, "  Unsaved changes! [s] Save & quit  [q] Quit without saving  [c] Cancel".ljust(w - 1), w - 1, COLOR_CHANGED)
                except curses.error:
                    pass
                stdscr.refresh()
                stdscr.timeout(-1)  # blocking
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
                # else cancel
            else:
                break

        elif key == curses.KEY_UP or key == ord("k"):
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_DOWN or key == ord("j"):
            cursor = min(len(selectable) - 1, cursor + 1) if selectable else 0

        elif key == ord(" "):
            # Toggle approval
            if selectable and cursor < len(selectable):
                row_idx = selectable[cursor]
                kind, data, _ = rows[row_idx]
                if kind in ("backlog", "approved"):
                    state.toggle_approval(data["id"])

        elif key in (curses.KEY_ENTER, 10, 13):
            # Detail view
            if selectable and cursor < len(selectable):
                row_idx = selectable[cursor]
                kind, data, _ = rows[row_idx]
                if kind != "section":
                    stdscr.timeout(-1)
                    show_detail_view(stdscr, state, data["id"])
                    stdscr.timeout(500)

        elif key == ord("a"):
            state.approve_all_backlog()
            status_msg = "All backlog tracks approved (unsaved)"
            status_time = time.time()

        elif key == ord("u"):
            state.unapprove_all()
            status_msg = "All tracks unapproved (unsaved)"
            status_time = time.time()

        elif key == ord("s"):
            ok, msg = state.save()
            status_msg = msg
            status_time = time.time()

        elif key == ord("r"):
            state.refresh()
            status_msg = "Refreshed"
            status_time = time.time()

    watcher.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kiloforge track approval TUI")
    parser.add_argument("--ref", default=None, help="Branch to read track state from")
    args = parser.parse_args()

    ref = args.ref or get_primary_branch()

    # Verify we have tracks
    tracks = load_tracks(ref)
    if not tracks:
        print("No tracks found. Create tracks first with /kf-architect.", file=sys.stderr)
        sys.exit(1)

    curses.wrapper(lambda stdscr: tui_main(stdscr, ref))


if __name__ == "__main__":
    main()
