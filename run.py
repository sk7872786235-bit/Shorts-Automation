import os
import json
import subprocess
import io
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google import genai

# Configuration
UPLOAD_FOLDER_ID = '1oAHvgUiNLV0uZHycYe_LV0iKKgiKh0SL'
PROCESSED_FOLDER_ID = '14MwLbBU0cx9-acCoQxDKHGl1eQ7IvYpy'

def get_drive_service():
    """Authenticates with Google Drive using the Service Account JSON"""
    service_account_info = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_JSON'])
    creds = Credentials.from_service_account_info(
        service_account_info, 
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def get_youtube_service():
    """Authenticates with YouTube using your existing Refresh Token secrets"""
    token_data = {
        'token': None,
        'refresh_token': os.environ['YT_REFRESH_TOKEN'],
        'client_id': os.environ['YT_CLIENT_ID'],
        'client_secret': os.environ['YT_CLIENT_SECRET'],
        'token_uri': 'https://oauth2.googleapis.com/token'
    }
    creds = UserCredentials.from_authorized_user_info(token_data)
    return build('youtube', 'v3', credentials=creds)

def main():
    drive_service = get_drive_service()
    
    # 1. Find the next video in the 'To Upload' folder
    results = drive_service.files().list(
        q=f"'{UPLOAD_FOLDER_ID}' in parents and mimeType contains 'video/'",
        spaces='drive',
        fields='files(id, name)',
        pageSize=1
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
            print(f"Download {int(status.progress() * 100)}%.")
            
    # 2. Ask Gemini 3.7 Flash for the peak moment
    print("Uploading to Gemini for analysis...")
    ai_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    gemini_file = ai_client.files.upload(file=local_filename)
    
    prompt = """
    Analyze this video and identify the single most engaging 30-to-60 second segment to use as a YouTube Short.
    Return ONLY a valid JSON object with these exact keys:
    - "start_time": (integer, start timestamp in seconds)
    - "end_time": (integer, end timestamp in seconds)
    - "title": (A viral, engaging title for the Short, max 60 characters)
    - "description": (A brief description with 3 relevant hashtags)
    """
    
    response = ai_client.models.generate_content(
        model="gemini-3.7-flash",
        contents=[gemini_file, prompt]
    )
    
    # Clean up the JSON response
    response_text = response.text.replace("```json", "").replace("```", "").strip()
    ai_data = json.loads(response_text)
    
    start_sec = ai_data["start_time"]
    end_sec = ai_data["end_time"]
    title = ai_data["title"]
    description = ai_data["description"]
    
    print(f"Gemini chose timestamps {start_sec}s to {end_sec}s")
    
    # 3. Use FFmpeg to cut and apply the blurred background (Option A)
    print("Editing video with FFmpeg...")
    final_video = "final_short.mp4"
    ffmpeg_filter = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "boxblur=luma_radius=20:luma_power=1[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    
    subprocess.run([
        "ffmpeg", "-y", 
        "-i", local_filename, 
        "-ss", str(start_sec), 
        "-to", str(end_sec), 
        "-lavfi", ffmpeg_filter, 
        "-c:a", "copy", 
        final_video
    ], check=True)
    
    # 4. Upload to YouTube
    print("Uploading to YouTube Shorts...")
    youtube_service = get_youtube_service()
    
    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': ['shorts', 'space', 'documentary'],
            'categoryId': '28' # Science & Technology
        },
        'status': {
            'privacyStatus': 'public',
            'selfDeclaredMadeForKids': False
        }
    }
    
    media = MediaFileUpload(final_video, chunksize=-1, resumable=True)
    request = youtube_service.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )
    response = request.execute()
    print(f"Successfully uploaded! Video ID: {response['id']}")
    
    # 5. Move the original video to 'Processed' in Google Drive
    print("Moving original video to Processed folder...")
    drive_service.files().update(
        fileId=video_id,
        addParents=PROCESSED_FOLDER_ID,
        removeParents=UPLOAD_FOLDER_ID
    ).execute()
    
    print("Automation complete.")

if __name__ == '__main__':
    main()
