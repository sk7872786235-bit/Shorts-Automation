import os, sys, json, feedparser, subprocess
from google import genai
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def main():
    # 1. Fetch Latest Video from RSS
    channel_id = os.environ.get("YT_CHANNEL_ID")
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)
    
    if not feed.entries:
        print("No videos found on channel.", flush=True)
        sys.exit(0)

    # 2. Find the newest unprocessed video
    if not os.path.exists("processed.txt"):
        open("processed.txt", "w").close()

    with open("processed.txt", "r") as f:
        processed_videos = f.read()

    valid_video = None
    for entry in feed.entries:
        if entry.yt_videoid not in processed_videos:
            valid_video = entry
            break # Found the newest video we haven't cut yet!
            
    if not valid_video:
        print("All recent videos have already been processed. Waiting for new uploads.", flush=True)
        sys.exit(0)

    video_id = valid_video.yt_videoid
    video_title = valid_video.title
    yt_url = f"https://www.youtube.com/watch?v={video_id}"

    print(f"Processing Kids Video: {video_title} ({video_id})", flush=True)

    # Handle Cookies
    cookie_param = ""
    yt_cookies = os.environ.get("YT_COOKIES", "")
    if yt_cookies:
        with open("cookies.txt", "w") as f:
            f.write(yt_cookies)
        cookie_param = '--cookies cookies.txt'

    # 3. Download AUDIO ONLY for Gemini to listen to
    print("Downloading audio track for AI analysis...", flush=True)
    audio_cmd = f'yt-dlp {cookie_param} -f "bestaudio[ext=m4a]" "{yt_url}" -o "audio.m4a"'
    subprocess.run(audio_cmd, shell=True, check=True)

    # 4. Upload Audio to Gemini 1.5 Flash
    print("Uploading audio to Gemini for timestamp extraction...", flush=True)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    
    # Upload the file to Google's servers temporarily
    audio_file = client.files.upload(file="audio.m4a")
    
    prompt = """
    Listen to this audio track from a kids' YouTube video. 
    Find the most engaging, catchy 30 to 50 second segment (like the chorus of a song, or the most energetic part).
    Return ONLY a valid JSON object with the exact start and end time in seconds. No formatting, no markdown blocks.
    Example: {"start": 12, "end": 45}
    """
    
    print("Analyzing audio...", flush=True)
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=[audio_file, prompt]
    )
    
    clean_json = response.text.strip().replace("```json", "").replace("```", "")
    timestamps = json.loads(clean_json)
    
    start = int(timestamps["start"])
    duration = int(timestamps["end"]) - start
    print(f"Gemini selected the catchiest part: Start {start}s, Duration {duration}s", flush=True)

    # 5. Download ONLY that specific video segment & format to 9:16
    print("Downloading that specific video segment...", flush=True)
    dl_cmd = f'yt-dlp {cookie_param} --download-sections "*{start}-{start+duration}" --force-keyframes-at-cuts -f "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" "{yt_url}" -o "clip.mp4"'
    subprocess.run(dl_cmd, shell=True, check=True)

    print("Cropping to 9:16 vertical via FFmpeg...", flush=True)
    crop_cmd = 'ffmpeg -i clip.mp4 -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v libx264 -preset fast -crf 22 -c:a aac output.mp4 -y'
    subprocess.run(crop_cmd, shell=True, check=True)

    # 6. Upload to YouTube Shorts (Kids Compliant)
    print("Uploading to YouTube Shorts...", flush=True)
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"]
    )
    youtube = build("youtube", "v3", credentials=creds)
    
    body = {
        "snippet": {
            "title": f"{video_title[:80]} #Shorts",
            "description": "Fun moment from our latest video! #kids #nurseryrhymes #Shorts",
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": True # CRITICAL FOR COPPA
        }
    }
    
    youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload("output.mp4", mimetype="video/mp4")
    ).execute()

    print("Upload complete!", flush=True)

    # 7. Record as processed
    with open("processed.txt", "a") as f:
        f.write(f"{video_id}\n")

    # Cleanup temp audio file from Google's servers
    try:
        client.files.delete(name=audio_file.name)
    except:
        pass

if __name__ == "__main__":
    main()
