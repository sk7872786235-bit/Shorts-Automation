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
    if not cookies_content or len(cookies_content.strip()) == 0:
        print("CRITICAL WARNING: YT_COOKIES environment variable not found.")
        print("YouTube downloads will likely fail due to bot detection.")
        return None

    cookie_file = "cookies.txt"
    with open(cookie_file, "w", encoding="utf-8") as f:
        f.write(cookies_content)
    print("Successfully wrote cookies.txt from environment variable.")
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
        # Fixed: Updated to gemini-3.6-flash as per the API 404 error log
        # Fixed: Using Chat session to avoid the Automatic Function Calling (AFC) warning
        chat = gemini_client.chats.create(model="gemini-3.6-flash")
        response = chat.send_message(prompt)
        
        text_response = response.text.strip()
        
        # Clean up Markdown JSON formatting if present
        if text_response.startswith("```json"):
            text_response = text_response[7:]
        if text_response.endswith("
