import os, sys, json, subprocess, requests, time
import yt_dlp
from google import genai
from google.genai import types
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

def download_media(video_id, output_filename, is_audio=False):
    """Downloads media natively using yt-dlp with client disguises, plus an API fallback."""
    print(f"Downloading via yt-dlp...", flush=True)
    
    ydl_opts = {
        'format': 'bestaudio/best' if is_audio else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_filename.replace('.mp3', '') if is_audio else output_filename,
        'quiet': False,
        'no_warnings': True,
        # THE FIX: Disguise the scraper to bypass the Android VR bot-blocker
        'extractor_args': {'youtube': {'player_client': ['ios', 'web']}}
    }
    
    if is_audio:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
        }]
    else:
        ydl_opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            
        if os.path.exists(output_filename):
            print(f"✅ Native download complete!", flush=True)
            return
        else:
            raise Exception("File not found after download.")
            
    except Exception as e:
        print(f"🚨 yt-dlp failed: {e}", flush=True)
        print(f"⚠️ Native download blocked by YouTube IP filters. Engaging API fallback...", flush=True)

    # --- FALLBACK: Cobalt API ---
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    instances = ["https://api.cobalt.tools", "https://api.cobalt.my.id", "https://cobalt.mrcyjanek.net"]
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    
    for base_url in instances:
        try:
            payload = {
                "url": yt_url,
                "downloadMode": "audio" if is_audio else "auto",
            }
            if is_audio:
                payload["audioFormat"] = "mp3"
            else:
                payload["videoQuality"] = "1080"
                
            response = requests.post(f"{base_url}/", json=payload, headers=headers, timeout=15)
            if response.status_code != 200:
                continue
                
            download_url = response.json().get("url")
            if not download_url:
                continue
                
            stream_response = requests.get(download_url, stream=True, timeout=30)
            with open(output_filename, 'wb') as f:
                for chunk in stream_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            if os.path.getsize(output_filename) > 100000:
                print(f"✅ Fallback download successful!", flush=True)
                return
                
        except Exception:
            continue
            
    print("🚨 All download methods failed. YouTube is severely rate-limiting this server.", flush=True)
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
    
    # Every channel has an invisible "Uploads" playlist using 'UU' instead of 'UC'
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

    # 1. Download AUDIO natively via yt-dlp
    print("\n--- FETCHING AUDIO ---", flush=True)
    download_media(video_id, "audio.mp3", is_audio=True)

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

    # 3. Download the FULL video natively via yt-dlp
    print("\n--- FETCHING VIDEO ---", flush=True)
    download_media(video_id, "full_video.mp4", is_audio=False)

    # 4. Crop to 9:16 vertical
    print("\n--- CROPPING VIDEO ---", flush=True)
    print(f"Cropping to 9:16 vertical...", flush=True)
    crop_cmd = f'ffmpeg -ss {start} -i full_video.mp4 -t {duration} -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v libx264 -preset fast -crf 22 -c:a aac output.mp4 -y'
    subprocess.run(crop_cmd, shell=True, check=True)

    # 5. Upload to YouTube Shorts
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
