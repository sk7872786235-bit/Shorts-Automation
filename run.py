"""
Automated YouTube Shorts Pipeline (run.py)
"""

import json
import os
import subprocess
import sys
from typing import Dict, List, Optional

import feedparser
from google import genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from youtube_transcript_api import YouTubeTranscriptApi

# Setup Gemini Client
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


# -------------------------------------------------------------------
# 1. Fetch Latest Video ID from RSS Feed
# -------------------------------------------------------------------
def get_latest_video_id(channel_id: str) -> Optional[str]:
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        print("No videos found in feed.")
        return None
    latest_entry = feed.entries[0]
    return latest_entry.yt_videoid


# -------------------------------------------------------------------
# 2. Get Video Transcript (Robust Multi-Version Compatibility)
# -------------------------------------------------------------------
def get_video_transcript(video_id: str) -> Optional[str]:
    try:
        # Try new instance approach
        api = YouTubeTranscriptApi()
        if hasattr(api, "get_transcript"):
            transcript_list = api.get_transcript(video_id)
        elif hasattr(YouTubeTranscriptApi, "get_transcript"):
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        else:
            # Fallback for newer v0.6+ API methods
            fetched = YouTubeTranscriptApi.get_transcripts([video_id])
            transcript_list = fetched[0][video_id]

        full_text = " ".join([entry["text"] for entry in transcript_list])
        return full_text
    except Exception as e:
        print(f"Error retrieving transcript: {e}")
        return None


# -------------------------------------------------------------------
# 3. Detect Top Clips using Gemini API
# -------------------------------------------------------------------
def get_viral_clips(transcript: str) -> List[Dict[str, str]]:
    prompt = f"""
    You are an expert video editor. Analyze the transcript below and identify up to 5 viral, high-hook clips suitable for YouTube Shorts.
    For each clip, provide:
    1. start_time (in HH:MM:SS format)
    2. end_time (in HH:MM:SS format)
    3. title (Catchy title for the Short)
    4. description (Short description with hashtags)

    Return ONLY a valid JSON array of objects with keys: "start_time", "end_time", "title", "description".

    Transcript:
    {transcript}
    """
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
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
        return []


# -------------------------------------------------------------------
# 4. Download and Cut Video using FFmpeg
# -------------------------------------------------------------------
def download_and_cut_video(video_id: str, start_time: str, end_time: str, output_file: str) -> bool:
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp",
        "-g",
        "-f",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        video_url,
    ]
    try:
        stream_url = subprocess.check_output(cmd).decode("utf-8").strip().split("\n")[0]
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            start_time,
            "-to",
            end_time,
            "-i",
            stream_url,
            "-c",
            "copy",
            output_file,
        ]
        subprocess.run(ffmpeg_cmd, check=True)
        return True
    except Exception as e:
        print(f"Error downloading/processing video clip: {e}")
        return False


# -------------------------------------------------------------------
# 5. Upload Video to YouTube via OAuth2 Credentials
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
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()
        print(f"Successfully uploaded video ID: {response.get('id')}")
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

    print(f"Fetching latest video for channel: {channel_id}")
    video_id = get_latest_video_id(channel_id)
    if not video_id:
        print("Could not retrieve latest video ID.")
        return

    print(f"Processing latest video ID: {video_id}")
    transcript = get_video_transcript(video_id)
    if not transcript:
        print("Skipping video due to missing transcript.")
        return

    print("Analyzing transcript with Gemini for viral clips...")
    clips = get_viral_clips(transcript)
    if not clips:
        print("No clips generated by Gemini.")
        return

    clip = clips[0]
    output_filename = "short_output.mp4"
    print(f"Generating clip '{clip.get('title')}' from {clip.get('start_time')} to {clip.get('end_time')}...")

    if download_and_cut_video(video_id, clip["start_time"], clip["end_time"], output_filename):
        print("Uploading short to YouTube...")
        upload_short(output_filename, clip["title"], clip["description"])


if __name__ == "__main__":
    main()
