#!/usr/bin/env python3
"""kf-install — Install or update Kiloforge CLI tools in a project.

Copies the latest scripts from the skills repo into .agent/kf/bin/,
sets up the project-local venv, rewrites shebangs, and cleans up
legacy files.

USAGE:
    kf-install [--project-dir DIR] [--skills-dir DIR] [--skip-venv]

OPTIONS:
    --project-dir DIR   Target project root (default: cwd)
    --skills-dir DIR    Path to kiloforge-skills repo (default: auto-detect from this script's location)
    --skip-venv         Skip venv creation/update (just copy scripts)

Run from anywhere — it auto-detects the skills repo from its own location.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


# Scripts that were renamed to .py — clean up old versions
LEGACY_NAMES = [
    "kf-preflight",
    "kf-primary-branch",
    "kf-merge",
    "kf-merge-lock",
    "kf-track",
    "kf-track-content",
    "kf-worktree-env",
    "kf-dispatch",
    "kf-claim",
]


def detect_skills_dir(script_path: Path) -> Path:
    """Detect the kiloforge-skills repo root from this script's location."""
    # This script lives at kf-bin/scripts/kf-install.py
    # Skills repo root is two levels up
    return script_path.parent.parent.parent


def ensure_venv(project_dir: Path) -> Path:
    """Create or update the project-local venv at .agent/kf/.venv."""
    venv_dir = project_dir / ".agent" / "kf" / ".venv"

    if not venv_dir.is_dir():
        print(f"Creating venv at {venv_dir}...")
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"ERROR: Failed to create venv: {result.stderr}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Venv exists at {venv_dir}")

    # Install/upgrade PyYAML
    pip = venv_dir / "bin" / "pip"
    if not pip.exists():
        pip = venv_dir / "Scripts" / "pip"  # Windows
    print("Installing PyYAML...")
    subprocess.run(
        [str(pip), "install", "-q", "pyyaml"],
        capture_output=True, text=True,
    )

    # Verify
    python = venv_dir / "bin" / "python"
    if not python.exists():
        python = venv_dir / "Scripts" / "python"  # Windows
    result = subprocess.run(
        [str(python), "-c", "import yaml; print('PyYAML', yaml.__version__)"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  {result.stdout.strip()}")
    else:
        print(f"WARNING: PyYAML verification failed: {result.stderr}", file=sys.stderr)

    return venv_dir


def ensure_gitignore(project_dir: Path):
    """Ensure .agent/kf/.gitignore contains .venv entry."""
    gitignore = project_dir / ".agent" / "kf" / ".gitignore"
    gitignore.parent.mkdir(parents=True, exist_ok=True)

    entries_needed = [".venv"]
    existing = set()
    if gitignore.exists():
        existing = set(gitignore.read_text().splitlines())

    missing = [e for e in entries_needed if e not in existing]
    if missing:
        with gitignore.open("a") as f:
            for entry in missing:
                f.write(f"{entry}\n")
        print(f"Updated .agent/kf/.gitignore: added {', '.join(missing)}")


def copy_scripts(skills_dir: Path, project_dir: Path) -> list[str]:
    """Copy scripts and lib from skills repo to project."""
    src = skills_dir / "kf-bin" / "scripts"
    dst = project_dir / ".agent" / "kf" / "bin"

    if not src.is_dir():
        print(f"ERROR: Scripts source not found at {src}", file=sys.stderr)
        sys.exit(1)

    dst.mkdir(parents=True, exist_ok=True)

    copied = []

    # Copy Python scripts
    for f in sorted(src.glob("*.py")):
        shutil.copy2(f, dst / f.name)
        os.chmod(dst / f.name, 0o755)
        copied.append(f.name)

    # Copy lib/ directory
    src_lib = src / "lib"
    dst_lib = dst / "lib"
    if src_lib.is_dir():
        if dst_lib.exists():
            shutil.rmtree(dst_lib)
        shutil.copytree(src_lib, dst_lib)
        lib_files = list(src_lib.rglob("*.py"))
        copied.append(f"lib/ ({len(lib_files)} files)")

    return copied


def rewrite_shebangs(project_dir: Path, venv_dir: Path):
    """Rewrite Python script shebangs to use the project-local venv."""
    bin_dir = project_dir / ".agent" / "kf" / "bin"
    python_path = str(venv_dir / "bin" / "python")
    if not Path(python_path).exists():
        python_path = str(venv_dir / "Scripts" / "python")  # Windows

    count = 0
    for f in sorted(bin_dir.glob("*.py")):
        text = f.read_text()
        lines = text.split("\n", 1)
        if lines and "python" in lines[0]:
            new_shebang = f"#!{python_path}"
            if lines[0] != new_shebang:
                new_text = new_shebang + "\n" + (lines[1] if len(lines) > 1 else "")
                f.write_text(new_text)
                count += 1

    print(f"Rewrote shebangs in {count} script(s)")


def clean_legacy(project_dir: Path) -> list[str]:
    """Remove old non-.py scripts that have been superseded."""
    bin_dir = project_dir / ".agent" / "kf" / "bin"
    removed = []

    for name in LEGACY_NAMES:
        old_file = bin_dir / name  # without .py
        py_file = bin_dir / f"{name}.py"
        if old_file.exists() and not old_file.suffix and py_file.exists():
            old_file.unlink()
            removed.append(name)

    return removed


def main():
    parser = argparse.ArgumentParser(
        description="Install or update Kiloforge CLI tools in a project.",
    )
    parser.add_argument(
        "--project-dir", default=os.getcwd(),
        help="Target project root (default: cwd)",
    )
    parser.add_argument(
        "--skills-dir", default=None,
        help="Path to kiloforge-skills repo (default: auto-detect)",
    )
    parser.add_argument(
        "--skip-venv", action="store_true",
        help="Skip venv creation/update",
    )
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    if args.skills_dir:
        skills_dir = Path(args.skills_dir).resolve()
    else:
        skills_dir = detect_skills_dir(Path(__file__).resolve())

    print("=" * 60)
    print("  Kiloforge CLI Tools — Install / Update")
    print("=" * 60)
    print(f"  Skills repo:  {skills_dir}")
    print(f"  Project:      {project_dir}")
    print(f"  Target:       {project_dir / '.agent' / 'kf' / 'bin'}")
    print("=" * 60)
    print()

    # Step 1: Venv
    if not args.skip_venv:
        venv_dir = ensure_venv(project_dir)
        ensure_gitignore(project_dir)
    else:
        venv_dir = project_dir / ".agent" / "kf" / ".venv"
        print("Skipping venv setup (--skip-venv)")

    print()

    # Step 2: Copy scripts
    print("Copying scripts...")
    copied = copy_scripts(skills_dir, project_dir)
    for name in copied:
        print(f"  {name}")

    print()

    # Step 3: Rewrite shebangs
    if venv_dir.is_dir():
        rewrite_shebangs(project_dir, venv_dir)
    else:
        print("WARNING: Venv not found — shebangs not updated", file=sys.stderr)

    print()

    # Step 4: Clean legacy
    removed = clean_legacy(project_dir)
    if removed:
        print(f"Cleaned {len(removed)} legacy script(s):")
        for name in removed:
            print(f"  removed {name} (superseded by {name}.py)")
    else:
        print("No legacy scripts to clean")

    print()
    print("=" * 60)
    print("  Install complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
