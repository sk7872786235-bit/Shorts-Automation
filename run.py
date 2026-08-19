import os, sys, json, subprocess, requests, time
from google import genai
from google.genai import types
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def get_cobalt_instances():
    """Fetches active Cobalt proxy servers to bypass YouTube IP blocks."""
    print("Fetching fresh list of Cobalt proxies...", flush=True)
    hardcoded_fallbacks = [
        "https://api.cobalt.tools",
        "https://api.cobalt.my.id",
        "https://cobalt-api.kwiatechu.com",
        "https://co.eepy.today",
        "https://cobalt.mrcyjanek.net",
        "https://cobalt.c.rest",
        "https://api.only-dank.com"
    ]
    try:
        res = requests.get("https://instances.hyper.lol/instances.json", timeout=10)
        if res.status_code == 200:
            data = res.json()
            fetched = [inst["api"] for inst in data if inst.get("api", "").startswith("http")]
            if fetched:
                return fetched + [h for h in hardcoded_fallbacks if h not in fetched]
    except Exception as e:
        print(f"Failed to fetch dynamic list, relying on hardcoded fallbacks: {e}", flush=True)
        
    return hardcoded_fallbacks

def download_with_cobalt(video_id, output_filename, is_audio=False):
    """Downloads media securely using the Cobalt API proxy network."""
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    instances = get_cobalt_instances()
    
    for base_url in instances:
        print(f"Trying Cobalt API proxy: {base_url}...", flush=True)
        try:
            payload = {
                "url": yt_url,
                "downloadMode": "audio" if is_audio else "auto"
            }
            if is_audio:
                payload["audioFormat"] = "mp3"
            else:
                payload["videoQuality"] = "1080"
                
            response = requests.post(f"{base_url}/", json=payload, headers=headers, timeout=15)
            
            if response.status_code != 200:
                print(f"❌ Server returned {response.status_code}. Trying next...", flush=True)
                continue
                
            download_url = response.json().get("url")
            if not download_url:
                print("❌ No valid download URL found. Trying next...", flush=True)
                continue
                
            stream_response = requests.get(download_url, stream=True, timeout=30)
            with open(output_filename, 'wb') as f:
                for chunk in stream_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            # Safety check: Ensure the file isn't an empty/corrupted 1KB HTML error page
            if os.path.exists(output_filename) and os.path.getsize(output_filename) > 50000:
                print(f"✅ Download successful via {base_url}!", flush=True)
                return
            else:
                print("❌ File size too small (corrupted). Trying next...", flush=True)
                
        except Exception as e:
            print(f"❌ Connection error: {e}. Trying next...", flush=True)
            
    print("🚨 All Cobalt proxies failed. Waiting for next cron schedule to retry.", flush=True)
    sys.exit(1)

def main():
    channel_id = os.environ.get("YT_CHANNEL_ID")
    
    if not channel_id or not channel_id.startswith("UC"):
        print("🚨 ERROR: YT_CHANNEL_ID is missing or invalid! It must start with 'UC'.", flush=True)
        sys.exit(1)
        
    print("Authenticating with YouTube API...", flush=True)
    
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"].strip(),
        client_secret=os.environ["YT_CLIENT_SECRET"].strip()
    )
    youtube = build("youtube", "v3", credentials=creds)
    
    print("Fetching latest videos via official API...", flush=True)
    
    # Bypass RSS bot blockers by reading the invisible "Uploads" playlist
    uploads_playlist_id = "UU" + channel_id[2:]
    
    try:
        playlist_response = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=10
        ).execute()
    except Exception as e:
        print(f"🚨 Failed to fetch videos via API. Check your OAuth credentials! Error: {e}", flush=True)
        sys.exit(1)
        
    entries = playlist_response.get("items", [])
    
    if not entries:
        print("🚨 No videos found in the uploads playlist.", flush=True)
        sys.exit(0)

    if not os.path.exists("processed.txt"):
        open("processed.txt", "w").close()

    with open("processed.txt", "r") as f:
        processed_videos = f.read()

    valid_video = None
    for entry in entries:
        vid_id = entry["snippet"]["resourceId"]["videoId"]
        if vid_id not in processed_videos:
            valid_video = entry
            break 
            
    if not valid_video:
        print("All recent videos have already been processed. Waiting for new uploads.", flush=True)
        sys.exit(0)

    video_id = valid_video["snippet"]["resourceId"]["videoId"]
    video_title = valid_video["snippet"]["title"]

    print(f"Processing Kids Video: {video_title} ({video_id})", flush=True)

    # 1. Download AUDIO via Cobalt Proxy Network
    print("\n--- FETCHING AUDIO ---", flush=True)
    download_with_cobalt(video_id, "audio.mp3", is_audio=True)

    # 2. Upload Audio to Gemini
    print("\n--- AI ANALYSIS ---", flush=True)
    print("Uploading audio to Gemini...", flush=True)
    
    api_key = os.environ["GEMINI_API_KEY"].strip()
    client = genai.Client(api_key=api_key)
    
    audio_file = client.files.upload(
        file="audio.mp3", 
        config={'mime_type': 'audio/mp3'}
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
        mime_type="audio/mp3"
    )
    
    # Using the correct 3.6-flash model we established yesterday
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

    # 3. Download the FULL video via Cobalt Proxy Network
    print("\n--- FETCHING VIDEO ---", flush=True)
    download_with_cobalt(video_id, "full_video.mp4", is_audio=False)

    # 4. Crop to 9:16 vertical
    print("\n--- CROPPING VIDEO ---", flush=True)
    print(f"Cropping to 9:16 vertical...", flush=True)
    crop_cmd = f'ffmpeg -ss {start} -i full_video.mp4 -t {duration} -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v libx264 -preset fast -crf 22 -c:a aac output.mp4 -y'
    subprocess.run(crop_cmd, shell=True, check=True)

    # 5. Upload to YouTube Shorts via API
    print("\n--- UPLOADING SHORT ---", flush=True)
    
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
