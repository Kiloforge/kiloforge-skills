# Kiloforge Skills — Windows installer (PowerShell)
#
# Usage:
#   irm https://raw.githubusercontent.com/Kiloforge/kiloforge-skills/main/install.ps1 | iex
#
# What it does:
#   1. Downloads the latest release from GitHub
#   2. Installs skills to ~/.claude/skills/kf-*
#   3. Installs CLI tools to ~/.kf/bin/
#   4. Creates the Python venv at ~/.kf/.venv with PyYAML
#   5. Writes the current version to ~/.kf/VERSION
#
# Prerequisites:
#   - Python 3.8+ (python3 or python in PATH)
#   - Git (git in PATH)
#   - Claude Code CLI (claude in PATH) — for using skills after install

$ErrorActionPreference = "Stop"

$Repo = "Kiloforge/kiloforge-skills"
$KfHome = Join-Path $HOME ".kf"
$SkillsDir = Join-Path $HOME ".claude" "skills"

function Info($label, $msg) { Write-Host "  $label" -ForegroundColor Cyan -NoNewline; Write-Host " $msg" }
function Warn($msg) { Write-Host "  WARN" -ForegroundColor Yellow -NoNewline; Write-Host " $msg" }
function Fail($msg) { Write-Host "  ERROR" -ForegroundColor Red -NoNewline; Write-Host " $msg"; exit 1 }

# --- Check prerequisites ---
$python = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" }
          elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" }
          else { Fail "Python 3 not found. Install from https://python.org" }

& $python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Python 3.8+ required." }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git not found. Install from https://git-scm.com" }

# --- Resolve latest version ---
Info "Checking" "latest release..."
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -ErrorAction Stop
$tag = $release.tag_name
$version = $tag -replace '^v', ''

if (-not $version) { Fail "Could not determine latest release." }
Info "Version" $version

# --- Check if already up to date ---
$versionFile = Join-Path $KfHome "VERSION"
if (Test-Path $versionFile) {
    $current = (Get-Content $versionFile -Raw).Trim()
    if ($current -eq $version) {
        Info "Up to date" "Kiloforge Skills $version already installed."
        exit 0
    }
    Info "Upgrading" "$current -> $version"
}

# --- Download release ---
$tmpDir = Join-Path $env:TEMP "kf-install-$(Get-Random)"
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
$archive = Join-Path $tmpDir "kiloforge-skills.zip"

Info "Downloading" "$tag..."
$downloadUrl = "https://github.com/$Repo/archive/refs/tags/$tag.zip"
Invoke-WebRequest -Uri $downloadUrl -OutFile $archive -ErrorAction Stop

Info "Extracting" "..."
Expand-Archive -Path $archive -DestinationPath $tmpDir -Force
$srcDir = Get-ChildItem -Path $tmpDir -Directory -Filter "kiloforge-skills*" | Select-Object -First 1
if (-not $srcDir) { Fail "Could not find extracted source directory" }
$srcDir = $srcDir.FullName

# --- Install skills ---
Info "Installing" "skills to $SkillsDir..."
New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null

$skillCount = 0
Get-ChildItem -Path $srcDir -Directory -Filter "kf-*" | ForEach-Object {
    $skillDir = $_.FullName
    $skillName = $_.Name
    $skillMd = Join-Path $skillDir "SKILL.md"
    if (-not (Test-Path $skillMd)) { return }

    $target = Join-Path $SkillsDir $skillName
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-Item $skillMd -Destination $target -Force

    # Copy references/
    $refsDir = Join-Path $skillDir "references"
    if (Test-Path $refsDir) {
        $targetRefs = Join-Path $target "references"
        New-Item -ItemType Directory -Path $targetRefs -Force | Out-Null
        Copy-Item "$refsDir\*.md" -Destination $targetRefs -Force -ErrorAction SilentlyContinue
    }

    # Copy resources/
    $resDir = Join-Path $skillDir "resources"
    if (Test-Path $resDir) {
        $targetRes = Join-Path $target "resources"
        New-Item -ItemType Directory -Path $targetRes -Force | Out-Null
        Copy-Item "$resDir\*" -Destination $targetRes -Recurse -Force -ErrorAction SilentlyContinue
    }

    $script:skillCount++
}
Info "Skills" "$skillCount kf-* skills installed"

# --- Install CLI tools ---
$binDir = Join-Path $KfHome "bin"
$libDir = Join-Path $binDir "lib"
Info "Installing" "CLI tools to $binDir..."
New-Item -ItemType Directory -Path $libDir -Force | Out-Null

$scriptsDir = Join-Path $srcDir "kf-bin" "scripts"
Get-ChildItem -Path $scriptsDir -Filter "*.py" -File | ForEach-Object {
    Copy-Item $_.FullName -Destination $binDir -Force
}
Get-ChildItem -Path (Join-Path $scriptsDir "lib") -Filter "*.py" -File -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName -Destination $libDir -Force
}

# Ensure __init__.py
if (-not (Test-Path (Join-Path $libDir "__init__.py"))) {
    New-Item -ItemType File -Path (Join-Path $libDir "__init__.py") -Force | Out-Null
}

# --- Create venv ---
$venvDir = Join-Path $KfHome ".venv"
if (-not (Test-Path $venvDir)) {
    Info "Creating" "Python venv at $venvDir..."
    & $python -m venv $venvDir
}

# Install PyYAML
Info "Installing" "dependencies..."
$venvPip = Join-Path $venvDir "Scripts" "pip"
if (-not (Test-Path $venvPip)) { $venvPip = Join-Path $venvDir "bin" "pip" }
& $venvPip install -q pyyaml 2>$null

# --- Write version ---
$version | Out-File -FilePath $versionFile -Encoding utf8 -NoNewline

# --- Cleanup ---
Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

# --- Done ---
Write-Host ""
Write-Host "  ✓ Kiloforge Skills $version installed successfully" -ForegroundColor Green
Write-Host ""
Write-Host "  Skills:  $SkillsDir\kf-*"
Write-Host "  CLI:     $binDir"
Write-Host "  Venv:    $venvDir"
Write-Host ""
Write-Host "  Next steps:"
Write-Host "    1. Open Claude Code in your project: claude"
Write-Host "    2. Run /kf-setup to initialize Kiloforge"
Write-Host "    3. Run /kf-architect to create your first tracks"
Write-Host ""
Write-Host "  Update later with: /kf-update"
Write-Host ""
