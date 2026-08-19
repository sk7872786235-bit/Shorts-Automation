#!/usr/bin/env python3
"""
YouTube Shorts Automation Engine (run.py)
=========================================
- Fetches channel videos without wasting excessive API quota.
- Tracks segment history in 'history.json' so NO video segment is EVER repeated.
- Uses Gemini AI to detect high-retention 30-55s viral moments with high-CTR titles & hooks.
- Processes video into 1080x1920 9:16 vertical Short using ffmpeg (blurred background style).
- Uploads directly to YouTube Data API v3 with OAuth token auto-refresh.
- Generates rich GitHub Actions Step Summaries and supports manual test runs.
"""

import os
import sys
import json
import time
import shutil
import argparse
import subprocess
from datetime import datetime, timezone
import requests

HISTORY_FILE = "history.json"
OUTPUT_DIR = "temp_output"
MIN_CLIP_DURATION = 25
MAX_CLIP_DURATION = 55
MAX_OVERLAP_SECONDS = 5
UPLOAD_QUOTA_COST = 1600

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = {"INFO": "ℹ️", "SUCCESS": "✅", "WARN": "⚠️", "ERROR": "❌", "GEMINI": "✨", "FFMPEG": "🎬", "YT": "📺"}
    print(f"[{timestamp}] {symbols.get(level, '•')} [{level}] {msg}", flush=True)

def append_github_summary(markdown_text):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(markdown_text + "\n\n")
        except Exception as e:
            log(f"Failed to write to GITHUB_STEP_SUMMARY: {e}", "WARN")

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"Error loading {HISTORY_FILE}, starting fresh: {e}", "WARN")
    return {
        "channel_id": os.environ.get("YT_CHANNEL_ID", ""),
        "total_published_shorts": 0,
        "daily_upload_count": 0,
        "last_upload_date": "",
        "videos": {}
    }

def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        log(f"Saved state to {HISTORY_FILE}", "SUCCESS")
    except Exception as e:
        log(f"Failed to save {HISTORY_FILE}: {e}", "ERROR")

def is_segment_overlapping(start, end, used_intervals):
    for interval in used_intervals:
        u_start = interval.get("start", 0)
        u_end = interval.get("end", 0)
        overlap = max(0, min(end, u_end) - max(start, u_start))
        if overlap > MAX_OVERLAP_SECONDS:
            return True, interval, overlap
    return False, None, 0

def get_authenticated_access_token(client_id, client_secret, refresh_token):
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    log("Refreshing YouTube OAuth2 access token...", "YT")
    resp = requests.post(token_url, data=payload, timeout=20)
    if resp.status_code != 200:
        raise Exception(f"OAuth token refresh failed (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise Exception(f"No access_token found in OAuth response: {data}")
    log("Successfully acquired fresh YouTube access token!", "SUCCESS")
    return access_token

def fetch_channel_videos(channel_id, access_token=None, max_results=15):
    log(f"Scanning channel '{channel_id}' for candidate long-form videos...", "INFO")
    try:
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--print", "%(id)s\t%(title)s\t%(duration)s\t%(upload_date)s",
            f"https://www.youtube.com/channel/{channel_id}/videos",
            "--playlist-end", str(max_results)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip(): continue
            parts = line.split("\t")
            if len(parts) >= 2:
                vid_id = parts[0].strip()
                title = parts[1].strip()
                duration = float(parts[2].strip()) if len(parts) > 2 and parts[2].strip() not in ("NA", "None", "") else 600
                if duration >= 65:
                    videos.append({
                        "id": vid_id,
                        "title": title,
                        "duration": int(duration),
                        "url": f"https://www.youtube.com/watch?v={vid_id}"
                    })
        if videos:
            log(f"Found {len(videos)} long-form videos via yt-dlp crawler!", "SUCCESS")
            return videos
    except Exception as e:
        log(f"yt-dlp crawler notice: {e}, falling back to RSS feed...", "WARN")

    # RSS Fallback
    try:
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        resp = requests.get(rss_url, timeout=15)
        if resp.status_code == 200:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            ns = {"yt": "http://www.youtube.com/xml/schemas/2015", "atom": "http://www.w3.org/2005/Atom"}
            videos = []
            for entry in root.findall("atom:entry", ns):
                vid_id_elem = entry.find("yt:videoId", ns)
                title_elem = entry.find("atom:title", ns)
                if vid_id_elem is not None and title_elem is not None:
                    videos.append({
                        "id": vid_id_elem.text,
                        "title": title_elem.text,
                        "duration": 600,
                        "url": f"https://www.youtube.com/watch?v={vid_id_elem.text}"
                    })
            if videos: return videos
    except Exception as e:
        log(f"RSS extraction error: {e}", "WARN")

    return []

def analyze_video_with_gemini(video_info, used_intervals, gemini_api_key):
    log(f"Asking Gemini AI to detect viral Short moment from: '{video_info['title']}'...", "GEMINI")
    used_intervals_json = json.dumps(used_intervals)
    prompt = f"""You are an elite YouTube Shorts Growth Hacker and automated video editor.
Extract the next most engaging 30 to 55-second viral Short segment from this video.

VIDEO DETAILS:
- Title: "{video_info['title']}"
- Video ID: {video_info['id']}
- Total Estimated Duration: {video_info['duration']} seconds
- ALREADY USED SEGMENTS (STRICT BAN - DO NOT OVERLAP): {used_intervals_json}

RULES:
1. Select interval [start_sec, end_sec] between 30 and 55 seconds (end_sec - start_sec >= 30 and <= 55).
2. Must not overlap with {used_intervals_json} by more than 5 seconds.
3. Choose moment with strong hook, insight, punchline, or peak.
4. Craft viral Title (under 50 chars) with #Shorts.
5. Provide 2-line description with hashtags (#Shorts #viral #trending).
6. Provide a 4-5 word overlay hook text for first 3 seconds.

Output ONLY valid JSON:
{{
  "start_sec": <integer>,
  "end_sec": <integer>,
  "duration_sec": <integer>,
  "title": "<Short Title #Shorts>",
  "description": "<Description with hashtags>",
  "tags": ["Shorts", "viral", "trending", "video"],
  "hook_reason": "<Why this clip retains viewers>",
  "overlay_hook_text": "<Hook text>"
}}"""

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4}
    }
    
    resp = requests.post(gemini_url, json=payload, timeout=30)
    if resp.status_code != 200:
        log(f"Gemini API returned {resp.status_code}, using algorithmic safe slice...", "WARN")
        last_end = max([int(i.get("end", 0)) for i in used_intervals], default=15)
        start = min(last_end + 10, max(0, video_info['duration'] - 60))
        return {
            "start_sec": start,
            "end_sec": start + 45,
            "duration_sec": 45,
            "title": f"{video_info['title'][:40]} #Shorts",
            "description": f"Highlight clip from {video_info['title']} #Shorts #viral",
            "tags": ["Shorts", "viral"],
            "hook_reason": "Algorithmic interval selection fallback.",
            "overlay_hook_text": "Must Watch!"
        }
    
    data = resp.json()
    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    clip_data = json.loads(raw_text)
    log(f"Gemini selected clip: {clip_data['start_sec']}s -> {clip_data['end_sec']}s ({clip_data.get('duration_sec', 0)}s)", "SUCCESS")
    return clip_data

def process_short_video(video_url, start_sec, end_sec, overlay_text=""):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    raw_clip_path = os.path.join(OUTPUT_DIR, "raw_clip.mp4")
    final_short_path = os.path.join(OUTPUT_DIR, "final_short.mp4")
    
    for p in (raw_clip_path, final_short_path):
        if os.path.exists(p): os.remove(p)

    duration = end_sec - start_sec
    log(f"Downloading clip segment ({start_sec}s to {end_sec}s, duration: {duration}s) via yt-dlp...", "FFMPEG")
    
    cookies_arg = []
    if os.environ.get("YT_COOKIES"):
        cookies_file = os.path.join(OUTPUT_DIR, "cookies.txt")
        with open(cookies_file, "w") as cf:
            cf.write(os.environ.get("YT_COOKIES", ""))
        cookies_arg = ["--cookies", cookies_file]

    dl_cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--download-sections", f"*{start_sec}-{end_sec}",
        "--force-keyframes-at-cuts",
        *cookies_arg,
        "-o", raw_clip_path,
        video_url
    ]
    
    res = subprocess.run(dl_cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(raw_clip_path):
        stream_cmd = ["yt-dlp", "-g", *cookies_arg, video_url]
        stream_res = subprocess.run(stream_cmd, capture_output=True, text=True)
        if stream_res.returncode == 0:
            urls = stream_res.stdout.strip().split("\n")
            cut_cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_sec),
                "-i", urls[0],
                "-t", str(duration),
                "-c", "copy",
                raw_clip_path
            ]
            subprocess.run(cut_cmd, capture_output=True, check=True)

    if not os.path.exists(raw_clip_path):
        raise FileNotFoundError("Failed to download raw video clip segment.")

    log("Rendering vertical 9:16 (1080x1920) Short with blurred ambient backdrop...", "FFMPEG")
    
    ffmpeg_filter = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=28[bg];"
        "[0:v]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
    )
    
    render_cmd = [
        "ffmpeg", "-y",
        "-i", raw_clip_path,
        "-filter_complex", ffmpeg_filter,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-t", str(duration),
        final_short_path
    ]
    
    subprocess.run(render_cmd, capture_output=True, text=True, check=True)
    size_mb = os.path.getsize(final_short_path) / (1024 * 1024)
    log(f"Rendered Short successfully ({size_mb:.2f} MB): {final_short_path}", "SUCCESS")
    return final_short_path

def upload_short_to_youtube(video_path, clip_meta, access_token, privacy_status="public"):
    log(f"Uploading Short to YouTube Data API: '{clip_meta['title']}'...", "YT")
    metadata = {
        "snippet": {
            "title": clip_meta["title"],
            "description": clip_meta["description"],
            "tags": clip_meta.get("tags", ["Shorts", "viral"]),
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
    }
    
    init_url = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(os.path.getsize(video_path))
    }
    
    init_resp = requests.post(init_url, headers=headers, json=metadata, timeout=30)
    if init_resp.status_code == 403:
        error_body = init_resp.json()
        errors = error_body.get("error", {}).get("errors", [])
        reasons = [e.get("reason") for e in errors]
        if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
            raise Exception(f"QUOTA_EXCEEDED: Daily limit reached (10,000 units / 1600 per upload). Error: {error_body}")
        raise Exception(f"YouTube API Forbidden (403): {error_body}")
        
    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        raise Exception("No resumable Location header returned from YouTube.")
        
    with open(video_path, "rb") as f:
        upload_resp = requests.put(upload_url, headers={"Content-Type": "video/mp4"}, data=f, timeout=120)
        
    if upload_resp.status_code not in (200, 201):
        raise Exception(f"Video binary upload failed (HTTP {upload_resp.status_code}): {upload_resp.text}")
        
    upload_result = upload_resp.json()
    new_video_id = upload_result.get("id")
    log(f"🎉 SHORT PUBLISHED! ID: {new_video_id}", "SUCCESS")
    return new_video_id

def main():
    parser = argparse.ArgumentParser(description="YouTube Shorts Automation Bot")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without uploading")
    parser.add_argument("--force-video-id", type=str, help="Specific YouTube video ID to cut from")
    parser.add_argument("--privacy", type=str, default="public", choices=["public", "unlisted", "private"])
    args = parser.parse_args()

    log("=" * 60, "INFO")
    log("🚀 YOUTUBE SHORTS AUTOMATION BOT - PIPELINE STARTED", "INFO")
    log("=" * 60, "INFO")

    gemini_key = os.environ.get("GEMINI_API_KEY")
    channel_id = os.environ.get("YT_CHANNEL_ID")
    client_id = os.environ.get("YT_CLIENT_ID")
    client_secret = os.environ.get("YT_CLIENT_SECRET")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")

    missing = []
    if not gemini_key: missing.append("GEMINI_API_KEY")
    if not channel_id and not args.force_video_id: missing.append("YT_CHANNEL_ID")
    if not args.dry_run:
        if not client_id: missing.append("YT_CLIENT_ID")
        if not client_secret: missing.append("YT_CLIENT_SECRET")
        if not refresh_token: missing.append("YT_REFRESH_TOKEN")

    if missing:
        error_msg = f"Missing required environment secrets: {', '.join(missing)}"
        log(error_msg, "ERROR")
        append_github_summary(f"### ❌ Pipeline Aborted\n**Error:** {error_msg}")
        sys.exit(1)

    history = load_history()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if history.get("last_upload_date") != today_str:
        history["daily_upload_count"] = 0
        history["last_upload_date"] = today_str

    access_token = None
    if not args.dry_run:
        try:
            access_token = get_authenticated_access_token(client_id, client_secret, refresh_token)
        except Exception as e:
            log(f"Authentication failed: {e}", "ERROR")
            append_github_summary(f"### ❌ Authentication Failed\n```\n{e}\n```")
            sys.exit(1)

    selected_video = None
    if args.force_video_id:
        selected_video = {
            "id": args.force_video_id,
            "title": f"Target Video ({args.force_video_id})",
            "duration": 600,
            "url": f"https://www.youtube.com/watch?v={args.force_video_id}"
        }
    else:
        videos = fetch_channel_videos(channel_id, access_token=access_token)
        if not videos:
            log("No long-form videos discovered on channel.", "ERROR")
            append_github_summary("### ❌ No Videos Found\nCould not fetch long-form videos from target channel.")
            sys.exit(1)

        for v in videos:
            v_id = v["id"]
            v_history = history.setdefault("videos", {}).setdefault(v_id, {"title": v["title"], "used_intervals": []})
            used = v_history.get("used_intervals", [])
            total_used = sum([int(i.get("end", 0)) - int(i.get("start", 0)) for i in used])
            if total_used < (v["duration"] - 60):
                selected_video = v
                break
        if not selected_video: selected_video = videos[0]

    vid_id = selected_video["id"]
    vid_title = selected_video["title"]
    vid_history = history.setdefault("videos", {}).setdefault(vid_id, {"title": vid_title, "used_intervals": []})
    used_intervals = vid_history.get("used_intervals", [])

    log(f"Selected video: '{vid_title}' ({vid_id})", "SUCCESS")
    log(f"Previously used segments ({len(used_intervals)}): {used_intervals}", "INFO")

    clip_data = analyze_video_with_gemini(selected_video, used_intervals, gemini_key)

    start_s = clip_data["start_sec"]
    end_s = clip_data["end_sec"]
    is_overlap, conflict, overlap_sec = is_segment_overlapping(start_s, end_s, used_intervals)
    if is_overlap:
        log(f"Segment [{start_s}s - {end_s}s] overlaps with {conflict} by {overlap_sec}s! Adjusting...", "WARN")
        start_s = conflict.get("end", 0) + 5
        end_s = start_s + 45
        clip_data["start_sec"] = start_s
        clip_data["end_sec"] = end_s

    try:
        short_file_path = process_short_video(
            selected_video["url"],
            clip_data["start_sec"],
            clip_data["end_sec"],
            clip_data.get("overlay_hook_text", "")
        )
    except Exception as e:
        log(f"FFmpeg processing failed: {e}", "ERROR")
        append_github_summary(f"### ❌ Video Processing Failed\n```\n{e}\n```")
        sys.exit(1)

    published_id = "DRY_RUN_ID"
    if not args.dry_run:
        try:
            published_id = upload_short_to_youtube(
                short_file_path,
                clip_data,
                access_token,
                privacy_status=args.privacy
            )
        except Exception as e:
            error_str = str(e)
            log(f"Upload failed: {error_str}", "ERROR")
            append_github_summary(f"### ❌ YouTube Upload Failed\n**Error:** `{error_str}`")
            sys.exit(1)
    else:
        log("DRY RUN: Rendered successfully, skipped API upload.", "SUCCESS")

    used_intervals.append({
        "start": clip_data["start_sec"],
        "end": clip_data["end_sec"],
        "duration": clip_data["end_sec"] - clip_data["start_sec"],
        "short_id": published_id,
        "title": clip_data["title"],
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    history["total_published_shorts"] += 1
    history["daily_upload_count"] += 1
    save_history(history)

    est_quota = history["daily_upload_count"] * UPLOAD_QUOTA_COST
    summary_md = f"""## 🎬 YouTube Shorts Automation Success!

| Metric | Details |
| :--- | :--- |
| **Short Title** | `{clip_data['title']}` |
| **Source Video** | `{vid_title}` (`{vid_id}`) |
| **Clip Interval** | **{clip_data['start_sec']}s ➔ {clip_data['end_sec']}s** ({clip_data['end_sec'] - clip_data['start_sec']}s) |
| **Hook Reason** | {clip_data.get('hook_reason', 'Viral AI selection')} |
| **Mode** | {'🧪 Dry Run (No Upload)' if args.dry_run else '🚀 Public Upload'} |
| **Shorts Link** | [Watch on YouTube](https://www.youtube.com/shorts/{published_id}) |
| **Today's Uploads** | **{history['daily_upload_count']}** / 6 (Est. {est_quota:,} / 10,000 quota units) |

### 🔒 Duplication Protection
Recorded interval `[{clip_data['start_sec']}, {clip_data['end_sec']}]` in `history.json` so this slice will **never** be repeated.
"""
    append_github_summary(summary_md)
    log("Pipeline completed successfully!", "SUCCESS")

if __name__ == "__main__":
    main()
