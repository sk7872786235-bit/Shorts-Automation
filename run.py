import os, sys, json, feedparser, subprocess
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from google import genai
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def get_transcript(video_id):
    """Fetches manual or auto-generated captions without freezing the runner."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # 1. Try to find manual English captions
        try:
            return transcript_list.find_manually_created_transcript(['en', 'en-US', 'en-GB']).fetch()
        except:
            pass
            
        # 2. Try to find auto-generated English captions
        try:
            return transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB']).fetch()
        except:
            pass
            
        # 3. Fallback: Take whatever language caption exists
        for transcript in transcript_list:
            return transcript.fetch()
            
    except (NoTranscriptFound, TranscriptsDisabled):
        print("Captions not ready yet. Exiting cleanly. Next 30-min run will retry.", flush=True)
        sys.exit(0)
    except Exception as e:
        print(f"Transcript error: {e}. Exiting cleanly.", flush=True)
        sys.exit(0)

def main():
    # 1. Fetch Latest Video from RSS
    channel_id = os.environ.get("YT_CHANNEL_ID")
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)
    
    if not feed.entries:
        print("No videos found on channel.", flush=True)
        sys.exit(0)

    latest_video = feed.entries[0]
    video_id = latest_video.yt_videoid
    video_title = latest_video.title

    # 2. Check if already processed
    if not os.path.exists("processed.txt"):
        open("processed.txt", "w").close()

    with open("processed.txt", "r") as f:
        if video_id in f.read():
            print(f"Video {video_id} already processed. Waiting for next upload.", flush=True)
            sys.exit(0)

    print(f"Processing new video: {video_title} ({video_id})", flush=True)

    # 3. Fetch Transcript
    transcript_data = get_transcript(video_id)
    transcript_text = " ".join([f"[{t['start']:.1f}s] {t['text']}" for t in transcript_data])
    print(f"Transcript extracted ({len(transcript_text)} characters)", flush=True)

    # 4. Ask Gemini for viral hook timestamps
    print("Calling Gemini 1.5 Flash for optimal 30-50s clip...", flush=True)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    
    prompt = f"""
    Analyze this video transcript. Find the single most engaging, high-retention 30 to 50 second segment.
    Return ONLY a valid JSON object with the exact start and end time in seconds. No formatting, no markdown blocks.
    Example: {{"start": 12, "end": 45}}
    
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
    print(f"Selected clip: Start {start}s, Duration {duration}s", flush=True)

    # 5. Handle Cookies and Fast Download
    print("Downloading video clip via yt-dlp...", flush=True)
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    
    cookie_param = ""
    yt_cookies = os.environ.get("YT_COOKIES", "")
    if yt_cookies:
        with open("cookies.txt", "w") as f:
            f.write(yt_cookies)
        cookie_param = '--cookies cookies.txt'

    # Download only the requested section
    dl_cmd = f'yt-dlp {cookie_param} --download-sections "*{start}-{start+duration}" --force-keyframes-at-cuts -f "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" "{yt_url}" -o "clip.mp4"'
    subprocess.run(dl_cmd, shell=True, check=True)

    # Crop to 1080x1920 (Vertical 9:16)
    print("Cropping to 9:16 vertical via FFmpeg...", flush=True)
    crop_cmd = 'ffmpeg -i clip.mp4 -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v libx264 -preset fast -crf 22 -c:a aac output.mp4 -y'
    subprocess.run(crop_cmd, shell=True, check=True)

    # 6. Upload to YouTube Shorts
    print("Uploading to YouTube...", flush=True)
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
            "categoryId": "22"
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

    print("Upload complete!", flush=True)

    # 7. Record as processed
    with open("processed.txt", "a") as f:
        f.write(f"{video_id}\n")

if __name__ == "__main__":
    main()
