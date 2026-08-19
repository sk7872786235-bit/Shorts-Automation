import os
import sys
import json
import time
import subprocess
import argparse
from pathlib import Path

# Google API Imports
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

OUTPUT_DIR = Path("output_shorts")
OUTPUT_DIR.mkdir(exist_ok=True)
HISTORY_FILE = Path("history.json")

# ---------------------------------------------------------
# STATE MANAGEMENT (Never repeat content)
# ---------------------------------------------------------
def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    # Structure: {"video_id_1": [[0, 45], [120, 165]], "video_id_2": [...]}
    return {"used_segments": {}}

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def is_overlap(new_start, new_end, existing_segments):
    """Returns True if the proposed clip overlaps with an already processed clip."""
    for (st, en) in existing_segments:
        if max(new_start, st) < min(new_end, en):
            return True # Overlap detected
    return False

# ---------------------------------------------------------
# CORE PIPELINE
# ---------------------------------------------------------
def get_channel_videos(channel_id, cookies_file):
    print(f"[*] Scanning channel: {channel_id}")
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(duration)s",
        "--cookies", cookies_file,
        "--no-check-certificates",
        url
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[!] Fetch failed: {res.stderr}")
        return []
    
    videos = []
    for line in res.stdout.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2:
            videos.append({"id": parts[0].strip(), "title": parts[1].strip()})
    return videos

def process_video(video, history, cookies_file):
    vid_id = video["id"]
    used_segments = history["used_segments"].get(vid_id, [])
    
    print(f"[*] Analyzing '{video['title']}'...")
    
    # Download Video
    local_mp4 = f"{vid_id}.mp4"
    if not Path(local_mp4).exists():
        print(f"[*] Downloading source video: {vid_id}")
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
            "--cookies", cookies_file,
            "-o", local_mp4,
            f"https://www.youtube.com/watch?v={vid_id}"
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)

    # Extract Audio for Whisper
    local_wav = f"{vid_id}.wav"
    subprocess.run(["ffmpeg", "-y", "-i", local_mp4, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", local_wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Transcribe & Find Segments
    print("[*] Transcribing with faster-whisper...")
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(local_wav, beam_size=5)
    
    total_duration = info.duration
    target_duration = 45 # seconds per short
    
    # Locate a segment that hasn't been used yet
    start_time = 15.0 # Skip standard intros
    selected_clip = None
    
    while start_time + target_duration < total_duration:
        end_time = start_time + target_duration
        if not is_overlap(start_time, end_time, used_segments):
            selected_clip = {"start": start_time, "end": end_time, "duration": target_duration}
            break
        start_time += 30.0 # Shift search window forward if blocked by overlap
        
    if not selected_clip:
        print("[!] Video exhausted (no unused high-retention segments left).")
        return False # Move to next video
        
    # We found a fresh clip! Let's render it.
    out_file = OUTPUT_DIR / f"short_{vid_id}_{int(selected_clip['start'])}.mp4"
    srt_file = OUTPUT_DIR / f"sub_{vid_id}.srt"
    
    # Generate Dummy SRT (Integrate Gemini hook logic here if desired)
    with open(srt_file, "w") as f:
        f.write("1\n00:00:00,000 --> 00:00:03,500\nWATCH THIS CLOSELY!\n\n")
        f.write("2\n00:00:03,500 --> 00:00:45,000\nLink in description!\n\n")
        
    print(f"[*] Rendering new segment: {selected_clip['start']}s to {selected_clip['end']}s")
    srt_clean = str(srt_file).replace("\\", "/").replace(":", "\\:")
    style = "Fontname=Arial,Fontsize=22,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=140"
    
    filter_complex = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[b];"
        "[fg]scale=1080:-2[f];"
        f"[b][f]overlay=(W-w)/2:(H-h)/2,subtitles='{srt_clean}':force_style='{style}'[v_out]"
    )
    
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(selected_clip["start"]), "-t", str(selected_clip["duration"]),
        "-i", local_mp4, "-filter_complex", filter_complex, "-map", "[v_out]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "128k", str(out_file)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Upload via YouTube API
    upload_short(out_file, f"Secret from {video['title'][:40]} #Shorts", vid_id)
    
    # Mark segment as used and save state
    used_segments.append([selected_clip["start"], selected_clip["end"]])
    history["used_segments"][vid_id] = used_segments
    save_history(history)
    
    print("[+] Successfully generated and uploaded!")
    return True

# ---------------------------------------------------------
# YOUTUBE UPLOAD (Using mapped Environment variables)
# ---------------------------------------------------------
def upload_short(video_path, title, original_video_id):
    client_id = os.environ.get("YT_CLIENT_ID")
    client_secret = os.environ.get("YT_CLIENT_SECRET")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")
    
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )
    youtube = build("youtube", "v3", credentials=creds)
    
    body = {
        "snippet": {
            "title": title,
            "description": f"Watch the full video: https://youtu.be/{original_video_id}\n\n#Shorts #Tech",
            "categoryId": "22"
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    res = req.execute()
    print(f"[+] Uploaded: https://youtube.com/shorts/{res.get('id')}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookies", required=True, help="Path to yt-dlp cookies file")
    args = parser.parse_args()

    channel_id = os.environ.get("YT_CHANNEL_ID")
    if not channel_id:
        print("[!] Missing YT_CHANNEL_ID environment variable.")
        sys.exit(1)

    history = load_history()
    videos = get_channel_videos(channel_id, args.cookies)
    
    # Iterate through videos until we find one with unused segments
    for video in videos:
        success = process_video(video, history, args.cookies)
        if success:
            # We process exactly 1 clip per hour to pace the API
            break
            
    # Cleanup temp files
    for f in Path(".").glob("*.mp4"):
        if not str(f).startswith("output_shorts"):
            f.unlink()
    for f in Path(".").glob("*.wav"):
        f.unlink()
