# video-vision-cli

Token-efficient video pre-pass for Claude Code. Send Claude a video URL the smart way: audio-first transcription + scene/silence detection, then drill specific frames *only* when visual proof is required. **30-50× cheaper than asking Claude to "watch the whole video" naively.**

Designed for Claude Max plan users on Mac who want to point Claude at YouTube / Instagram / TikTok / local video files and get a clean structured analysis without burning tokens.

---

## How it works

```
You:    python3 video_check.py <url> --mode auto
        ↓
Script: download via yt-dlp → ffprobe metadata → cache state →
        prints recommendation + a ready-to-paste prompt
        ↓
You:    paste the prompt into Claude Code chat
        ↓
Claude: reads recommendation → calls video_analyze MCP with smart filters →
        drills frames only at scene boundaries that earned a visual check →
        produces structured analysis
```

The script is the **recommender**. The actual video understanding is done by the [`claude-video-vision`](https://github.com/jordanrendric/claude-video-vision) plugin running inside your Claude Code session. The recommender's job is to keep token cost low by telling Claude what to skip.

### Modes

| Mode | When | Cost (approx) |
|------|------|--------------|
| `auto` | Default. Pre-pass + Claude decides which frames to drill | 2-8K tokens |
| `auto-long` | For videos >5min — synthesis routed via Haiku subagent | 5-15K tokens (vs 50K+ naive) |
| `tutorial` | Lectures, explainers, recipes — audio is enough | 1-3K tokens |
| `recipe` | Cooking videos — audio + 1 frame at the end | 1-3K tokens |
| `minimal` | Cheapest — transcription only | 500-2K tokens |
| `grift` | Ads, sales pitches — drill every scene change for claim-vs-evidence | 8-20K tokens |

---

## Install

### Prerequisites (both platforms)

- **Claude Code CLI** with a Max plan (or API key). If you don't have it: https://claude.com/code
- **Python 3.10+** — pre-installed on macOS 12+ and most Linuxes; on Windows the installer below installs it via your package manager.

### Mac — one-shot

```bash
chmod +x install-mac.sh
./install-mac.sh
```

Installs `yt-dlp` + `ffmpeg` via Homebrew, the `claude-video-vision` plugin via Claude Code, and verifies the script runs. Re-runnable; idempotent.

If you don't have Homebrew: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`

### Windows — one-shot

**Easiest:** double-click `install-windows.bat`. A PowerShell window opens, the installer runs, you press a key when done.

**Manual (PowerShell):**

```powershell
# One-time, if you haven't run PowerShell scripts before:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Then:
.\install-windows.ps1
```

The Windows installer auto-detects your package manager (winget / scoop / chocolatey) and installs `yt-dlp` + `ffmpeg` through it. winget ships with Windows 10 1809+ and Windows 11 — most users have it already. If you don't, the installer points you at one of the alternatives.

### Manual install (any platform)

```bash
# 1. Tools (use your platform's package manager)
#    Mac:     brew install yt-dlp ffmpeg
#    Windows: winget install yt-dlp.yt-dlp Gyan.FFmpeg
#    Linux:   apt/dnf/pacman install yt-dlp ffmpeg

# 2. Plugin (token-efficient video analysis)
claude plugin marketplace add https://github.com/jordanrendric/claude-video-vision.git
claude plugin install claude-video-vision

# 3. (Optional) verify the plugin set itself up correctly — inside Claude Code chat:
#    /claude-video-vision:setup-video-vision

# 4. Verify the script
python3 video_check.py --help     # Mac/Linux
python  video_check.py --help     # Windows (Python launcher)
```

---

## Usage

### Single video, default mode

```bash
python3 video_check.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Output ends with a `==== Ready-to-paste prompt for Claude Code ====` block. Copy that, paste it into your Claude Code session, and Claude follows the smart-filter recommendation.

### Long video → use `auto-long`

```bash
python3 video_check.py "https://www.youtube.com/watch?v=<long-video>" --mode auto-long
```

This tells Claude to dispatch a Haiku subagent to chunk-read the long transcript and return only a structured summary to your main context (~2-3K tokens) — instead of pulling 50K+ tokens of raw transcription.

### Tutorial / recipe (audio is enough)

```bash
python3 video_check.py "<url>" --mode tutorial
python3 video_check.py "<url>" --mode recipe
```

### Local file

```bash
python3 video_check.py ~/Movies/clip.mp4 --mode auto
```

### Just give me the prompt (for piping)

```bash
python3 video_check.py "<url>" --prompt | pbcopy
# now paste into Claude Code
```

### Cache management

The script caches per-URL state (download path, ffprobe metadata, optional analyze result) under `~/.video-vision-cli/`. You can inspect or clear it:

```bash
python3 video_check.py cache get "<url>"      # show cached state
python3 video_check.py cache clear            # nuke all caches
```

---

## Tradeoffs

**What this gives you:** dramatic token savings on long videos, since most of a video's information lives in the audio (transcribable cheaply) and only specific frames need visual analysis. The script tells Claude *which* frames to look at, instead of letting Claude default to "watch every keyframe."

**What it doesn't give you:** real-time analysis. There's a download step (~5-30s for typical YouTube videos), and the audio pre-pass takes another ~10-60s depending on length. For one-off analysis this is negligible; for batch you'll feel it.

**Privacy note:** Videos download to `~/.video-vision-cli/downloads/` on your machine. They stay local. Nothing leaves your laptop except what Claude itself sends to Anthropic when you paste the prompt.

**Cost note:** With a Max plan, video_analyze MCP calls consume your Sonnet quota (well-budgeted for typical use). If you're on pay-per-use API, a long video can run $0.20-1.00 in Anthropic charges depending on mode + length.

---

## Troubleshooting

**`yt-dlp: command not found`** — install missed; rerun `brew install yt-dlp` or `./install-mac.sh`.

**`ffprobe: command not found`** — same; `brew install ffmpeg` (ffprobe ships with ffmpeg).

**`claude plugin install` fails** — make sure your Claude Code is up to date: `claude update`. If you don't have the plugin marketplace command, your Claude Code is too old.

**`Instagram URL but no cookies`** — public reels usually work without cookies. Private/follower-only ones need a `cookies.txt` exported from your logged-in browser. Pass it: `--cookies ~/path/to/cookies.txt`.

**`yt-dlp failed`** — YouTube sometimes hardens against yt-dlp; update with `brew upgrade yt-dlp`.

**The MCP plugin works but my Claude says it doesn't have a `video_analyze` tool** — restart your Claude Code session so the plugin's tool list reloads. If still broken, run the plugin's setup wizard: `/claude-video-vision:setup-video-vision`.

---

## Source / credits

- This script is a stripped, brother-friendly fork of [`mcp-skills-server/video_to_claude.py`](https://github.com/) — Erik Hammarn's internal token-efficiency wrapper for his Claude Code workflow. Erik's original retains panel-eval dispatching + Obsidian vault writes; this version drops both.
- The actual video understanding lives in [`claude-video-vision`](https://github.com/jordanrendric/claude-video-vision) by Jordan Rendric. Without that plugin, the script is just a yt-dlp + ffprobe wrapper. Install it; that's where the magic is.
- License: same as the upstream repo.
