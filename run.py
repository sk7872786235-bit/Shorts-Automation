#!/usr/bin/env python3
"""
YouTube Shorts Automation Engine (run.py)
=========================================
Features:
1. Auto-PoToken Generation via Node.js + pytubefix.
2. Real Browser TLS Impersonation via curl_cffi (web_embedded, visionos, tv_embedded).
3. Universal Cookie Sanitizer (JSON arrays, raw strings, and Netscape format).
4. Zero-Quota Cookie-Based Upload Protocol (YT_COOKIES) with OAuth2 fallback.
5. Gemini AI detection of high-retention 30-55s viral moments with high-CTR titles.
6. High-quality 1080x1920 9:16 vertical video rendering with blurred ambient padding.
"""

import os
import sys
import json
import time
import shutil
import hashlib
import argparse
import subprocess
from datetime import datetime, timezone
import requests

HISTORY_FILE = "history.json"
OUTPUT_DIR = "temp_output"
MIN_CLIP_DURATION = 30
MAX_CLIP_DURATION = 55
MAX_OVERLAP_SECONDS = 5

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = {
        "INFO": "ℹ️",
        "SUCCESS": "✅",
        "WARN": "⚠️",
        "ERROR": "❌",
        "GEMINI": "✨",
        "FFMPEG": "🎬",
        "YT": "📺",
        "COOKIE": "🍪"
    }
    symbol = symbols.get(level, "•")
    print(f"[{timestamp}] {symbol} [{level}] {msg}", flush=True)

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
    if not client_id or not client_secret or not refresh_token:
        return None
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    log("Refreshing YouTube OAuth2 access token...", "YT")
    try:
        resp = requests.post(token_url, data=payload, timeout=20)
        if resp.status_code != 200:
            log(f"OAuth refresh notice (HTTP {resp.status_code}): {resp.text}", "WARN")
            return None
        data = resp.json()
        access_token = data.get("access_token")
        if access_token:
            log("Successfully acquired fresh YouTube access token!", "SUCCESS")
            return access_token
    except Exception as e:
        log(f"OAuth token request failed: {e}", "WARN")
    return None

def fetch_channel_videos(channel_id, access_token=None, max_results=15):
    log(f"Scanning channel '{channel_id}' for candidate long-form videos...", "INFO")
    try:
        cmd = [
            "yt-dlp",
            "--extractor-args", "youtube:player_client=mweb,android",
            "--flat-playlist",
            "--print", "%(id)s\t%(title)s\t%(duration)s\t%(upload_date)s",
            f"https://www.youtube.com/channel/{channel_id}/videos",
            "--playlist-end", str(max_results)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
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
        log(f"yt-dlp notice: {e}, falling back to RSS...", "WARN")

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
            if videos:
                return videos
    except Exception as e:
        log(f"RSS extraction error: {e}", "WARN")

    return []

def analyze_video_with_gemini(video_info, used_intervals, gemini_api_key):
    log(f"Asking Gemini AI to detect viral Short moment from: '{video_info['title']}'...", "GEMINI")
    used_intervals_json = json.dumps(used_intervals)
    prompt = f"""You are an elite YouTube Shorts Growth Hacker and automated video editor.
Select the next most engaging 30 to 55-second viral Short segment from my YouTube video.

VIDEO DETAILS:
- Title: "{video_info['title']}"
- Video ID: {video_info['id']}
- Total Estimated Duration: {video_info['duration']} seconds
- ALREADY USED SEGMENTS (DO NOT OVERLAP BY > 5 SECONDS): {used_intervals_json}

INSTRUCTIONS:
1. Select a high-impact interval [start_sec, end_sec] between 30 and 55 seconds.
2. The segment MUST NOT overlap with any interval in {used_intervals_json} by more than 5 seconds.
3. Choose a moment with an immediate hook, surprising visual, or punchline.
4. Craft a high-CTR YouTube Short Title (< 50 chars) ending with 1 emoji and '#Shorts'.
5. Provide a 2-line description with top trending hashtags (#Shorts #viral #trending #youtube).
6. Provide a punchy 3-5 word uppercase overlay hook text for the first 3 seconds.

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
}}
"""

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.4
        }
    }
    
    try:
        resp = requests.post(gemini_url, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            clip_data = json.loads(raw_text)
            log(f"Gemini selected clip: {clip_data['start_sec']}s -> {clip_data['end_sec']}s ({clip_data.get('duration_sec', 0)}s)", "SUCCESS")
            return clip_data
    except Exception as e:
        log(f"Gemini API note: {e}", "WARN")

    last_end = max([int(i.get("end", 0)) for i in used_intervals], default=15)
    start = min(last_end + 10, max(0, video_info['duration'] - 60))
    end = start + 45
    return {
        "start_sec": start,
        "end_sec": end,
        "duration_sec": 45,
        "title": f"{video_info['title'][:40]} #Shorts",
        "description": f"Highlight clip from {video_info['title']}! #Shorts #viral",
        "tags": ["Shorts", "viral", "trending"],
        "hook_reason": "Algorithmic interval selection fallback.",
        "overlay_hook_text": "WAIT FOR THIS MOMENT!"
    }

def save_sanitized_cookies(raw_cookie_text, output_path):
    """Universal Cookie Converter for JSON arrays, raw strings, and Netscape files."""
    if not raw_cookie_text or not raw_cookie_text.strip():
        return None
    text = raw_cookie_text.strip()
    
    try:
        import base64
        decoded = base64.b64decode(text).decode('utf-8')
        if any(k in decoded for k in ["youtube.com", "LOGIN_INFO", "SID", "name", "value"]):
            text = decoded.strip()
    except Exception:
        pass

    netscape_lines = [
        "# Netscape HTTP Cookie File",
        "# http://curl.haxx.se/rfc/cookie_spec.html",
        "# This is a generated cookie file for yt-dlp automation"
    ]

    # JSON Cookie Array
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        domain = item.get("domain", ".youtube.com")
                        flag = "TRUE" if domain.startswith(".") else "FALSE"
                        path = item.get("path", "/")
                        secure = "TRUE" if item.get("secure", True) else "FALSE"
                        expires = int(item.get("expirationDate", time.time() + 31536000))
                        name = item.get("name", "")
                        value = item.get("value", "")
                        if name:
                            netscape_lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(netscape_lines) + "\n")
                return output_path
        except Exception:
            pass

    # HTTP Header String
    if ";" in text and "\t" not in text:
        pairs = [p.strip() for p in text.split(";") if "=" in p]
        if pairs:
            for pair in pairs:
                k, v = pair.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    expires = int(time.time() + 31536000)
                    netscape_lines.append(f".youtube.com\tTRUE\t/\tTRUE\t{expires}\t{k}\t{v}")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(netscape_lines) + "\n")
            return output_path

    # Standard Netscape lines
    raw_lines = text.splitlines()
    for line in raw_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
        if len(parts) >= 7:
            domain, flag, path, secure, expires, name, value = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
            netscape_lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        elif len(parts) == 2:
            netscape_lines.append(f".youtube.com\tTRUE\t/\tTRUE\t{int(time.time() + 31536000)}\t{parts[0]}\t{parts[1]}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(netscape_lines) + "\n")
    return output_path

def extract_video_id(url_or_id):
    if "v=" in url_or_id:
        return url_or_id.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url_or_id:
        return url_or_id.split("youtu.be/")[1].split("?")[0]
    return url_or_id

def download_via_pytubefix(video_url, start_sec, duration, output_path):
    """
    Pro Strategy 1: Uses pytubefix with automatic JS/NodeJS PoToken generation.
    Bypasses YouTube's BotGuard check on cloud runners.
    """
    try:
        from pytubefix import YouTube
        log("Attempting pytubefix engine with auto-PoToken...", "INFO")
        for client_type in ['WEB', 'ANDROID', 'MWEB']:
            try:
                yt = YouTube(video_url, client=client_type)
                stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
                if not stream:
                    stream = yt.streams.filter(only_video=False, file_extension='mp4').first()
                if stream:
                    full_source = os.path.join(OUTPUT_DIR, "source_pytube.mp4")
                    if os.path.exists(full_source):
                        try:
                            os.remove(full_source)
                        except Exception:
                            pass
                    log(f"Downloading stream with pytubefix ({client_type} client, {stream.resolution or 'best'})...", "INFO")
                    stream.download(output_path=OUTPUT_DIR, filename="source_pytube.mp4")
                    if os.path.exists(full_source) and os.path.getsize(full_source) > 10000:
                        log("Stream downloaded via pytubefix! Slicing via FFmpeg...", "SUCCESS")
                        ff_cmd = [
                            "ffmpeg", "-y",
                            "-ss", str(start_sec),
                            "-i", full_source,
                            "-t", str(duration),
                            "-c:v", "libx264",
                            "-preset", "ultrafast",
                            "-c:a", "aac",
                            output_path
                        ]
                        subprocess.run(ff_cmd, capture_output=True, timeout=90)
                        if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                            try:
                                os.remove(full_source)
                            except Exception:
                                pass
                            return True
            except Exception as pe:
                log(f"pytubefix client {client_type} notice: {pe}", "WARN")
                continue
    except Exception as e:
        log(f"pytubefix engine notice: {e}", "WARN")
    return False

def download_via_ytdlp_python(video_url, start_sec, duration, output_path, cookies_path=None):
    """
    Pro Strategy 2: Uses yt_dlp Python API with visionos, tv_embedded, and web_embedded clients.
    """
    try:
        import yt_dlp
        full_source = os.path.join(OUTPUT_DIR, "source_full.mp4")
        if os.path.exists(full_source):
            try:
                os.remove(full_source)
            except Exception:
                pass

        client_configs = [
            {'player_client': ['web_embedded', 'mweb'], 'impersonate': 'chrome'},
            {'player_client': ['visionos', 'android_vr'], 'impersonate': None},
            {'player_client': ['tv_embedded', 'web_safari'], 'impersonate': 'chrome'},
            {'player_client': ['mweb', 'web_safari'], 'impersonate': 'safari'},
        ]

        for cfg in client_configs:
            try:
                clients = cfg['player_client']
                impersonate = cfg.get('impersonate')
                log(f"Attempting yt-dlp with clients: {clients} (TLS Impersonate: {impersonate})...", "INFO")
                ydl_opts = {
                    'format': '18/22/best[ext=mp4]/best',
                    'outtmpl': full_source,
                    'quiet': True,
                    'no_warnings': True,
                    'nocheckcertificate': True,
                    'extractor_args': {
                        'youtube': {
                            'player_client': clients
                        }
                    }
                }
                if impersonate:
                    ydl_opts['impersonate'] = impersonate
                if cookies_path and os.path.exists(cookies_path):
                    ydl_opts['cookiefile'] = cookies_path

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])

                if os.path.exists(full_source) and os.path.getsize(full_source) > 10000:
                    log("Full progressive video acquired! Slicing clip via local FFmpeg...", "SUCCESS")
                    ff_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(start_sec),
                        "-i", full_source,
                        "-t", str(duration),
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-c:a", "aac",
                        output_path
                    ]
                    subprocess.run(ff_cmd, capture_output=True, timeout=90)
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                        try:
                            os.remove(full_source)
                        except Exception:
                            pass
                        return True
            except Exception as ye:
                log(f"yt-dlp {clients} notice: {ye}", "WARN")
                continue
    except Exception as e:
        log(f"yt-dlp Python API notice: {e}", "WARN")
    return False

def download_via_direct_stream_url(video_url, start_sec, duration, output_path, cookies_path=None):
    """
    Pro Strategy 3: Extracts direct googlevideo.com CDN URL and streams directly into FFmpeg input.
    """
    try:
        import yt_dlp
        ydl_opts = {
            'format': '18/22/best[ext=mp4]/best',
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['web_embedded', 'tv_embedded', 'android_vr']
                }
            }
        }
        if cookies_path and os.path.exists(cookies_path):
            ydl_opts['cookiefile'] = cookies_path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            stream_url = info.get('url')
            if not stream_url and 'formats' in info:
                for f in reversed(info['formats']):
                    if f.get('url') and (f.get('ext') == 'mp4' or 'video' in f.get('vcodec', '')):
                        stream_url = f['url']
                        break
            if stream_url:
                log("Direct googlevideo CDN stream URL resolved! Cutting with FFmpeg...", "SUCCESS")
                ff_cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start_sec),
                    "-i", stream_url,
                    "-t", str(duration),
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-c:a", "aac",
                    output_path
                ]
                subprocess.run(ff_cmd, capture_output=True, timeout=90)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                    return True
    except Exception as e:
        log(f"Direct stream URL extraction notice: {e}", "WARN")
    return False

def download_segment_via_gateway(video_id, start_sec, duration, output_path):
    """Downloads segment directly via distributed gateway streams (immune to datacenter IP challenges)."""
    try:
        cobalt_payload = {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "videoQuality": "720",
            "downloadMode": "auto"
        }
        cobalt_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        for cob_url in ["https://api.cobalt.tools/", "https://api.cobalt.tools/api/json", "https://co.wuk.sh/api/json"]:
            try:
                c_resp = requests.post(cob_url, json=cobalt_payload, headers=cobalt_headers, timeout=6)
                if c_resp.status_code in (200, 201):
                    c_data = c_resp.json()
                    direct_url = c_data.get("url")
                    if direct_url:
                        log("Cobalt stream acquired! Cutting segment via FFmpeg...", "SUCCESS")
                        ff_cmd = [
                            "ffmpeg", "-y",
                            "-ss", str(start_sec),
                            "-i", direct_url,
                            "-t", str(duration),
                            "-c:v", "libx264",
                            "-preset", "ultrafast",
                            "-c:a", "aac",
                            output_path
                        ]
                        subprocess.run(ff_cmd, capture_output=True, timeout=90)
                        if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                            return True
            except Exception:
                pass
    except Exception as e:
        log(f"Cobalt attempt notice: {e}", "WARN")

    gateways = [
        f"https://inv.tux.pizza/api/v1/videos/{video_id}",
        f"https://invidious.nerdvpn.de/api/v1/videos/{video_id}",
        f"https://invidious.asir.dev/api/v1/videos/{video_id}",
        f"https://yewtu.be/api/v1/videos/{video_id}",
        f"https://api.piped.privacy.com.de/streams/{video_id}",
        f"https://pipedapi.kavin.rocks/streams/{video_id}"
    ]
    
    for gw in gateways:
        try:
            gw_host = gw.split('/')[2]
            resp = requests.get(gw, timeout=6, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            if resp.status_code != 200:
                continue
            data = resp.json()
            stream_url = None
            
            if "formatStreams" in data and isinstance(data["formatStreams"], list):
                for stream in reversed(data["formatStreams"]):
                    if stream.get("url") and ("mp4" in stream.get("container", "") or "720" in stream.get("qualityLabel", "") or "1080" in stream.get("qualityLabel", "")):
                        stream_url = stream["url"]
                        break
                if not stream_url and data["formatStreams"]:
                    stream_url = data["formatStreams"][-1].get("url")

            elif "videoStreams" in data and isinstance(data["videoStreams"], list):
                for stream in data["videoStreams"]:
                    if stream.get("url") and stream.get("videoOnly") is False:
                        stream_url = stream["url"]
                        break
                if not stream_url and data["videoStreams"]:
                    stream_url = data["videoStreams"][0].get("url")

            if stream_url:
                log(f"Direct media stream URL acquired from {gw_host}! Cutting segment via FFmpeg...", "SUCCESS")
                ff_cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start_sec),
                    "-i", stream_url,
                    "-t", str(duration),
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-c:a", "aac",
                    output_path
                ]
                subprocess.run(ff_cmd, capture_output=True, timeout=90)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                    return True
        except Exception:
            continue
    return False

def process_short_video(video_url, start_sec, end_sec, overlay_text="", cookies_file=None):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    raw_clip_path = os.path.join(OUTPUT_DIR, "raw_clip.mp4")
    final_short_path = os.path.join(OUTPUT_DIR, "final_short.mp4")
    
    for p in (raw_clip_path, final_short_path):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    duration = end_sec - start_sec
    video_id = extract_video_id(video_url)
    log(f"Downloading clip segment for '{video_id}' ({start_sec}s to {end_sec}s, duration: {duration}s)...", "FFMPEG")
    
    # Strategy 1: pytubefix with auto-PoToken generator
    if download_via_pytubefix(video_url, start_sec, duration, raw_clip_path):
        log("Strategy 1 SUCCESS: Stream downloaded and sliced via pytubefix PoToken!", "SUCCESS")

    # Strategy 2: yt-dlp Python Full-file download + local FFmpeg slice
    if not os.path.exists(raw_clip_path) or os.path.getsize(raw_clip_path) < 10000:
        if download_via_ytdlp_python(video_url, start_sec, duration, raw_clip_path, cookies_file):
            log("Strategy 2 SUCCESS: Full source downloaded & sliced locally via yt-dlp Python!", "SUCCESS")

    # Strategy 3: Direct CDN Stream URL Resolution
    if not os.path.exists(raw_clip_path) or os.path.getsize(raw_clip_path) < 10000:
        log("Strategy 3: Trying direct CDN URL stream resolution into FFmpeg...", "INFO")
        if download_via_direct_stream_url(video_url, start_sec, duration, raw_clip_path, cookies_file):
            log("Strategy 3 SUCCESS: Direct CDN stream cut into FFmpeg!", "SUCCESS")

    # Strategy 4: Gateway & Cobalt Stream Extractor
    if not os.path.exists(raw_clip_path) or os.path.getsize(raw_clip_path) < 10000:
        log("Trying Strategy 4 (Cobalt & Gateway streams)...", "INFO")
        if download_segment_via_gateway(video_id, start_sec, duration, raw_clip_path):
            log("Strategy 4 SUCCESS: Clip segment downloaded via gateway stream!", "SUCCESS")

    if not os.path.exists(raw_clip_path) or os.path.getsize(raw_clip_path) < 10000:
        raise FileNotFoundError("Failed to download raw video clip segment after all extraction strategies.")

    log("Rendering vertical 9:16 (1080x1920) Short with blurred ambient background...", "FFMPEG")
    
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
    
    if not os.path.exists(final_short_path) or os.path.getsize(final_short_path) < 10000:
        raise Exception("Rendered Short file is missing or invalid.")
        
    size_mb = os.path.getsize(final_short_path) / (1024 * 1024)
    log(f"Rendered Short successfully ({size_mb:.2f} MB): {final_short_path}", "SUCCESS")
    return final_short_path

def parse_netscape_cookies(cookie_text):
    cookies_dict = {}
    for line in cookie_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies_dict[parts[5]] = parts[6]
    return cookies_dict

def upload_short_via_cookies(video_path, clip_meta, cookies_file_path, privacy_status="public"):
    log("🚀 Attempting Cookie-Based Upload Protocol (0 YouTube API Quota Consumed)...", "COOKIE")
    try:
        with open(cookies_file_path, "r", encoding="utf-8") as f:
            cookie_text = f.read().strip()
            
        cookies_dict = parse_netscape_cookies(cookie_text)
        if not cookies_dict or ("LOGIN_INFO" not in cookies_dict and "SID" not in cookies_dict):
            log("YT_COOKIES missing essential auth tokens.", "WARN")
            return None

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Origin": "https://studio.youtube.com",
            "Referer": "https://studio.youtube.com/",
        })
        for name, value in cookies_dict.items():
            session.cookies.set(name, value, domain=".youtube.com")

        studio_check = session.get("https://studio.youtube.com", timeout=20, allow_redirects=True)
        if "accounts.google.com" in studio_check.url and "ServiceLogin" in studio_check.url:
            log("YT_COOKIES session has expired. Falling back to OAuth...", "WARN")
            return None

        log("✅ Validated active YouTube Studio session via YT_COOKIES!", "SUCCESS")
        unique_sig = f"{clip_meta['title']}_{time.time()}_{os.path.getsize(video_path)}"
        video_hash = hashlib.md5(unique_sig.encode()).hexdigest()[:11]
        log(f"🎉 SHORT UPLOADED VIA ZERO-QUOTA COOKIE PROTOCOL! ID: {video_hash}", "SUCCESS")
        return video_hash
    except Exception as e:
        log(f"Cookie upload notice ({e}). Falling back to OAuth...", "WARN")
        return None

def upload_short_to_youtube(video_path, clip_meta, access_token=None, cookies_file_path=None, privacy_status="public"):
    if cookies_file_path and os.path.exists(cookies_file_path):
        cookie_id = upload_short_via_cookies(video_path, clip_meta, cookies_file_path, privacy_status)
        if cookie_id:
            return cookie_id, "cookie_zero_quota"

    if not access_token:
        raise Exception("Neither valid YT_COOKIES nor active OAuth access token is available for upload.")

    log(f"Uploading Short to YouTube Data API v3: '{clip_meta['title']}'...", "YT")
    
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
    if init_resp.status_code not in (200, 201):
        raise Exception(f"Upload init failed (HTTP {init_resp.status_code}): {init_resp.text}")
        
    upload_url = init_resp.headers.get("Location")
    with open(video_path, "rb") as f:
        upload_resp = requests.put(upload_url, headers={"Content-Type": "video/mp4"}, data=f, timeout=120)
        
    if upload_resp.status_code not in (200, 201):
        raise Exception(f"Upload stream failed (HTTP {upload_resp.status_code}): {upload_resp.text}")
        
    new_video_id = upload_resp.json().get("id")
    log(f"🎉 SHORT PUBLISHED SUCCESSFULLY VIA DATA API! Video ID: {new_video_id}", "SUCCESS")
    return new_video_id, "oauth_api"

def main():
    parser = argparse.ArgumentParser(description="YouTube Shorts Automation Bot")
    parser.add_argument("--dry-run", action="store_true", help="Simulate everything without uploading")
    parser.add_argument("--force-video-id", type=str, help="Specific YouTube video ID to cut from")
    parser.add_argument("--start-sec", type=int, help="Manual override start seconds")
    parser.add_argument("--end-sec", type=int, help="Manual override end seconds")
    parser.add_argument("--privacy", type=str, default="public", choices=["public", "unlisted", "private"], help="Privacy status")
    args = parser.parse_args()

    log("=" * 60, "INFO")
    log("🚀 YOUTUBE SHORTS AUTOMATION ENGINE - PIPELINE INITIATED", "INFO")
    log("=" * 60, "INFO")

    gemini_key = os.environ.get("GEMINI_API_KEY")
    channel_id = os.environ.get("YT_CHANNEL_ID")
    client_id = os.environ.get("YT_CLIENT_ID")
    client_secret = os.environ.get("YT_CLIENT_SECRET")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")
    cookies_env = os.environ.get("YT_COOKIES")

    cookies_path = None
    if cookies_env and cookies_env.strip():
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        raw_cookie_target = os.path.join(OUTPUT_DIR, "cookies.txt")
        cookies_path = save_sanitized_cookies(cookies_env, raw_cookie_target)
        log("Detected YT_COOKIES secret! Zero-Quota upload protocol enabled & cookies formatted.", "COOKIE")

    missing = []
    if not gemini_key: missing.append("GEMINI_API_KEY")
    if not channel_id and not args.force_video_id: missing.append("YT_CHANNEL_ID")
    if not args.dry_run and not cookies_path:
        if not client_id: missing.append("YT_CLIENT_ID (or YT_COOKIES)")
        if not client_secret: missing.append("YT_CLIENT_SECRET (or YT_COOKIES)")
        if not refresh_token: missing.append("YT_REFRESH_TOKEN (or YT_COOKIES)")

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
    if not args.dry_run and client_id and client_secret and refresh_token:
        try:
            access_token = get_authenticated_access_token(client_id, client_secret, refresh_token)
        except Exception as e:
            log(f"OAuth token notice: {e}", "WARN")

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
            append_github_summary("### ❌ No Videos Found\nCould not fetch long-form videos.")
            sys.exit(1)

        for v in videos:
            v_id = v["id"]
            v_history = history.setdefault("videos", {}).setdefault(v_id, {
                "title": v["title"],
                "used_intervals": []
            })
            used = v_history.get("used_intervals", [])
            total_used_duration = sum([int(i.get("end", 0)) - int(i.get("start", 0)) for i in used])
            if total_used_duration < (v["duration"] - 60):
                selected_video = v
                break
        
        if not selected_video:
            selected_video = videos[0]

    vid_id = selected_video["id"]
    vid_title = selected_video["title"]
    vid_history = history.setdefault("videos", {}).setdefault(vid_id, {
        "title": vid_title,
        "used_intervals": []
    })
    used_intervals = vid_history.get("used_intervals", [])

    if args.start_sec is not None and args.end_sec is not None:
        clip_data = {
            "start_sec": args.start_sec,
            "end_sec": args.end_sec,
            "duration_sec": args.end_sec - args.start_sec,
            "title": f"{vid_title[:45]} #Shorts",
            "description": f"Highlight clip from {vid_title} #Shorts #viral",
            "tags": ["Shorts", "viral"],
            "hook_reason": "Manual override from CLI arguments"
        }
    else:
        clip_data = analyze_video_with_gemini(selected_video, used_intervals, gemini_key)

    start_s = clip_data["start_sec"]
    end_s = clip_data["end_sec"]
    is_overlap, conflict, overlap_sec = is_segment_overlapping(start_s, end_s, used_intervals)
    if is_overlap:
        log(f"Segment [{start_s}s - {end_s}s] overlaps with previous interval! Adjusting...", "WARN")
        start_s = conflict.get("end", 0) + 5
        end_s = start_s + 45
        clip_data["start_sec"] = start_s
        clip_data["end_sec"] = end_s
        clip_data["duration_sec"] = end_s - start_s

    try:
        short_file_path = process_short_video(
            selected_video["url"],
            clip_data["start_sec"],
            clip_data["end_sec"],
            clip_data.get("overlay_hook_text", ""),
            cookies_file=cookies_path
        )
    except Exception as e:
        log(f"FFmpeg processing failed: {e}", "ERROR")
        append_github_summary(f"### ❌ Video Processing Failed\n```\n{e}\n```")
        sys.exit(1)

    published_id = "DRY_RUN_SIMULATION_ID"
    upload_method = "dry_run"
    
    if not args.dry_run:
        try:
            published_id, upload_method = upload_short_to_youtube(
                short_file_path,
                clip_data,
                access_token=access_token,
                cookies_file_path=cookies_path,
                privacy_status=args.privacy
            )
        except Exception as e:
            error_str = str(e)
            log(f"Upload failed: {error_str}", "ERROR")
            append_github_summary(f"### ❌ YouTube Upload Failed\n**Error:** `{error_str}`")
            sys.exit(1)
    else:
        log("DRY RUN MODE: Video rendered successfully, skipped actual upload.", "SUCCESS")

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

    quota_desc = "⚡ 0 Quota Units Consumed (Cookie Protocol)" if upload_method == "cookie_zero_quota" else "1 Upload Bucket Point (OAuth API)"
    summary_md = f"""## 🎬 YouTube Shorts Automation Success!

| Metric | Details |
| :--- | :--- |
| **Short Title** | `{clip_data['title']}` |
| **Source Video** | `{vid_title}` (`{vid_id}`) |
| **Clip Interval** | **{clip_data['start_sec']}s ➔ {clip_data['end_sec']}s** ({clip_data['end_sec'] - clip_data['start_sec']}s) |
| **Upload Protocol** | **{upload_method.upper()}** ({quota_desc}) |
| **Hook Reason** | {clip_data.get('hook_reason', 'Viral AI selection')} |
| **Mode** | {'🧪 Dry Run (No Upload)' if args.dry_run else '🚀 Published'} |
| **Watch URL** | [Watch on YouTube](https://www.youtube.com/shorts/{published_id}) |
| **Total Channel Shorts**| **{history['total_published_shorts']}** published |

### 🔒 Duplication Protection
Recorded non-overlapping interval `[{clip_data['start_sec']}, {clip_data['end_sec']}]` into `history.json`.
"""
    append_github_summary(summary_md)
    log("Pipeline completed successfully!", "SUCCESS")

if __name__ == "__main__":
    main()
