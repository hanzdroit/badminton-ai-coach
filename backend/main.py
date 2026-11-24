import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

APP_VERSION = "1.8-api-bootstrap"
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
        "tracknet_ready": False,
        "inpaintnet_ready": False,
        "upload_limit_mb": 0,
        "message": "API is online. TrackNetV3 and its checkpoint have not been deployed yet."
    }

@app.post("/api/v1/analyze")
def analyze_not_ready():
    raise HTTPException(
        status_code=503,
        detail="The API is online, but TrackNetV3 is not deployed yet. Video analysis is disabled until the model service is ready."
    )

@app.get("/api/v1/jobs/{job_id}")
def job_not_ready(job_id: str):
    raise HTTPException(status_code=503, detail="Analysis jobs are not enabled until TrackNetV3 is deployed.")
