import os, sys, time, json, feedparser, subprocess
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from google import genai
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def get_transcript_with_retry(video_id, max_retries=6, wait_minutes=15):
    """Waits for YouTube auto-captions to finish generating."""
    for attempt in range(max_retries):
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = transcript_list.find_transcript(['en'])
            return transcript.fetch()
        except (NoTranscriptFound, TranscriptsDisabled):
            print(f"Captions not ready yet. Sleeping {wait_minutes} mins... (Attempt {attempt + 1}/{max_retries})")
            time.sleep(wait_minutes * 60)
            
    print("Failed to get transcript after max retries. Exiting cleanly.")
    sys.exit(0)

def main():
    # 1. Fetch Latest Video from RSS
    channel_id = os.environ["YT_CHANNEL_ID"]
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)
    
    if not feed.entries:
        print("No videos found on channel.")
        sys.exit(0)

    latest_video = feed.entries[0]
    video_id = latest_video.yt_videoid
    video_title = latest_video.title

    # 2. Check if we already processed this video
    if not os.path.exists("processed.txt"):
        open("processed.txt", "w").close()

    with open("processed.txt", "r") as f:
        if video_id in f.read():
            print(f"Video {video_id} already processed. Waiting for next upload.")
            sys.exit(0)

    print(f"New video detected: {video_title} ({video_id})")

    # 3. Get Transcript
    transcript_data = get_transcript_with_retry(video_id)
    transcript_text = " ".join([f"[{t['start']:.1f}s] {t['text']}" for t in transcript_data])

    # 4. Ask Gemini Flash for the best 30-50s viral segment
    print("Asking Gemini to find the viral hook...")
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    
    prompt = f"""
    Analyze this video transcript. Find the single most engaging, high-retention 30 to 50 second segment.
    Return ONLY a valid JSON object with the exact start and end time in seconds. No formatting, no markdown blocks.
    Example output: {{"start": 12, "end": 45}}
    
    Transcript:
    {transcript_text[:25000]}
    """
    
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt
    )
    
    clean_json = response.text.strip().replace("```json", "").replace("```", "")
    timestamps = json.loads(clean_json)
    
    start = int(timestamps["start"])
    duration = int(timestamps["end"]) - start
    print(f"Gemini selected: Start {start}s, Duration {duration}s")

    # 5. Handle Cookies and Download Segment
    print("Downloading and cropping video...")
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Process cookies if provided to bypass YouTube bot detection
    cookie_param = ""
    yt_cookies = os.environ.get("YT_COOKIES", "")
    if yt_cookies:
        with open("cookies.txt", "w") as f:
            f.write(yt_cookies)
        cookie_param = '--cookies cookies.txt'

    dl_cmd = f'yt-dlp {cookie_param} --download-sections "*{start}-{start+duration}" -f "bestvideo[ext=mp4]+bestaudio[m4a]/best" "{yt_url}" -o "clip.mp4"'
    subprocess.run(dl_cmd, shell=True, check=True)

    # Crop to 1080x1920 (Vertical)
    crop_cmd = 'ffmpeg -i clip.mp4 -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v libx264 -c:a aac output.mp4 -y'
    subprocess.run(crop_cmd, shell=True, check=True)

    # 6. Upload to YouTube Shorts
    print("Uploading to YouTube Shorts...")
    # Because you separated the secrets, you can build the credentials object directly!
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
            "description": "Automated cut from the main video #Shorts",
            "categoryId": "22" # 22 = People & Blogs category
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }
    
    youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload("output.mp4", mimetype="video/mp4")
    ).execute()

    print("Upload complete!")

    # 7. Record the video as processed
    with open("processed.txt", "a") as f:
        f.write(f"{video_id}\n")

if __name__ == "__main__":
    main()
