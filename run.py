"""
Automated YouTube Shorts Pipeline (Dynamic Clip Timing + Cookie Authentication)
"""

import json
import os
import subprocess
import sys
from typing import Dict, Optional

import feedparser
from google import genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Initialize Gemini Client
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


# -------------------------------------------------------------------
# Helper: Setup Cookies from GitHub Secret
# -------------------------------------------------------------------
def setup_cookies() -> Optional[str]:
    cookies_content = os.environ.get("YT_COOKIES")
    if not cookies_content:
        print("Warning: YT_COOKIES environment variable not found.")
        return None

    cookie_file = "cookies.txt"
    with open(cookie_file, "w", encoding="utf-8") as f:
        f.write(cookies_content)
    return cookie_file


# -------------------------------------------------------------------
# 1. Fetch Latest Video Details from RSS Feed
# -------------------------------------------------------------------
def get_latest_video_info(channel_id: str) -> Optional[Dict[str, str]]:
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        print("No videos found in feed.")
        return None

    latest_entry = feed.entries[0]
    return {
        "video_id": latest_entry.yt_videoid,
        "title": latest_entry.title,
        "description": latest_entry.get("summary", ""),
    }


# -------------------------------------------------------------------
# 2. Use Gemini to Determine Clip Timestamps & Short Metadata
# -------------------------------------------------------------------
def get_dynamic_clip_info(
    title: str, description: str
) -> Optional[Dict[str, str]]:
    prompt = f"""
    You are an expert YouTube Shorts editor for a Kids Animation & Songs channel.
    
    Video Title: "{title}"
    Video Description: "{description}"
    
    Tasks:
    1. Determine the best starting timestamp (in HH:MM:SS format) for a 30 to 45 second Short clip. Skip intro title cards (usually start around 00:00:15 or 00:00:20).
    2. Suggest a clip duration (in seconds, between 30 and 45).
    3. Create a high-hook YouTube Short title with emojis and relevant hashtags (e.g. #Shorts #KidsSongs #Animation).
    4. Write a brief engaging description for parents and toddlers.
    
    Return ONLY a valid JSON object with these exact keys:
    "start_time", "duration", "short_title", "short_description"
    """
    try:
        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        text_response = response.text.strip()
        if text_response.startswith("```json"):
            text_response = text_response[7:]
        if text_response.endswith("```"):
            text_response = text_response[:-3]

        return json.loads(text_response.strip())
    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return {
            "start_time": "00:00:20",
            "duration": "40",
            "short_title": f"{title} #Shorts #KidsSongs",
            "short_description": "Check out our latest animated kids song! Subscribe for more.",
        }


# -------------------------------------------------------------------
# 3. Download, Cut & Crop Video to Vertical 9:16 Shorts Format
# -------------------------------------------------------------------
def download_and_cut_video(
    video_id: str,
    start_time: str,
    duration: str,
    output_file: str,
    cookie_file: Optional[str] = None,
) -> bool:
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    print(
        f"Fetching stream for {video_url} starting at {start_time} for {duration}s..."
    )

    cmd = [
        "yt-dlp",
        "--extractor-args",
        "youtube:player_client=android,web",
    ]

    if cookie_file and os.path.exists(cookie_file):
        cmd.extend(["--cookies", cookie_file])

    cmd.extend(
        ["-g", "-f", "b[ext=mp4]/best[ext=mp4]/best", video_url]
    )

    try:
        stream_output = (
            subprocess.check_output(cmd).decode("utf-8").strip().split("\n")
        )
        stream_url = stream_output[0]

        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            start_time,
            "-i",
            stream_url,
            "-t",
            duration,
            "-vf",
            "crop=ih*(9/16):ih",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            output_file,
        ]
        print("Processing clip with FFmpeg...")
        subprocess.run(ffmpeg_cmd, check=True)
        return True
    except Exception as e:
        print(f"Error processing video clip: {e}")
        return False


# -------------------------------------------------------------------
# 4. Upload Video to YouTube via OAuth2
# -------------------------------------------------------------------
def get_youtube_service():
    client_id = os.environ.get("YT_CLIENT_ID")
    client_secret = os.environ.get("YT_CLIENT_SECRET")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload_short(video_path: str, title: str, description: str):
    try:
        youtube = get_youtube_service()
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "1",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": True,
            },
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        response = request.execute()
        print(f"Successfully uploaded Short! Video ID: {response.get('id')}")
    except Exception as e:
        print(f"Error uploading video: {e}")


# -------------------------------------------------------------------
# Main Pipeline Execution
# -------------------------------------------------------------------
def main():
    channel_id = os.environ.get("YT_CHANNEL_ID")
    if not channel_id:
        print("YT_CHANNEL_ID environment variable is missing.")
        sys.exit(1)

    # Setup cookies file if available
    cookie_file = setup_cookies()

    print(f"Fetching latest video for channel: {channel_id}")
    video_info = get_latest_video_info(channel_id)
    if not video_info:
        print("Could not retrieve latest video details.")
        return

    video_id = video_info["video_id"]
    print(
        f"Processing video: '{video_info['title']}' (Video ID: {video_id})"
    )

    print("Asking Gemini for dynamic clip timing & Short metadata...")
    clip_info = get_dynamic_clip_info(
        video_info["title"], video_info["description"]
    )

    output_filename = "short_output.mp4"
    if download_and_cut_video(
        video_id,
        start_time=clip_info["start_time"],
        duration=str(clip_info["duration"]),
        output_file=output_filename,
        cookie_file=cookie_file,
    ):
        print("Uploading Short to YouTube...")
        upload_short(
            output_filename,
            title=clip_info["short_title"],
            description=clip_info["short_description"],
        )


if __name__ == "__main__":
    main()
