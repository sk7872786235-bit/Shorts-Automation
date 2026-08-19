import os, sys, json, feedparser, subprocess, requests, time
import google.generativeai as genai
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
    
    # Strip whitespace to prevent the previous "API key not valid" crash
    api_key = os.environ["GEMINI_API_KEY"].strip()
    genai.configure(api_key=api_key)
    
    # The stable SDK infers mime_types naturally
    audio_file = genai.upload_file("audio.m4a")
    
    print("Waiting for Google's servers to process the audio track...", flush=True)
    while audio_file.state.name == "PROCESSING":
        print(".", end="", flush=True)
        time.sleep(2)
        audio_file = genai.get_file(audio_file.name)
        
    if audio_file.state.name == "FAILED":
        print("\n❌ Gemini failed to process audio.", flush=True)
        sys.exit(1)
        
    print("\n✅ Audio ready!", flush=True)
    
    prompt = """
    Listen to this audio track from a kids' YouTube video. 
    Find the most engaging, catchy 30 to 50 second segment (like the chorus of a song).
    Return ONLY a valid JSON object with the exact start and end time in seconds. No formatting.
    Example: {"start": 12, "end": 45}
    """
    
    print("Analyzing audio to find the best viral hook...", flush=True)
    
    # Using the rock-solid 1.5 flash model
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    response = model.generate_content(
        [audio_file, prompt],
        generation_config=genai.GenerationConfig(response_mime_type="application/json")
    )
    
    clean_json = response.text.strip().replace("```json", "").replace("
