import os
import json
import subprocess
import io
import time
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google import genai
from google.genai import types

# Configuration
UPLOAD_FOLDER_ID = '1oAHvgUiNLV0uZHycYe_LV0iKKgiKh0SL'
PROCESSED_FOLDER_ID = '14MwLbBU0cx9-acCoQxDKHGl1eQ7IvYpy'

def get_drive_service():
    service_account_info = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_JSON'])
    creds = Credentials.from_service_account_info(
        service_account_info, scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def get_youtube_service():
    token_data = {
        'token': None,
        'refresh_token': os.environ['YT_REFRESH_TOKEN'],
        'client_id': os.environ['YT_CLIENT_ID'],
        'client_secret': os.environ['YT_CLIENT_SECRET'],
        'token_uri': 'https://oauth2.googleapis.com/token'
    }
    creds = UserCredentials.from_authorized_user_info(token_data)
    return build('youtube', 'v3', credentials=creds)

def ensure_string(val):
    return "".join(val) if isinstance(val, list) else str(val)

def ensure_list(val):
    if isinstance(val, list): return val
    return [t.strip() for t in str(val).split(",")]

def main():
    drive_service = get_drive_service()
    
    # 1. Find Video
    results = drive_service.files().list(
        q=f"'{UPLOAD_FOLDER_ID}' in parents and mimeType contains 'video/'",
        spaces='drive', fields='files(id, name)', pageSize=1
    ).execute()
    
    items = results.get('files', [])
    if not items:
        print("No videos found in the 'To Upload' folder. Exiting.")
        return
        
    video_id = items[0]['id']
    video_name = items[0]['name']
    local_filename = "temp_long_video.mp4"
    
    print(f"Downloading {video_name}...")
    request = drive_service.files().get_media(fileId=video_id)
    with io.FileIO(local_filename, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            
    # 2. Ask Gemini for 4 Pro-Level Segments
    print("Uploading to Gemini for Master Production Planning...")
    ai_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    gemini_file = ai_client.files.upload(file=local_filename)
    
    prompt = """
    Act as an expert YouTube Shorts producer for a kids entertainment channel named 'Bonza Kids'.
    Analyze this video and identify EXACTLY 4 completely unique, non-overlapping 30-to-60 second segments.
    It is critical that the visual footage in each segment does not overlap with any other segment.
    Determine if the spoken language/context is primarily Hindi or English.
    
    Return ONLY a valid JSON object containing a single key "shorts" mapped to an array of exactly 4 objects.
    Each of the 4 objects MUST contain these exact keys:
    - "start_time": (integer, start timestamp in seconds)
    - "end_time": (integer, end timestamp in seconds)
    - "thumbnail_time": (integer, timestamp of the best frame to use for the thumbnail, must be within the start/end time)
    - "hero_text": (Short, catchy text for the top of the video, max 25 chars)
    - "title": (A viral title for the YouTube upload)
    - "description": (Write an extremely vast, long, and detailed description of the video. Include a full paragraph explaining what happens, why kids will love it, and include at least 15 highly relevant hashtags spread throughout the text.)
    - "tags": (List of at least 25 string tags covering every possible keyword related to the video, title, and description)
    - "language": (Return exact string "hi" for Hindi or "en" for English)
    """
    
    max_retries = 3
    response = None
    for attempt in range(max_retries):
        try:
            # UPGRADED: Using the generous Gemini 3.5 Flash model (1,500 requests per day)
            response = ai_client.models.generate_content(
                model="gemini-3.5-flash",
                contents=[gemini_file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            break
        except Exception as e:
            error_msg = str(e)
            print(f"API Error on attempt {attempt + 1}: {error_msg}")
            
            if "429" in error_msg or "Quota exceeded" in error_msg:
                print("\n🚨 URGENT: Gemini Free Tier API Limit Reached.")
                print("🚨 The script will exit now. Please wait 24 hours for your quota to reset, or upgrade your Google Cloud billing.")
                return
                
            if attempt < max_retries - 1:
                print("Gemini server is busy. Waiting 30 seconds before retrying...")
                time.sleep(30)
            else:
                print("Max retries reached. Exiting script so GitHub can try again tomorrow.")
                return

    if not response:
        return
    
    response_text = response.text.strip()
    ai_data = json.loads(response_text)
    shorts_list = ai_data.get("shorts", [])
    
    if not shorts_list or len(shorts_list) == 0:
        print("AI did not return any shorts. Exiting.")
        return
        
    print(f"\nGemini successfully planned {len(shorts_list)} unique shorts. Processing sequentially...")
    youtube_service = get_youtube_service()

    # 3. Process each Short sequentially
    for idx, short in enumerate(shorts_list):
        print(f"\n--- Processing Short {idx + 1} of {len(shorts_list)} ---")
        
        safe_hero = ensure_string(short.get("hero_text", "NEW SHORT"))
        safe_title = ensure_string(short.get("title", f"Bonza Kids Special Part {idx+1}!"))
        safe_desc = ensure_string(short.get("description", "Enjoy this short! #bonzakids"))
        safe_tags = ensure_list(short.get("tags", ["shorts", "kids"]))
        safe_lang = ensure_string(short.get("language", "en")).lower()
        
        with open("hero.txt", "w", encoding="utf-8") as f: f.write(safe_hero)
        with open("sub.txt", "w", encoding="utf-8") as f: f.write("Subscribe to Bonza Kids")
        
        final_video = f"final_short_{idx}.mp4"
        thumbnail_file = f"thumbnail_{idx}.jpg"
        
        print(f"Cutting Segment {idx + 1}: {short.get('start_time')}s to {short.get('end_time')}s...")
        font_standard = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        
        ffmpeg_video_filter = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black[bg];"
            f"[bg]drawtext=textfile='hero.txt':fontfile='{font_standard}':fontcolor=white:fontsize=70:x=(w-text_w)/2:y=400[t1];"
            f"[t1]drawtext=textfile='sub.txt':fontfile='{font_standard}':fontcolor=#FFD700:fontsize=70:x=(w-text_w)/2:y=1450"
        )
        
        subprocess.run([
            "ffmpeg", "-y", "-i", local_filename, 
            "-ss", str(short.get("start_time")), "-to", str(short.get("end_time")), 
            "-lavfi", ffmpeg_video_filter, "-c:a", "copy", final_video
        ], check=True)

        print(f"Generating custom thumbnail {idx + 1} (No text, raw 9:16)...")
        ffmpeg_thumb_filter = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(short.get("thumbnail_time")), "-i", local_filename,
            "-vframes", "1", "-vf", ffmpeg_thumb_filter, thumbnail_file
        ], check=True)
        
        print(f"Uploading Short {idx + 1} to YouTube...")
        body = {
            'snippet': {
                'title': safe_title,
                'description': safe_desc,
                'tags': safe_tags,
                'categoryId': '24', 
                'defaultLanguage': safe_lang,
                'defaultAudioLanguage': safe_lang
            },
            'status': {
                'privacyStatus': 'public',
                'selfDeclaredMadeForKids': True,
                # Forcing YouTube's "AI Use" toggle to NO
                'selfDeclaredAlteredContent': False,
                'selfDeclaredSyntheticMedia': False 
            }
        }
        
        media = MediaFileUpload(final_video, chunksize=-1, resumable=True)
        insert_req = youtube_service.videos().insert(
            part=','.join(body.keys()), body=body, media_body=media
        )
        video_response = insert_req.execute()
        new_video_id = video_response['id']
        print(f"Short {idx + 1} uploaded successfully! ID: {new_video_id}")
        
        print(f"Uploading thumbnail for Short {idx + 1}...")
        youtube_service.thumbnails().set(
            videoId=new_video_id,
            media_body=MediaFileUpload(thumbnail_file)
        ).execute()

    # 4. Move original video to Processed ONLY after all 4 shorts are successfully live
    print("\nAll 4 Shorts generated and uploaded! Moving original video to Processed folder...")
    drive_service.files().update(
        fileId=video_id, addParents=PROCESSED_FOLDER_ID, removeParents=UPLOAD_FOLDER_ID
    ).execute()
    
    print("Production Studio Automation Perfectly Executed.")

if __name__ == '__main__':
    main()
