import os, sys, json, feedparser, subprocess, urllib.request, time
from google import genai
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def download_via_proxy(video_url, output_filename, is_audio=False):
    """Bypasses YouTube's IP block by using a free proxy API."""
    print(f"Requesting download from proxy: {video_url}...", flush=True)
    
    # Using a public instance of Cobalt (a free, open-source media downloader)
    api_url = "https://co.wuk.sh/api/json" 
    
    payload = json.dumps({
        "url": video_url,
        "isAudioOnly": is_audio,
        "aFormat": "mp3" if is_audio else "best",
        "vQuality": "1080",
    }).encode('utf-8')
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    req = urllib.request.Request(api_url, data=payload, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            if data.get("status") == "error":
                print(f"Proxy error: {data.get('text')}", flush=True)
                sys.exit(1)
                
            download_url = data.get("url")
            
            print(f"Downloading file to {output_filename}...", flush=True)
            urllib.request.urlretrieve(download_url, output_filename)
            print("Download successful!", flush=True)
            
    except Exception as e:
        print(f"Failed to download via proxy: {e}", flush=True)
        sys.exit(1)

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
            break 
            
    if not valid_video:
        print("All recent videos have already been processed. Waiting for new uploads.", flush=True)
        sys.exit(0)

    video_id = valid_video.yt_videoid
    video_title = valid_video.title
    yt_url = f"https://www.youtube.com/watch?v={video_id}"

    print(f"Processing Kids Video: {video_title} ({video_id})", flush=True)

    # 3. Download AUDIO ONLY using Proxy
    download_via_proxy(yt_url, "audio.mp3", is_audio=True)

    # 4. Upload Audio to Gemini 1.5 Flash
    print("Uploading audio to Gemini for timestamp extraction...", flush=True)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    
    audio_file = client.files.upload(file="audio.mp3")
    
    prompt = """
    Listen to this audio track from a kids' YouTube video. 
    Find the most engaging, catchy 30 to 50 second segment (like the chorus of a song, or the most energetic part).
    Return ONLY a valid JSON object with the exact start and end time in seconds. No formatting, no markdown blocks.
    Example: {"start": 12, "end": 45}
    """
    
    print("Analyzing audio (this might take a few seconds)...", flush=True)
    
    # Add a small delay to ensure Google's servers process the uploaded file
    time.sleep(10) 
    
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=[audio_file, prompt]
    )
    
    clean_json = response.text.strip().replace("```json", "").replace("```", "")
    timestamps = json.loads(clean_json)
    
    start = int(timestamps["start"])
    duration = int(timestamps["end"]) - start
    print(f"Gemini selected the catchiest part: Start {start}s, Duration {duration}s", flush=True)

    # 5. Download the FULL video using Proxy
    print("Downloading full video for cropping...", flush=True)
    download_via_proxy(yt_url, "full_video.mp4", is_audio=False)

    # Crop to 9:16 vertical using FFmpeg based on Gemini's timestamps
    print(f"Cropping to 9:16 vertical (Start: {start}s, Duration: {duration}s)...", flush=True)
    crop_cmd = f'ffmpeg -ss {start} -i full_video.mp4 -t {duration} -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v libx264 -preset fast -crf 22 -c:a aac output.mp4 -y'
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
            "selfDeclaredMadeForKids": True
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

    # Cleanup temp files
    try:
        client.files.delete(name=audio_file.name)
    except:
        pass

if __name__ == "__main__":
    main()
