# Python Setup for Kiloforge CLI Tools

All Kiloforge CLI tools require Python 3 and PyYAML. During `/kf-setup`, a project-local virtual environment is created at `.agent/kf/.venv` with these dependencies pre-installed. Script shebangs are rewritten to use this venv.

If a script fails with a Python-related error, follow this guide to restore the environment.

## Step 1: Install Python 3

Check if Python 3 is available:

```bash
python3 --version
```

If not found, install it for your platform:

### macOS

```bash
# Homebrew (recommended)
brew install python3

# If Homebrew is not installed:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python3
```

Alternatively, download the installer from https://python.org/downloads/macos/.

### Debian / Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv
```

The `python3-venv` package is required separately on Debian-based systems.

### Fedora / RHEL / CentOS

```bash
sudo dnf install -y python3 python3-pip
```

### Arch Linux / Manjaro

```bash
sudo pacman -S --noconfirm python python-pip
```

### Alpine Linux

```bash
apk add python3 py3-pip
```

### NixOS / Nix

```bash
nix-env -iA nixpkgs.python3
```

Or add `python3` to your `environment.systemPackages`.

### openSUSE

```bash
sudo zypper install -y python3 python3-pip
```

### Windows

**winget (recommended):**

```powershell
winget install Python.Python.3
```

**Chocolatey:**

```powershell
choco install python3
```

**Manual:** Download from https://python.org/downloads/windows/. During install, check "Add Python to PATH".

After installing on Windows, use `python` instead of `python3` (Windows installs as `python.exe`).

## Step 2: Create or Restore the Kiloforge venv

### macOS / Linux

```bash
# Create venv if missing
if [ ! -d ".agent/kf/.venv" ]; then
  mkdir -p .agent/kf
  python3 -m venv ".agent/kf/.venv"
fi

# Install PyYAML
".agent/kf/.venv/bin/pip" install pyyaml
```

If `python3 -m venv` fails on Debian/Ubuntu with "ensurepip is not available":

```bash
sudo apt-get install -y python3-venv
# Then retry venv creation
```

### Windows (PowerShell)

```powershell
# Create venv if missing
if (-not (Test-Path ".agent\kf\.venv")) {
  New-Item -ItemType Directory -Force -Path ".agent\kf"
  python -m venv ".agent\kf\.venv"
}

# Install PyYAML
& ".agent\kf\.venv\Scripts\pip" install pyyaml
```

## Step 3: Verify

### macOS / Linux

```bash
".agent/kf/.venv/bin/python" -c "import yaml; print('PyYAML', yaml.__version__)"
```

### Windows (PowerShell)

```powershell
& ".agent\kf\.venv\Scripts\python" -c "import yaml; print('PyYAML', yaml.__version__)"
```

## Rewrite Script Shebangs (macOS / Linux only)

If scripts were installed before the venv existed, their shebangs may point to the wrong interpreter:

```bash
KF_PYTHON=".agent/kf/.venv/bin/python"
for f in .agent/kf/bin/*; do
  if head -1 "$f" | grep -q python; then
    sed -i.bak "1s|.*|#!$KF_PYTHON|" "$f" && rm -f "$f.bak"
  fi
done
```

On Windows, shebangs are ignored — Python scripts are invoked directly via the venv's `python.exe`.

## Re-run Setup

If the above steps don't resolve the issue, re-run `/kf-setup` which handles all of this automatically.
