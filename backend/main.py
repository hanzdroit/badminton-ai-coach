import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

APP_VERSION = "1.8-upload-pipeline"
UPLOAD_LIMIT_MB = int(os.getenv("UPLOAD_LIMIT_MB", "25"))
UPLOAD_LIMIT_BYTES = UPLOAD_LIMIT_MB * 1024 * 1024
TEMP_ROOT = Path(tempfile.gettempdir()) / "badminton-ai-coach"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
JOBS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="Badminton AI Coach API", version=APP_VERSION)
origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if origins == ["*"] else origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.get("/")
def root():
    return {"app": "Badminton AI Coach API", "version": APP_VERSION, "status": "online"}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "api_ready": True,
        "upload_ready": True,
        "tracknet_ready": False,
        "inpaintnet_ready": False,
        "upload_limit_mb": UPLOAD_LIMIT_MB,
        "message": "API and temporary video upload are ready. TrackNetV3 inference is not deployed yet."
    }

@app.post("/api/v1/analyze")
async def receive_match(
    file: UploadFile = File(...),
    match_type: str = Form("doubles"),
    max_players: int = Form(4),
    source: str = Form("camera"),
    movement_json: str = Form("{}"),
):
    if match_type not in ("singles", "doubles"):
        raise HTTPException(status_code=400, detail="match_type must be singles or doubles.")
    if max_players not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="max_players must be between 1 and 4.")
    if source != "camera":
        raise HTTPException(status_code=400, detail="Only phone-camera recordings are accepted.")
    try:
        movement = json.loads(movement_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="movement_json must contain valid JSON.")
    if not isinstance(movement, dict):
        raise HTTPException(status_code=400, detail="movement_json must be a JSON object.")

    filename = Path(file.filename or "match-video.webm").name
    suffix = Path(filename).suffix.lower()
    if suffix not in (".webm", ".mp4", ".mov", ".m4v"):
        suffix = ".webm"

    job_id = str(uuid.uuid4())
    temp_path = TEMP_ROOT / f"{job_id}{suffix}"
    total = 0
    try:
        with temp_path.open("wb") as destination:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > UPLOAD_LIMIT_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Recording exceeds the current {UPLOAD_LIMIT_MB} MB test upload limit. Try a shorter clip."
                    )
                destination.write(chunk)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if total == 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded video is empty.")

    # Upload smoke-test mode: confirm receipt, then remove raw video because
    # TrackNetV3 is not installed on this service yet.
    temp_path.unlink(missing_ok=True)
    frame_count = movement.get("frameCount", 0)
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "failed",
        "progress": 100,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "upload": {
            "received": True,
            "filename": filename,
            "size_bytes": total,
            "movement_frame_count": frame_count
        },
        "movement": movement,
        "error": "Video upload was received successfully and the temporary video was deleted. TrackNetV3 is not installed yet, so AI analysis has not run."
    }
    return {
        "job_id": job_id,
        "status": "failed",
        "upload_received": True,
        "video_size_bytes": total,
        "movement_frame_count": frame_count,
        "message": JOBS[job_id]["error"]
    }

@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found. The free service may have restarted and cleared its in-memory job list.")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "error": job.get("error"),
        "upload": job.get("upload")
    }

@app.get("/api/v1/jobs/{job_id}/result")
def get_job_result(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "job_id": job_id,
        "status": job["status"],
        "movement": job.get("movement"),
        "video": None,
        "shuttle": {"points": []},
        "error": job.get("error")
    }
