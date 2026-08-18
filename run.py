"""
Automated YouTube Shorts Pipeline (run.py)
"""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import feedparser
from google import genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from youtube_transcript_api import YouTubeTranscriptApi

# Setup Gemini Client (SDK v1.0+)
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


# -------------------------------------------------------------------
# 1. Fetch Latest Video ID from RSS Feed
# -------------------------------------------------------------------
def get_latest_video_id(channel_id: str) -> Optional[str]:
    rss_url = (
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    )
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        print("No videos found in feed.")
        return None
    latest_entry = feed.entries[0]
    # Format: yt:video:VIDEO_ID
    return latest_entry.yt_videoid


# -------------------------------------------------------------------
# 2. Get Video Transcript
# -------------------------------------------------------------------
def get_video_transcript(video_id: str) -> Optional[str]:
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        full_text = " ".join([entry["text"] for entry in transcript])
        return full_text
    except Exception as e:
        print(f"Error retrieving transcript: {e}")
        return None


# -------------------------------------------------------------------
# 3. Detect Top Clips using Gemini API
# -------------------------------------------------------------------
def get_viral_clips(transcript: str) -> List[Dict[str, str]]:
    prompt = f"""
    You are an expert video editor. Analyze the transcript below and identify up to 5 viral, high-hook clips lasting between 30 and 60 seconds.
    Return ONLY a JSON array of objects containing string fields: "start", "duration", "title", and "description".
    Format timestamps strictly as HH:MM:SS or SS.
    
    Transcript:
    {transcript}
    """

    response = gemini_client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )

    try:
        clips = json.loads(response.text)
        return clips
    except Exception as e:
        print(f"Failed to parse Gemini JSON output: {e}")
        return []


# -------------------------------------------------------------------
# 4. Download Video Segment & Crop to 9:16 Vertical (1080x1920)
# -------------------------------------------------------------------
def process_clip(
    video_id: str, start: str, duration: str, output_path: str
) -> bool:
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # Get stream URL using yt-dlp
    get_url_cmd = [
        "yt-dlp",
        "-g",
        "-f",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        video_url,
    ]

    try:
        stream_urls = subprocess.check_output(get_url_cmd).decode().splitlines()
        video_stream = stream_urls[0]
    except Exception as e:
        print(f"yt-dlp error: {e}")
        return False

    # FFmpeg Command: Crops 16:9 to center-cut 9:16 (1080x1920)
    # Filter breakdown: crop=ih*(9/16):ih:in_w/2-out_w/2:0 crops to 9:16 ratio, then scale resizes to 1080x1920.
    ffmpeg_cmd = [
        "ffmpeg",
        "-ss",
        str(start),
        "-i",
        video_stream,
        "-t",
        str(duration),
        "-vf",
        "crop=ih*(9/16):ih:(iw-ow)/2:0,scale=1080:1920",
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-y",
        output_path,
    ]

    try:
        subprocess.run(ffmpeg_cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg encoding error: {e}")
        return False


# -------------------------------------------------------------------
# 5. Upload Generated Short to YouTube
# -------------------------------------------------------------------
def get_authenticated_youtube_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload_short(file_path: str, title: str, description: str):
    youtube = get_authenticated_youtube_service()

    body = {
        "snippet": {
            "title": title[:100],  # YouTube title limit
            "description": f"{description}\n\n#shorts",
            "tags": ["shorts", "viral", "automation"],
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        file_path, chunksize=-1, resumable=True, mimetype="video/mp4"
    )
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = request.execute()
    print(
        f"Successfully uploaded short: https://youtube.com/shorts/{response['id']}"
    )


# -------------------------------------------------------------------
# Execution Main Loop
# -------------------------------------------------------------------
def main():
    channel_id = os.environ.get("YT_CHANNEL_ID")
    if not channel_id:
        print("Missing YT_CHANNEL_ID variable.")
        sys.exit(1)

    video_id = get_latest_video_id(channel_id)
    if not video_id:
        return

    print(f"Processing latest video ID: {video_id}")
    transcript = get_video_transcript(video_id)
    if not transcript:
        print("Skipping video due to missing transcript.")
        return

    clips = get_viral_clips(transcript)
    print(f"Found {len(clips)} clip candidate(s).")

    for idx, clip in enumerate(clips[:1]):  # Process top candidate per run
        output_file = f"short_{idx}.mp4"
        print(f"Rendering clip: {clip.get('title')}")

        success = process_clip(
            video_id, clip["start"], clip["duration"], output_file
        )
        if success:
            upload_short(output_file, clip["title"], clip["description"])
            if os.path.exists(output_file):
                os.remove(output_file)


if __name__ == "__main__":
    main()
