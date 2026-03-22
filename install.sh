#!/bin/sh
# Kiloforge Skills — one-line installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Kiloforge/kiloforge-skills/main/install.sh | sh
#
# What it does:
#   1. Downloads the latest release from GitHub
#   2. Installs skills to ~/.claude/skills/kf-*
#   3. Installs CLI tools to ~/.kf/bin/
#   4. Creates the Python venv at ~/.kf/.venv with PyYAML
#   5. Writes the current version to ~/.kf/VERSION
#
# Prerequisites:
#   - Python 3.8+ (python3 in PATH)
#   - Git (git in PATH)
#   - curl or wget
#   - Claude Code CLI (claude in PATH) — for using skills after install
#
# Platforms: macOS, Linux, WSL. Native Windows not supported.

set -e

REPO="Kiloforge/kiloforge-skills"
KF_HOME="$HOME/.kf"
SKILLS_DIR="$HOME/.claude/skills"
TMPDIR_INSTALL=""

info() { printf "  \033[36m%s\033[0m %s\n" "$1" "$2"; }
warn() { printf "  \033[33m%s\033[0m %s\n" "WARN" "$1"; }
fail() { printf "  \033[31m%s\033[0m %s\n" "ERROR" "$1"; exit 1; }

cleanup() {
  if [ -n "$TMPDIR_INSTALL" ] && [ -d "$TMPDIR_INSTALL" ]; then
    rm -rf "$TMPDIR_INSTALL"
  fi
}
trap cleanup EXIT

# --- Check prerequisites ---
command -v python3 >/dev/null 2>&1 || fail "python3 not found. Install Python 3.8+ first."
command -v git >/dev/null 2>&1 || fail "git not found. Install Git first."
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || fail "curl or wget required."

# --- Resolve latest version ---
info "Checking" "latest release..."
if command -v curl >/dev/null 2>&1; then
  LATEST=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null)
else
  LATEST=$(wget -qO- "https://api.github.com/repos/$REPO/releases/latest" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null)
fi

if [ -z "$LATEST" ]; then
  fail "Could not determine latest release. Check https://github.com/$REPO/releases"
fi

VERSION="${LATEST#v}"
info "Version" "$VERSION"

# --- Check if already up to date ---
if [ -f "$KF_HOME/VERSION" ]; then
  CURRENT=$(cat "$KF_HOME/VERSION" 2>/dev/null)
  if [ "$CURRENT" = "$VERSION" ]; then
    info "Up to date" "Kiloforge Skills $VERSION already installed."
    exit 0
  fi
  info "Upgrading" "$CURRENT → $VERSION"
fi

# --- Download release ---
TMPDIR_INSTALL=$(mktemp -d)
ARCHIVE="$TMPDIR_INSTALL/kiloforge-skills.tar.gz"

info "Downloading" "$LATEST..."
DOWNLOAD_URL="https://github.com/$REPO/archive/refs/tags/$LATEST.tar.gz"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$DOWNLOAD_URL" -o "$ARCHIVE"
else
  wget -q "$DOWNLOAD_URL" -O "$ARCHIVE"
fi

info "Extracting" "..."
tar xzf "$ARCHIVE" -C "$TMPDIR_INSTALL"
SRCDIR="$TMPDIR_INSTALL/kiloforge-skills-$VERSION"

if [ ! -d "$SRCDIR" ]; then
  # Some releases use different directory names
  SRCDIR=$(find "$TMPDIR_INSTALL" -maxdepth 1 -type d -name "kiloforge-skills*" | head -1)
fi

[ -d "$SRCDIR" ] || fail "Could not find extracted source directory"

# --- Install skills to ~/.claude/skills/ ---
info "Installing" "skills to $SKILLS_DIR..."
mkdir -p "$SKILLS_DIR"

for skill_dir in "$SRCDIR"/kf-*/; do
  skill_name=$(basename "$skill_dir")
  [ -f "$skill_dir/SKILL.md" ] || continue

  target="$SKILLS_DIR/$skill_name"
  mkdir -p "$target"
  cp "$skill_dir/SKILL.md" "$target/SKILL.md"

  # Copy references/ if present
  if [ -d "$skill_dir/references" ]; then
    mkdir -p "$target/references"
    cp "$skill_dir/references/"*.md "$target/references/" 2>/dev/null || true
  fi

  # Copy resources/ if present
  if [ -d "$skill_dir/resources" ]; then
    mkdir -p "$target/resources"
    cp -r "$skill_dir/resources/"* "$target/resources/" 2>/dev/null || true
  fi

  # Copy scripts/ if present
  if [ -d "$skill_dir/scripts" ]; then
    mkdir -p "$target/scripts"
    cp -r "$skill_dir/scripts/"* "$target/scripts/" 2>/dev/null || true
  fi
done

# Count installed skills
SKILL_COUNT=$(find "$SKILLS_DIR" -maxdepth 1 -name "kf-*" -type d | wc -l | tr -d ' ')
info "Skills" "$SKILL_COUNT kf-* skills installed"

# --- Install CLI tools to ~/.kf/bin/ ---
info "Installing" "CLI tools to $KF_HOME/bin/..."
mkdir -p "$KF_HOME/bin/lib"

# Copy scripts
for script in "$SRCDIR"/kf-bin/scripts/*.py; do
  [ -f "$script" ] || continue
  cp "$script" "$KF_HOME/bin/"
  chmod +x "$KF_HOME/bin/$(basename "$script")"
done

# Copy lib/
for lib_file in "$SRCDIR"/kf-bin/scripts/lib/*.py; do
  [ -f "$lib_file" ] || continue
  cp "$lib_file" "$KF_HOME/bin/lib/"
done

# Ensure lib/__init__.py exists
touch "$KF_HOME/bin/lib/__init__.py"

# --- Create venv ---
VENV_DIR="$KF_HOME/.venv"
if [ ! -d "$VENV_DIR" ]; then
  info "Creating" "Python venv at $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
fi

# Install PyYAML
info "Installing" "dependencies..."
"$VENV_DIR/bin/pip" install -q pyyaml 2>/dev/null || "$VENV_DIR/Scripts/pip" install -q pyyaml 2>/dev/null || warn "Could not install PyYAML into venv"

# --- Write version ---
echo "$VERSION" > "$KF_HOME/VERSION"

# --- Done ---
echo ""
echo "  ✓ Kiloforge Skills $VERSION installed successfully"
echo ""
echo "  Skills:  $SKILLS_DIR/kf-*"
echo "  CLI:     $KF_HOME/bin/"
echo "  Venv:    $VENV_DIR"
echo ""
echo "  Next steps:"
echo "    1. Open Claude Code in your project: claude"
echo "    2. Run /kf-setup to initialize Kiloforge"
echo "    3. Run /kf-architect to create your first tracks"
echo ""
echo "  Update later with: /kf-update"
echo ""
