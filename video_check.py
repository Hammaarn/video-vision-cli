#!/usr/bin/env python3
"""
video_check.py — token-efficient video pre-pass recommender for Claude Code

Sends Claude a video URL the smart way: pre-pass with audio-first
transcription + scene/silence detection, then drill specific frames
ONLY when visual proof is required. 30-50× cheaper than asking Claude
to "watch the whole video" naively.

Workflow:
    1. You run:  python3 video_check.py <url> [--mode MODE]
    2. Script downloads the video (cache-aware), reads metadata, and
       prints a structured recommendation (which MCP calls to issue,
       which to skip) plus a ready-to-paste prompt for Claude Code.
    3. You paste the prompt into your Claude Code session. Claude reads
       the recommendation and issues the right calls to the
       claude-video-vision MCP plugin (the one from
       https://github.com/jordanrendric/claude-video-vision).
    4. Token cost stays in the low thousands instead of the millions.

Modes:
    auto       Pre-pass + Claude decides which frames to drill   (default)
    auto-long  For videos >5min — synthesis routed via haiku subagent
    tutorial   Audio-only, no visual drill (lectures, explainers)
    recipe     Audio + 1 frame at the end (cooking, plating shots)
    minimal    Transcription only (cheapest; ~500-2K tokens)
    grift      Pre-pass + drill every scene change (claim-vs-evidence
               check; ads, sales pitches, "look at this" content)

Requirements (Mac):
    brew install yt-dlp ffmpeg python
    claude plugin marketplace add https://github.com/jordanrendric/claude-video-vision.git
    claude plugin install claude-video-vision
    (Claude Code installed; Max plan or API key configured)

See README.md for full setup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# -- Paths ---------------------------------------------------------
# All state lives under the user's home directory in a single hidden
# folder so the package is installable anywhere. Override via env vars
# if you have specific layout preferences.

HOME_BASE = Path(os.environ.get("VIDEO_VISION_HOME", str(Path.home() / ".video-vision-cli")))
DOWNLOAD_DIR = HOME_BASE / "downloads"
CACHE_DIR = HOME_BASE / "cache"


# -- URL / source classification ----------------------------------

def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def is_youtube(s: str) -> bool:
    return "youtube.com" in s or "youtu.be" in s


def is_instagram(s: str) -> bool:
    return "instagram.com" in s


def classify_source(s: str) -> str:
    if not is_url(s):
        return "local"
    if "youtube.com/shorts/" in s:
        return "youtube_short"
    if is_youtube(s):
        return "youtube"
    if "instagram.com/reel" in s or "instagram.com/p/" in s:
        return "instagram_reel"
    if "tiktok.com" in s:
        return "tiktok"
    return "url"


# -- Cache (per-URL JSON state) -----------------------------------

def url_hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def cache_key(input_arg: str) -> str:
    if is_url(input_arg):
        return url_hash(input_arg)
    p = Path(input_arg).resolve()
    return hashlib.sha256(str(p).encode()).hexdigest()[:16]


def cache_path(input_arg: str) -> Path:
    return CACHE_DIR / f"{cache_key(input_arg)}.json"


def cache_load(input_arg: str) -> dict | None:
    cp = cache_path(input_arg)
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def cache_store(input_arg: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = cache_path(input_arg)
    cp.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# -- yt-dlp / ffprobe wrappers ------------------------------------

def find_existing_download(video_id: str) -> Path | None:
    if not DOWNLOAD_DIR.exists():
        return None
    for f in DOWNLOAD_DIR.glob(f"video-{video_id}.*"):
        return f
    for f in DOWNLOAD_DIR.glob(f"*{video_id}*"):
        if f.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv"):
            return f
    return None


def download(url: str, cookies: Path | None = None) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-f", "best[ext=mp4]/best",
        "-o", str(DOWNLOAD_DIR / "video-%(id)s.%(ext)s"),
        "--no-warnings",
        "--quiet",
        "--print", "after_move:filepath",
    ]
    if cookies and cookies.exists():
        cmd += ["--cookies", str(cookies)]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        if is_instagram(url) and (not cookies or not cookies.exists()):
            msg += (
                "\n\n[hint] Instagram URL but no cookies passed.\n"
                "Public reels usually work anonymously; private/follower-only need cookies.\n"
                "Pass with --cookies <path-to-cookies.txt>."
            )
        raise RuntimeError(f"yt-dlp failed:\n{msg}")
    out = proc.stdout.strip().splitlines()
    if out:
        candidate = Path(out[-1].strip())
        if candidate.exists():
            return candidate
    raise RuntimeError("yt-dlp completed but no file path captured")


def eval_fps(rate: str) -> float:
    try:
        n, d = rate.split("/")
        d = float(d)
        return float(n) / d if d else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "ffprobe output not JSON"}
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video_s = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return {
        "duration_s": float(fmt.get("duration", 0) or 0),
        "size_bytes": int(fmt.get("size", 0) or 0),
        "codec": video_s.get("codec_name"),
        "width": video_s.get("width"),
        "height": video_s.get("height"),
        "fps": eval_fps(video_s.get("r_frame_rate", "0/1")),
        "has_audio": has_audio,
    }


def ensure_local(input_arg: str, cookies: Path | None) -> Path:
    if is_url(input_arg):
        m = re.search(r"(?:shorts/|v=|/reel/|/p/)([A-Za-z0-9_-]+)", input_arg)
        video_id = m.group(1) if m else None
        if video_id:
            existing = find_existing_download(video_id)
            if existing:
                return existing
        return download(input_arg, cookies=cookies)
    p = Path(input_arg).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Local path not found: {p}")
    return p


# -- Mode recipes (the smooth-token-minimization core) -------------

LONG_VIDEO_THRESHOLD_S = 300  # 5 minutes; above this, auto-long is recommended

MODE_RECIPES = {
    "auto": {
        "pre_pass_filters": {
            "scene_changes": True,
            "silence": True,
            "transcription": True,
        },
        "drill_policy": "claude-decides-from-pre-pass",
        "drill_default_mode": "descriptions",
        "drill_default_n": 4,
        "notes": "Read transcription + scene boundaries first. Drill only if visual proof is required.",
    },
    "tutorial": {
        "pre_pass_filters": {
            "transcription": True,
            "silence": True,
        },
        "drill_policy": "do-not-drill",
        "drill_default_mode": None,
        "drill_default_n": 0,
        "notes": "Audio is sufficient for tutorials, recipes, explainers. Verdict from transcription alone.",
    },
    "grift": {
        "pre_pass_filters": {
            "scene_changes": True,
            "silence": True,
            "transcription": True,
            "loudness": True,
        },
        "drill_policy": "drill-every-scene-change",
        "drill_default_mode": "images",
        "drill_default_n": 6,
        "notes": "Visual confirmation of claim-vs-evidence gap. Use images mode at scene boundaries; cross-reference audio claims against visual content.",
    },
    "recipe": {
        "pre_pass_filters": {
            "transcription": True,
        },
        "drill_policy": "last-frame-only",
        "drill_default_mode": "images",
        "drill_default_n": 1,
        "notes": "Audio gives steps; final frame shows plating/result.",
    },
    "minimal": {
        "pre_pass_filters": {
            "transcription": True,
        },
        "drill_policy": "do-not-drill",
        "drill_default_mode": None,
        "drill_default_n": 0,
        "notes": "Lowest token cost. Transcription only, ~500-2K tokens.",
    },
    "auto-long": {
        "pre_pass_filters": {
            "scene_changes": True,
            "silence": True,
            "transcription": True,
        },
        "drill_policy": "claude-decides-from-summary",
        "drill_default_mode": "descriptions",
        "drill_default_n": 7,
        "subagent_routing": True,
        "notes": (
            "For videos >5min with dense narration. video_analyze output "
            "likely exceeds context; the MCP auto-saves to a file. Dispatch "
            "a Haiku subagent (Task tool) to read that file in chunks and "
            "write a structured summary. Main context only sees the summary "
            "(~1-3K tokens) + drill frames."
        ),
    },
}


# -- Recommendation builder ---------------------------------------

def build_recommendation(local_path: Path, mode: str, cached: dict | None,
                         source_url: str | None = None) -> dict:
    recipe = MODE_RECIPES[mode]
    duration_s = (cached or {}).get("metadata", {}).get("duration_s", 0) or 0
    rec: dict = {
        "path": str(local_path),
        "mode": mode,
        "policy": recipe["drill_policy"],
        "notes": recipe["notes"],
        "cache_status": "hit" if cached and "analyze" in cached else "miss",
    }
    if mode == "auto" and duration_s > LONG_VIDEO_THRESHOLD_S:
        rec["long_video_hint"] = (
            f"Video is {duration_s/60:.1f} min (>{LONG_VIDEO_THRESHOLD_S/60:.0f} min). "
            f"Consider --mode auto-long to route synthesis through a Haiku subagent."
        )
    if rec["cache_status"] == "hit":
        rec["cached_analyze"] = cached["analyze"]
        rec["next_action"] = "use cached_analyze; skip video_analyze MCP call"
    else:
        rec["next_action"] = "call video_analyze MCP with filters below, then store via 'cache put'"
        rec["mcp_call"] = {
            "tool": "video_analyze",
            "params": {
                "path": str(local_path),
                "filters": recipe["pre_pass_filters"],
            },
        }
    if recipe.get("subagent_routing"):
        rec["subagent_routing"] = {
            "expected_behavior": (
                "video_analyze MCP will likely return 'output exceeds maximum allowed tokens' "
                "for long videos and auto-save the JSON to a file. Capture that path from the error."
            ),
            "subagent_params_hint": {
                "subagent_type": "general-purpose",
                "model": "haiku",
                "description": "Synthesize long-video transcription",
            },
            "dispatch_prompt": (
                f"Read the video transcription file in chunks of ~140 lines using Read with "
                f"offset/limit until covered. The file contains JSON with metadata, scene "
                f"boundaries, silence intervals, and timestamped transcription for a "
                f"{duration_s/60:.1f}-min video.\n\n"
                f"Produce a structured summary with these section headers:\n"
                f"- Topic and shape (1-2 sentences)\n"
                f"- Speakers (names + role; flag low-confidence attributions)\n"
                f"- Structural arc (5-8 bullets with timestamps)\n"
                f"- Key claims (5-10 bullets with timestamps)\n"
                f"- Drill candidates (5-8 timestamps worth visual frame check + WHY)\n"
                f"- Honest read (signal vs noise, attribution uncertainty)\n\n"
                f"Stay focused; the goal is 2-3K tokens of synthesis, not 25-75K of raw transcript. "
                f"Return only 'done, summary follows: <summary>'."
            ),
        }
    if recipe["drill_default_n"] > 0:
        rec["drill_template"] = {
            "tool": "video_detail",
            "params_after_pre_pass_decision": {
                "path": str(local_path),
                "frame_mode": recipe["drill_default_mode"],
                "view_sample": recipe["drill_default_n"],
                "segments": "<infer from pre_pass scene_changes>",
            },
        }
    else:
        rec["drill_template"] = None
    return rec


# -- Output ---------------------------------------------------------

def emit_text(payload: dict) -> str:
    lines = []
    lines.append(f"[video_check] mode={payload['mode']} cache={payload['cache_status']}")
    lines.append(f"path: {payload['path']}")
    if "metadata" in payload:
        m = payload["metadata"]
        if "error" not in m:
            lines.append(
                f"meta: {m.get('duration_s', 0):.1f}s {m.get('width')}x{m.get('height')} "
                f"{m.get('codec')} audio={m.get('has_audio')}"
            )
    lines.append(f"policy: {payload['policy']}")
    lines.append(f"notes: {payload['notes']}")
    if payload["cache_status"] == "hit":
        lines.append("\nCached analyze available — skip video_analyze MCP call.")
    else:
        c = payload["mcp_call"]
        lines.append(f"\nNext MCP call:\n  {c['tool']}({json.dumps(c['params'], indent=2)})")
    if payload.get("long_video_hint"):
        lines.append(f"\n[long-video hint] {payload['long_video_hint']}")
    if payload.get("subagent_routing"):
        s = payload["subagent_routing"]
        lines.append("\nSubagent routing (auto-long):")
        lines.append(f"  expected: {s['expected_behavior']}")
        lines.append(f"  dispatch params: {json.dumps(s['subagent_params_hint'], indent=2)}")
        lines.append(f"  dispatch prompt:\n---\n{s['dispatch_prompt']}\n---")
    if payload.get("drill_template"):
        d = payload["drill_template"]
        lines.append(f"\nDrill template (after pre-pass):\n  {d['tool']}({json.dumps(d['params_after_pre_pass_decision'], indent=2)})")
    return "\n".join(lines)


def emit_claude_prompt(payload: dict, source_url: str | None) -> str:
    """Generate a ready-to-paste prompt for Claude Code."""
    src = source_url if source_url else payload["path"]
    mode = payload["mode"]
    recipe = MODE_RECIPES[mode]
    lines = [
        f"Watch this video efficiently: {src}",
        "",
        f"Mode: {mode}",
        f"Local path: {payload['path']}",
    ]
    if payload["cache_status"] == "hit":
        lines.append("Cached analyze available — skip video_analyze MCP call.")
    else:
        lines.append(f"Pre-pass via video_analyze MCP with filters: {json.dumps(recipe['pre_pass_filters'])}")
    if payload.get("subagent_routing"):
        lines.append(
            "If video_analyze output exceeds context, the MCP auto-saves to a file. "
            "Dispatch a Haiku subagent (Task tool) to read it in chunks and write a "
            "structured summary; only the summary returns to main context."
        )
    if recipe["drill_default_n"] > 0:
        lines.append(
            f"After pre-pass, drill {recipe['drill_default_n']} frames at scene boundaries "
            f"using video_detail with frame_mode={recipe['drill_default_mode']}. "
            f"Drill policy: {recipe['drill_policy']}."
        )
    else:
        lines.append("No visual drill — audio synthesis is sufficient for this mode.")
    lines.append("")
    lines.append(f"Notes: {recipe['notes']}")
    return "\n".join(lines)


# -- Subcommands ---------------------------------------------------

def cmd_process(args: argparse.Namespace) -> int:
    cookies = Path(args.cookies).expanduser() if args.cookies else None
    try:
        local = ensure_local(args.input, cookies=cookies if cookies and cookies.exists() else None)
    except (RuntimeError, FileNotFoundError) as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    cached = cache_load(args.input) if not args.no_cache else None
    if not cached:
        meta = ffprobe(local)
        cached = {"input": args.input, "local": str(local), "metadata": meta}
        cache_store(args.input, cached)
    source_url = args.input if is_url(args.input) else None
    recommendation = build_recommendation(local, args.mode, cached, source_url=source_url)
    recommendation["metadata"] = cached.get("metadata", {})
    if args.json:
        print(json.dumps(recommendation, indent=2))
    elif args.prompt:
        print(emit_claude_prompt(recommendation, source_url))
    else:
        print(emit_text(recommendation))
        print()
        print("=" * 60)
        print("Ready-to-paste prompt for Claude Code:")
        print("=" * 60)
        print(emit_claude_prompt(recommendation, source_url))
    return 0


def cmd_cache_get(args: argparse.Namespace) -> int:
    cached = cache_load(args.input)
    if cached:
        print(json.dumps(cached, indent=2))
        return 0
    print(f"[miss] no cache entry for {args.input}", file=sys.stderr)
    return 1


def cmd_cache_put(args: argparse.Namespace) -> int:
    src = Path(args.json_file).expanduser()
    if not src.exists():
        print(f"[error] json file not found: {src}", file=sys.stderr)
        return 2
    try:
        new_data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[error] json file not parseable: {e}", file=sys.stderr)
        return 2
    cached = cache_load(args.input) or {"input": args.input}
    cached["analyze"] = new_data
    cache_store(args.input, cached)
    print(f"[stored] cache key={cache_key(args.input)} for {args.input}")
    return 0


def cmd_cache_clear(args: argparse.Namespace) -> int:
    if not CACHE_DIR.exists():
        print("(cache empty)")
        return 0
    n = 0
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()
        n += 1
    print(f"[cleared] {n} cache entries removed")
    return 0


# -- CLI entry -----------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="video_check.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd")

    proc = sub.add_parser("process", help="Process a URL or local file (default if no subcommand)")
    proc.add_argument("input", help="URL or local path")
    proc.add_argument("--mode", default="auto", choices=list(MODE_RECIPES.keys()))
    proc.add_argument("--cookies", default=None, help="Path to cookies.txt for private/Instagram URLs")
    proc.add_argument("--no-cache", action="store_true")
    proc.add_argument("--json", action="store_true", help="Emit raw JSON (for scripting)")
    proc.add_argument("--prompt", action="store_true", help="Emit only the Claude prompt (for piping)")
    proc.set_defaults(fn=cmd_process)

    cache = sub.add_parser("cache", help="Cache management")
    cache_sub = cache.add_subparsers(dest="cache_cmd")
    cg = cache_sub.add_parser("get", help="Print cached state for an input")
    cg.add_argument("input")
    cg.set_defaults(fn=cmd_cache_get)
    cp = cache_sub.add_parser("put", help="Store analyze result JSON for an input")
    cp.add_argument("input")
    cp.add_argument("json_file", help="Path to a JSON file with the video_analyze output")
    cp.set_defaults(fn=cmd_cache_put)
    cc = cache_sub.add_parser("clear", help="Remove all cache entries")
    cc.set_defaults(fn=cmd_cache_clear)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Default subcommand: "process". `python3 video_check.py <url>` works.
    if argv and not argv[0] in ("process", "cache", "-h", "--help"):
        argv = ["process"] + argv
    parser = build_parser()
    ns = parser.parse_args(argv)
    if not getattr(ns, "fn", None):
        parser.print_help()
        return 1
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
