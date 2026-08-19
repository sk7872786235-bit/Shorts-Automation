import os, sys, json, feedparser, subprocess, requests, time
from google import genai
from google.genai import types
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def download_via_piped(video_id, output_filename, is_audio=False):
    """Uses the highly resilient Piped API network to proxy downloads, bypassing YouTube blocks."""
    instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.tokhmi.xyz",
        "https://pipedapi.syncpundit.io",
        "https://piapi.ggtyler.dev",
        "https://pipedapi.smnz.de",
        "https://piped-api.lunar.icu"
    ]
    
    for instance in instances:
        print(f"Trying Piped API proxy: {instance}...", flush=True)
        try:
            url = f"{instance}/streams/{video_id}"
            response = requests.get(url, timeout=15)
            
            if response.status_code != 200:
                print(f"❌ Server returned {response.status_code}. Trying next...", flush=True)
                continue
                
            data = response.json()
            if "error" in data:
                print(f"❌ API returned error: {data['error']}. Trying next...", flush=True)
                continue
                
            download_url = None
            if is_audio:
                # Target the highest quality M4A audio stream
                for stream in data.get("audioStreams", []):
                    if stream.get("format") == "M4A":
                        download_url = stream.get("url")
                        break
                # Fallback to the first available audio track if M4A is missing
                if not download_url and data.get("audioStreams"):
                    download_url = data["audioStreams"][0].get("url")
            else:
                # Target a combined Video+Audio stream (MPEG_4 / MP4)
                for stream in data.get("videoStreams", []):
                    if not stream.get("videoOnly") and stream.get("format") == "MPEG_4":
                        download_url = stream.get("url")
                        break
                # Fallback if no combined MPEG_4 is found
                if not download_url:
                    for stream in data.get("videoStreams", []):
                        if not stream.get("videoOnly"):
                            download_url = stream.get("url")
                            break
                            
            if not download_url:
                print("❌ No compatible stream found on this instance. Trying next...", flush=True)
                continue
                
            print(f"Connecting to proxy stream...", flush=True)
            stream_response = requests.get(download_url, stream=True, timeout=30)
            
            if stream_response.status_code == 200:
                with open(output_filename, 'wb') as f:
                    for chunk in stream_response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            
                # Safety check against corrupted or empty files
                if os.path.getsize(output_filename) < 100000:
                    print("❌ Downloaded file is suspiciously small. Trying next...", flush=True)
                    continue
                    
                print(f"✅ Successfully downloaded to {output_filename}!", flush=True)
                return 
            else:
                print(f"❌ Stream proxy returned {stream_response.status_code}. Trying next...", flush=True)
                
        except Exception as e:
            print(f"❌ Connection error: {e}. Trying next...", flush=True)
            continue
            
    print("🚨 All Piped proxies failed. YouTube might be blocking datacenters. Waiting for retry.", flush=True)
    sys.exit(1)

def main():
    channel_id = os.environ.get("YT_CHANNEL_ID")
    
    if not channel_id:
        print("🚨 ERROR: YT_CHANNEL_ID is missing from your GitHub Secrets!", flush=True)
        sys.exit(1)
        
    print(f"Fetching RSS feed for channel: {channel_id}", flush=True)
    
    feedparser.USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id.strip()}"
    feed = feedparser.parse(feed_url)
    
    if not feed.entries:
        print("🚨 No videos found! Ensure your YT_CHANNEL_ID starts with 'UC'.", flush=True)
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

    # 1. Download AUDIO natively as M4A (saved as MP4 to bypass Linux metadata quirks)
    print("\n--- FETCHING AUDIO ---", flush=True)
    download_via_piped(video_id, "audio.mp4", is_audio=True)

    # 2. Upload Audio to Gemini
    print("\n--- AI ANALYSIS ---", flush=True)
    print("Uploading audio to Gemini...", flush=True)
    
    api_key = os.environ["GEMINI_API_KEY"].strip()
    client = genai.Client(api_key=api_key)
    
    audio_file = client.files.upload(
        file="audio.mp4", 
        config={'mime_type': 'audio/mp4'}
    )
    
    print("Waiting for Google's servers to process the audio track...", flush=True)
    while True:
        audio_file = client.files.get(name=audio_file.name)
        state_str = str(getattr(audio_file, 'state', ''))
        
        if "PROCESSING" in state_str:
            print(".", end="", flush=True)
            time.sleep(3)
        elif "FAILED" in state_str:
            print("\n❌ Gemini failed to process audio.", flush=True)
            sys.exit(1)
        else:
            print("\n✅ Audio ready!", flush=True)
            break
    
    prompt = """
    Listen to this audio track from a kids' YouTube video. 
    Find the most engaging, catchy 30 to 50 second segment (like the chorus of a song).
    Return ONLY a valid JSON object with the exact start and end time in seconds. No formatting.
    Example: {"start": 12, "end": 45}
    """
    
    print("Analyzing audio to find the best viral hook...", flush=True)
    
    audio_part = types.Part.from_uri(
        file_uri=audio_file.uri, 
        mime_type="audio/mp4"
    )
    
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[audio_part, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )
    
    clean_json = response.text.strip().replace("```json", "").replace("```", "")
    timestamps = json.loads(clean_json)
    
    start = int(timestamps["start"])
    duration = int(timestamps["end"]) - start
    print(f"🎯 Gemini selected: Start {start}s, Duration {duration}s", flush=True)

    # 3. Download the FULL video
    print("\n--- FETCHING VIDEO ---", flush=True)
    download_via_piped(video_id, "full_video.mp4", is_audio=False)

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
