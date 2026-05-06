# install-windows.ps1 - one-shot installer for video-vision-cli on Windows.
# Idempotent: safe to re-run. Handles winget, scoop, or chocolatey as the
# package manager; falls back to clear manual instructions if none present.
#
# Run from PowerShell:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser   (one-time)
#   .\install-windows.ps1

$ErrorActionPreference = "Stop"

function Write-Bold([string]$msg)   { Write-Host $msg -ForegroundColor White }
function Write-Green([string]$msg)  { Write-Host $msg -ForegroundColor Green }
function Write-Yellow([string]$msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Red([string]$msg)    { Write-Host $msg -ForegroundColor Red }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host
Write-Bold "video-vision-cli installer (Windows)"
Write-Host

# --- 1. Detect package manager -----------------------------------
$pm = $null
if (Get-Command winget -ErrorAction SilentlyContinue) { $pm = "winget" }
elseif (Get-Command scoop  -ErrorAction SilentlyContinue) { $pm = "scoop"  }
elseif (Get-Command choco  -ErrorAction SilentlyContinue) { $pm = "choco"  }

if (-not $pm) {
  Write-Red "No supported package manager found (winget / scoop / chocolatey)."
  Write-Host
  Write-Host "winget ships with Windows 10 1809+ and Windows 11. If you don't have it:"
  Write-Host "  1. Open Microsoft Store, search 'App Installer', install/update."
  Write-Host "  2. OR install Scoop: https://scoop.sh"
  Write-Host "  3. OR install Chocolatey: https://chocolatey.org/install"
  Write-Host
  Write-Host "Then re-run this script."
  exit 1
}
Write-Green "OK Package manager: $pm"

# --- 2. Python 3.10+ ---------------------------------------------
$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) {
  Write-Yellow "Python not found, installing..."
  switch ($pm) {
    "winget" { winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements }
    "scoop"  { scoop install python }
    "choco"  { choco install python -y }
  }
  Write-Yellow "  Re-open this PowerShell window to refresh PATH, then re-run install-windows.ps1."
  exit 0
}
$pyVer = & $python.Source -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")'
Write-Green "OK Python $pyVer present ($($python.Source))"

# --- 3. yt-dlp + ffmpeg ------------------------------------------
function Install-Tool([string]$tool, [hashtable]$ids) {
  if (Get-Command $tool -ErrorAction SilentlyContinue) {
    Write-Green "OK $tool already installed"
    return
  }
  Write-Yellow "Installing $tool via $pm..."
  switch ($pm) {
    "winget" { winget install --id $ids.winget --silent --accept-source-agreements --accept-package-agreements }
    "scoop"  { scoop install $ids.scoop }
    "choco"  { choco install $ids.choco -y }
  }
}

Install-Tool "yt-dlp" @{ winget = "yt-dlp.yt-dlp"; scoop = "yt-dlp"; choco = "yt-dlp" }
Install-Tool "ffmpeg" @{ winget = "Gyan.FFmpeg";   scoop = "ffmpeg"; choco = "ffmpeg" }

# Re-check PATH after installs (winget may not refresh current session)
$missing = @()
foreach ($t in @("yt-dlp", "ffmpeg", "ffprobe")) {
  if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { $missing += $t }
}
if ($missing.Count -gt 0) {
  Write-Yellow "These tools were installed but aren't on PATH yet in this session: $($missing -join ', ')"
  Write-Yellow "Open a fresh PowerShell window and re-run install-windows.ps1 to verify."
  Write-Host
}

# --- 4. Claude Code CLI ------------------------------------------
$claude = Get-Command claude.cmd -ErrorAction SilentlyContinue
if (-not $claude) { $claude = Get-Command claude -ErrorAction SilentlyContinue }
if (-not $claude) {
  Write-Red "Claude Code CLI not found on PATH."
  Write-Host "Install it: https://claude.com/code"
  Write-Host "Then re-run this script."
  exit 1
}
$claudeVer = ""
try { $claudeVer = (& $claude.Source --version 2>$null | Select-Object -First 1) } catch { }
Write-Green "OK Claude Code CLI present ($($claude.Source))$(if ($claudeVer) { ' - ' + $claudeVer })"

# --- 5. claude-video-vision plugin -------------------------------
$pluginList = ""
try { $pluginList = & $claude.Source plugin list 2>$null | Out-String } catch { }
if ($pluginList -match "claude-video-vision") {
  Write-Green "OK claude-video-vision plugin already installed"
} else {
  Write-Yellow "Installing claude-video-vision plugin..."
  try {
    & $claude.Source plugin marketplace add https://github.com/jordanrendric/claude-video-vision.git 2>&1 | Out-Null
  } catch {
    Write-Yellow "  (marketplace may already be added; continuing)"
  }
  & $claude.Source plugin install claude-video-vision
  Write-Green "OK claude-video-vision plugin installed"
  Write-Host
  Write-Yellow "Recommended: run the plugin's setup wizard inside Claude Code:"
  Write-Host "  claude"
  Write-Host "  > /claude-video-vision:setup-video-vision"
}

# --- 6. Verify the script ----------------------------------------
Write-Host
Write-Bold "Verifying video_check.py..."
$scriptPath = Join-Path $ScriptDir "video_check.py"
try {
  & $python.Source $scriptPath --help | Out-Null
  Write-Green "OK video_check.py runs"
} catch {
  Write-Red "X video_check.py failed to run"
  & $python.Source $scriptPath --help
  exit 1
}

# --- 7. Done -----------------------------------------------------
Write-Host
Write-Bold "Done."
Write-Host
Write-Host "Try it:"
Write-Host "  python `"$scriptPath`" `"https://www.youtube.com/watch?v=<some-id>`""
Write-Host
Write-Host "Or add a PowerShell function for convenience. Add to `$PROFILE`:"
Write-Host "  function vidcheck { python `"$scriptPath`" `$args }"
Write-Host "Then in any PowerShell:"
Write-Host "  vidcheck `"<url>`""
