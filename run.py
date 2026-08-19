import os, sys, json, feedparser, subprocess, requests, time
from google import genai
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def download_via_invidious(video_id, output_filename, is_audio=False):
    """Uses decentralized Invidious servers to proxy the download, bypassing GitHub IP blocks for free."""
    
    # A list of reliable, public Invidious instances
    instances = [
        "https://vid.puffyan.us",
        "https://invidious.protokolla.fi",
        "https://inv.tux.pizza",
        "https://invidious.incogniweb.net"
    ]
    
    # YouTube itags: 140 = Audio Only (m4a), 22 = 720p Video+Audio (mp4), 18 = 360p Video+Audio (mp4)
    itag = "140" if is_audio else "22"
    
    for instance in instances:
        print(f"Trying decentralized proxy: {instance}...", flush=True)
        try:
            # &local=true forces the Invidious server to download it and pass it to us (hides our IP)
            url = f"{instance}/latest_version?id={video_id}&itag={itag}&local=true"
            response = requests.get(url, stream=True, timeout=20)
            
            # If 720p video isn't available, gracefully fall back to 360p
            if response.status_code == 404 and not is_audio:
                print("720p not found, falling back to 360p...", flush=True)
                url = f"{instance}/latest_version?id={video_id}&itag=18&local=true"
                response = requests.get(url, stream=True, timeout=20)
                
            if response.status_code == 200:
                with open(output_filename, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                print(f"✅ Successfully downloaded to {output_filename}!", flush=True)
                return # Success! Exit the download loop
            else:
                print(f"❌ Server returned status {response.status_code}. Trying next...", flush=True)
                
        except Exception as e:
            print(f"❌ Server timeout or error: {e}. Trying next...", flush=True)
            continue
            
    print("🚨 All free proxies failed. Waiting for next cron schedule to retry.", flush=True)
    sys.exit(1)

def main():
    # 1. Fetch Latest Video from RSS
    channel_id = os.environ.get("YT_CHANNEL_ID")
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)
    
    if not feed.entries:
        print("No videos found on channel.", flush=True)
        sys.exit(0)

    # 2. Check processed state
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

    print(f"Processing Kids Video: {video_title} ({video_id})", flush=True)

    # 3. Download AUDIO ONLY using Invidious
    print("\n--- FETCHING AUDIO ---", flush=True)
    download_via_invidious(video_id, "audio.m4a", is_audio=True)

    # 4. Upload Audio to Gemini 1.5 Flash
    print("\n--- AI ANALYSIS ---", flush=True)
    print("Uploading audio to Gemini for timestamp extraction...", flush=True)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    audio_file = client.files.upload(file="audio.m4a")
    
    prompt = """
    Listen to this audio track from a kids' YouTube video. 
    Find the most engaging, catchy 30 to 50 second segment (like the chorus of a song).
    Return ONLY a valid JSON object with the exact start and end time in seconds. No formatting.
    Example: {"start": 12, "end": 45}
    """
    
    print("Analyzing audio (this takes ~10 seconds)...", flush=True)
    time.sleep(10) # Give Google's servers a moment to process the uploaded file
    
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=[audio_file, prompt]
    )
    
    clean_json = response.text.strip().replace("```json", "").replace("```", "")
    timestamps = json.loads(clean_json)
    
    start = int(timestamps["start"])
    duration = int(timestamps["end"]) - start
    print(f"🎯 Gemini selected: Start {start}s, Duration {duration}s", flush=True)

    # 5. Download the FULL video using Invidious
    print("\n--- FETCHING VIDEO ---", flush=True)
    download_via_invidious(video_id, "full_video.mp4", is_audio=False)

    # 6. Crop to 9:16 vertical using FFmpeg based on Gemini's timestamps
    print("\n--- CROPPING VIDEO ---", flush=True)
    print(f"Cropping to 9:16 vertical...", flush=True)
    crop_cmd = f'ffmpeg -ss {start} -i full_video.mp4 -t {duration} -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v libx264 -preset fast -crf 22 -c:a aac output.mp4 -y'
    subprocess.run(crop_cmd, shell=True, check=True)

    # 7. Upload to YouTube Shorts (Kids Compliant)
    print("\n--- UPLOADING SHORT ---", flush=True)
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
            "selfDeclaredMadeForKids": True # COPPA COMPLIANT
        }
    }
    
    youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload("output.mp4", mimetype="video/mp4")
    ).execute()

    print("✅ Upload complete!", flush=True)

    # 8. Clean up and Save State
    with open("processed.txt", "a") as f:
        f.write(f"{video_id}\n")

    try:
        client.files.delete(name=audio_file.name)
    except:
        pass

if __name__ == "__main__":
    main()
