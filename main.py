from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from db_history import insert_transcript, get_user_history, test_connection
import yt_dlp
import os
import uuid
from urllib.parse import urlparse
from pathlib import Path
import ipaddress
import socket

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
DOWNLOAD_DIR_PATH = Path(DOWNLOAD_DIR).resolve()

def is_ip_safe(ip):
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return False
    return True

def is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        if not is_ip_safe(ip):
            return False
    except ValueError:
        try:
            resolved_ips = socket.getaddrinfo(hostname, None)
            for result in resolved_ips:
                ip = ipaddress.ip_address(result[4][0])
                if not is_ip_safe(ip):
                    return False
        except:
            return False
    return True

@app.get("/")
def root():
    return {"ok": True, "service": "ClipForge AI Backend", "version": "3.1.0"}

@app.get("/health/db")
async def health_check_db():
    """Test database connectivity"""
    if test_connection():
        return {"status": "ok", "message": "Database connected"}
    else:
        return {"status": "error", "message": "Database connection failed"}

@app.get("/fetch")
def fetch(url: str = Query(...)):
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL")
    try:
        file_id = uuid.uuid4().hex
        filename = f"{file_id}.mp4"
        output_path = os.path.join(DOWNLOAD_DIR, filename)
        ydl_opts = {
            "outtmpl": output_path,
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        return {
            "success": True,
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "filename": filename,
            "download_url": f"/download/{filename}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{filename}")
def download(filename: str):
    path = DOWNLOAD_DIR_PATH / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=filename,
        headers={"Cache-Control": "no-cache"}
    )

@app.post("/transcribe")
async def transcribe_endpoint(
    file: UploadFile = File(None),
    user_id: str = Form(default="anonymous")
):
    """
    Main transcription endpoint that saves to database
    """
    try:
        # Get the filename
        source_name = file.filename if file else "unknown_file"
        
        print(f"📥 TRANSCRIBE REQUEST: {source_name}")
        print(f"   User ID: {user_id}")
        
        # YOUR EXISTING TRANSCRIPTION LOGIC GOES HERE
        # Replace this placeholder with your actual Whisper/transcription code
        transcript_text = "Placeholder transcript - replace with actual Whisper transcription"
        
        # If you have actual transcription logic, it would look something like:
        # transcript_text = your_whisper_function(file)
        
        # Save to database
        db_success = insert_transcript(
            user_id=user_id,
            source_name=source_name,
            transcript=transcript_text,
            duration=None,  # Add if you calculate duration
            preview_url=None,
            final_url=None
        )
        
        if db_success:
            print(f"✅ Transcript saved to database for user: {user_id}")
        else:
            print(f"⚠️ Failed to save transcript to database")
        
        return {
            "success": True,
            "transcript": transcript_text,
            "saved_to_db": db_success,
            "user_id": user_id,
            "source_name": source_name
        }
        
    except Exception as e:
        print(f"❌ Transcription error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/history/{user_id}")
async def get_history(user_id: str, limit: int = 50):
    """Retrieve user's transcript history"""
    try:
        history = get_user_history(user_id, limit)
        return {
            "success": True,
            "user_id": user_id,
            "count": len(history),
            "history": history
        }
    except Exception as e:
        print(f"❌ Error fetching history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/history/{record_id}")
async def delete_history_record(record_id: str):
    """Delete a specific history record"""
    from db_history import get_db
    
    try:
        db = get_db()
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")
        
        res = db.table("history").delete().eq("id", record_id).execute()
        
        if res.data:
            return {"success": True, "message": "Record deleted", "id": record_id}
        else:
            raise HTTPException(status_code=404, detail="Record not found")
            
    except Exception as e:
        print(f"❌ Delete error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/test/insert")
async def test_insert(user_id: str = Query(default="@ClippedBySal")):
    """Test endpoint to verify database insert works"""
    print(f"🧪 TEST INSERT for user: {user_id}")
    
    success = insert_transcript(
        user_id=user_id,
        source_name="Flashlight Fear An Encounter with the Occult.mp4",
        transcript="This is a test transcript. Effect, accumulated fear. He freezes in fear. Mysterious footsteps approaching from behind.",
        duration=120.5,
        preview_url="https://example.com/preview.mp4",
        final_url="https://example.com/final.mp4"
    )
    
    if success:
        return {"success": True, "message": f"Test record inserted for {user_id}"}
    else:
        raise HTTPException(status_code=500, detail="Failed to insert test record")

app.mount("/static", StaticFiles(directory="static"), name="static")
```

**Now test it:**

1. **First, test the database connection:**
```
   https://clipper-api-final-1.onrender.com/health/db
```

2. **Insert a test record:**
```
   https://clipper-api-final-1.onrender.com/test/insert?user_id=@ClippedBySal
```

3. **Check if it saved:**
```
   https://clipper-api-final-1.onrender.com/history/@ClippedBySal
