#!/usr/bin/env bash
# install-mac.sh — one-shot installer for video-vision-cli on macOS.
# Idempotent: safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }

echo
bold "video-vision-cli installer (macOS)"
echo

# --- 1. Sanity ---
if [[ "$(uname)" != "Darwin" ]]; then
  red "This installer targets macOS. For Linux/Windows, see the manual install in README.md."
  exit 1
fi

# --- 2. Homebrew ---
if ! command -v brew >/dev/null 2>&1; then
  red "Homebrew not found. Install it first:"
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
fi
green "✓ Homebrew present"

# --- 3. Python 3.10+ ---
if ! command -v python3 >/dev/null 2>&1; then
  yellow "Python3 not found, installing via Homebrew..."
  brew install python
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
green "✓ Python ${PY_VER} present"

# --- 4. yt-dlp + ffmpeg ---
for pkg in yt-dlp ffmpeg; do
  if ! command -v "$pkg" >/dev/null 2>&1; then
    yellow "Installing $pkg..."
    brew install "$pkg"
  else
    green "✓ $pkg already installed ($(command -v "$pkg"))"
  fi
done

# --- 5. Claude Code ---
if ! command -v claude >/dev/null 2>&1; then
  red "Claude Code CLI not found on PATH."
  echo "Install it first: https://claude.com/code"
  echo "Then re-run this script."
  exit 1
fi
green "✓ Claude Code CLI present ($(claude --version 2>/dev/null | head -n1))"

# --- 6. claude-video-vision plugin ---
if claude plugin list 2>/dev/null | grep -q "claude-video-vision"; then
  green "✓ claude-video-vision plugin already installed"
else
  yellow "Installing claude-video-vision plugin..."
  if ! claude plugin marketplace add https://github.com/jordanrendric/claude-video-vision.git 2>&1; then
    yellow "  (marketplace may already be added; continuing)"
  fi
  claude plugin install claude-video-vision
  green "✓ claude-video-vision plugin installed"
  echo
  yellow "Recommended: run the plugin's setup wizard inside Claude Code:"
  echo "  claude"
  echo "  > /claude-video-vision:setup-video-vision"
fi

# --- 7. Verify the script runs ---
echo
bold "Verifying video_check.py..."
if python3 "${SCRIPT_DIR}/video_check.py" --help >/dev/null 2>&1; then
  green "✓ video_check.py runs"
else
  red "✗ video_check.py failed to run"
  python3 "${SCRIPT_DIR}/video_check.py" --help
  exit 1
fi

# --- 8. Optional: shell alias ---
echo
bold "Done."
echo
echo "Try it:"
echo "  python3 ${SCRIPT_DIR}/video_check.py \"https://www.youtube.com/watch?v=<some-id>\""
echo
echo "Or add a shell alias for convenience (zsh):"
echo "  echo 'alias vidcheck=\"python3 ${SCRIPT_DIR}/video_check.py\"' >> ~/.zshrc"
echo "  source ~/.zshrc"
echo "  vidcheck \"<url>\""
