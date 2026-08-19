import os, sys, json, feedparser, subprocess, requests, time
from google import genai
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def download_via_invidious(video_id, output_filename, is_audio=False):
    """Uses decentralized Invidious servers to proxy the download, bypassing GitHub IP blocks for free."""
    instances = [
        "https://vid.puffyan.us",
        "https://invidious.protokolla.fi",
        "https://inv.tux.pizza",
        "https://invidious.incogniweb.net"
    ]
    
    itag = "140" if is_audio else "22"
    
    for instance in instances:
        print(f"Trying decentralized proxy: {instance}...", flush=True)
        try:
            url = f"{instance}/latest_version?id={video_id}&itag={itag}&local=true"
            response = requests.get(url, stream=True, timeout=20)
            
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
                return 
            else:
                print(f"❌ Server returned status {response.status_code}. Trying next...", flush=True)
                
        except Exception as e:
            print(f"❌ Server timeout or error: {e}. Trying next...", flush=True)
            continue
            
    print("🚨 All free proxies failed. Waiting for next cron schedule to retry.", flush=True)
    sys.exit(1)

def main():
    channel_id = os.environ.get("YT_CHANNEL_ID")
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)
    
    if not feed.entries:
        print("No videos found on channel.", flush=True)
        sys.exit(0)

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

    # 1. Download AUDIO
    print("\n--- FETCHING AUDIO ---", flush=True)
    download_via_invidious(video_id, "audio.m4a", is_audio=True)

    # 2. Upload Audio to Gemini
    print("\n--- AI ANALYSIS ---", flush=True)
    print("Uploading audio to Gemini...", flush=True)
    
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    
    # We let the SDK natively detect the format without forcing an override
    audio_file = client.files.upload(file="audio.m4a")
    
    print("Waiting for Google's servers to process the audio track...", flush=True)
    
    while True:
        audio_file = client.files.get(name=audio_file.name)
        state_str = str(getattr(audio_file, 'state', ''))
        
        if "PROCESSING" in state_str:
            print(".", end="", flush=True)
            time.sleep(3)
        elif "FAILED" in state_str:
            print("\n❌ Gemini failed to process the audio file.", flush=True)
            sys.exit(1)
        else:
            print("\n✅ Audio ready!", flush=True)
            break
    
    prompt = """
    Listen to this audio track from a kids' YouTube video. 
    Find the most engaging, catchy 30 to 50 second segment (like the chorus of a song).
    Return ONLY a valid JSON object with the exact start and end time in seconds. No formatting, no markdown blocks.
    Example: {"start": 12, "end": 45}
    """
    
    print("Analyzing audio to find the best viral hook...", flush=True)
    
    # Removed the strict JSON config argument that was causing the 400 crash
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[audio_file, prompt]
    )
    
    clean_json = response.text.strip().replace("```json", "").replace("```", "")
    timestamps = json.loads(clean_json)
    
    start = int(timestamps["start"])
    duration = int(timestamps["end"]) - start
    print(f"🎯 Gemini selected: Start {start}s, Duration {duration}s", flush=True)

    # 3. Download the FULL video
    print("\n--- FETCHING VIDEO ---", flush=True)
    download_via_invidious(video_id, "full_video.mp4", is_audio=False)

    # 4. Crop to 9:16 vertical
    print("\n--- CROPPING VIDEO ---", flush=True)
    print(f"Cropping to 9:16 vertical...", flush=True)
    crop_cmd = f'ffmpeg -ss {start} -i full_video.mp4 -t {duration} -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v libx264 -preset fast -crf 22 -c:a aac output.mp4 -y'
    subprocess.run(crop_cmd, shell=True, check=True)

    # 5. Upload to YouTube Shorts
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
            "selfDeclaredMadeForKids": True
        }
    }
    
    youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload("output.mp4", mimetype="video/mp4")
    ).execute()

    print("✅ Upload complete!", flush=True)

    with open("processed.txt", "a") as f:
        f.write(f"{video_id}\n")

    try:
        client.files.delete(name=audio_file.name)
    except:
        pass

if __name__ == "__main__":
    main()
