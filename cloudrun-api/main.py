import json, os, uuid
from pathlib import Path
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
REGION = os.getenv("GCP_REGION", "asia-southeast1")
JOB_NAME = os.getenv("TRACKNET_JOB_NAME", "badminton-tracknet-worker")
BUCKET_NAME = os.environ["GCS_BUCKET"]
UPLOAD_LIMIT_MB = int(os.getenv("UPLOAD_LIMIT_MB", "30"))
API_VERSION = "2.0-multi-match-api"

app = FastAPI(title="Badminton AI Coach Cloud API", version=API_VERSION)
origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=["*"] if origins == ["*"] else origins,
                   allow_credentials=False, allow_methods=["GET","POST","OPTIONS"],
                   allow_headers=["Authorization","Content-Type"])

credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
session = AuthorizedSession(credentials)
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(BUCKET_NAME)
RUN_URL = f"https://run.googleapis.com/v2/projects/{PROJECT_ID}/locations/{REGION}/jobs/{JOB_NAME}:run"
OPS_URL = f"https://run.googleapis.com/v2/projects/{PROJECT_ID}/locations/{REGION}/operations"

def job_doc_path(job_id): return f"jobs/{job_id}.json"
def session_doc_path(session_id): return f"sessions/{session_id}.json"
def read_session(session_id):
    blob = bucket.blob(session_doc_path(session_id))
    if not blob.exists(): raise HTTPException(status_code=404, detail="Session not found.")
    return json.loads(blob.download_as_text())
def write_session(data):
    bucket.blob(session_doc_path(data["session_id"])).upload_from_string(json.dumps(data), content_type="application/json")
def update_session_match(session_id, match):
    data = read_session(session_id)
    data["matches"] = [m for m in data.get("matches", []) if m.get("job_id") != match.get("job_id")] + [match]
    from datetime import datetime, timezone
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_session(data)
def read_job(job_id):
    blob = bucket.blob(job_doc_path(job_id))
    if not blob.exists(): raise HTTPException(status_code=404, detail="Job not found.")
    return json.loads(blob.download_as_text())
def write_job(data):
    bucket.blob(job_doc_path(data["job_id"])).upload_from_string(json.dumps(data), content_type="application/json")

@app.get("/")
def root():
    return {"app":"Badminton AI Coach Cloud API","version":API_VERSION,"status":"online"}

@app.get("/health")
def health():
    return {"status":"ok","version":API_VERSION,"api_ready":True,"upload_ready":True,
            "tracknet_ready":True,"inpaintnet_ready":True,"upload_limit_mb":UPLOAD_LIMIT_MB,
            "message":"Cloud Run API is connected to Cloud Storage and the TrackNet Cloud Run Job."}

@app.post("/api/v1/sessions")
def create_session():
    from datetime import datetime, timezone
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    data = {"session_id":session_id,"created_at":now,"updated_at":now,"status":"active","matches":[]}
    write_session(data)
    return data

@app.get("/api/v1/sessions")
def list_sessions():
    items = []
    for blob in bucket.list_blobs(prefix="sessions/"):
        if blob.name.endswith(".json"):
            try: items.append(json.loads(blob.download_as_text()))
            except Exception: continue
    items.sort(key=lambda x: x.get("updated_at",""), reverse=True)
    return {"sessions":items}

@app.get("/api/v1/sessions/{session_id}")
def get_session(session_id: str):
    return read_session(session_id)

@app.post("/api/v1/analyze")
async def analyze(file: UploadFile = File(...), session_id: str = Form(...), match_type: str = Form("doubles"),
                  max_players: int = Form(4), source: str = Form("camera"),
                  movement_json: str = Form("{}")):
    if match_type not in ("singles","doubles"): raise HTTPException(400,"Invalid match_type.")
    if max_players not in (1,2,3,4): raise HTTPException(400,"max_players must be 1-4.")
    if source != "camera": raise HTTPException(400,"Only camera recordings are accepted.")
    try: movement = json.loads(movement_json)
    except json.JSONDecodeError: raise HTTPException(400,"movement_json must be valid JSON.")
    if not isinstance(movement,dict): raise HTTPException(400,"movement_json must be an object.")

    read_session(session_id)
    filename = Path(file.filename or "match.webm").name
    suffix = Path(filename).suffix.lower()
    if suffix not in (".webm",".mp4",".mov",".m4v"): suffix = ".webm"
    job_id = str(uuid.uuid4())
    object_name = f"input/{job_id}{suffix}"
    video_blob = bucket.blob(object_name)
    total = 0
    try:
        # Stream upload to a temporary file is avoided; the GCS blob receives
        # the request stream in chunks and enforces the configured size limit.
        chunks = []
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk: break
            total += len(chunk)
            if total > UPLOAD_LIMIT_MB * 1024 * 1024:
                raise HTTPException(413, f"Recording exceeds {UPLOAD_LIMIT_MB} MB test limit.")
            chunks.append(chunk)
        if total == 0: raise HTTPException(400,"Uploaded video is empty.")
        video_blob.upload_from_string(b"".join(chunks), content_type=file.content_type or "application/octet-stream")
    except Exception:
        if video_blob.exists(): video_blob.delete()
        raise
    finally:
        await file.close()

    movement_name = f"jobs/{job_id}.movement.json"
    bucket.blob(movement_name).upload_from_string(json.dumps(movement), content_type="application/json")
    input_uri = f"gs://{BUCKET_NAME}/{object_name}"
    output_uri = f"gs://{BUCKET_NAME}/results/{job_id}"
    overrides = {"overrides":{"containerOverrides":[{"env":[
        {"name":"INPUT_URI","value":input_uri},
        {"name":"OUTPUT_URI","value":output_uri},
        {"name":"DELETE_SOURCE_AFTER_SUCCESS","value":"false"}
    ]}]}}
    response = session.post(RUN_URL, json=overrides, timeout=60)
    if not response.ok:
        video_blob.delete()
        bucket.blob(movement_name).delete()
        raise HTTPException(502, f"Could not start TrackNet job: {response.text[:1200]}")
    operation = response.json()
    job = {"job_id":job_id,"status":"queued","progress":0,"operation_name":operation.get("name"),
           "input_uri":input_uri,"output_uri":output_uri,"movement_path":movement_name,
           "filename":filename,"size_bytes":total,"movement_frame_count":movement.get("frameCount",0),"match_type":match_type,"max_players":max_players,"session_id":session_id,"result_verified":False,"source_deleted":False}
    write_job(job)
    from datetime import datetime, timezone
    update_session_match(session_id, {"job_id":job_id,"filename":filename,"status":"queued","created_at":datetime.now(timezone.utc).isoformat(),"size_bytes":total,"match_type":match_type,"max_players":max_players,"result_verified":False})
    return {"job_id":job_id,"session_id":session_id,"status":"queued","upload_received":True,"video_size_bytes":total,
            "movement_frame_count":movement.get("frameCount",0),"message":"Video uploaded and TrackNet job queued."}

@app.get("/api/v1/jobs/{job_id}")
def job_status(job_id: str):
    job = read_job(job_id)
    operation_name = job.get("operation_name")
    if not operation_name: return {"job_id":job_id,"status":job["status"],"progress":job["progress"]}
    op_response = session.get(f"https://run.googleapis.com/v2/{operation_name}", timeout=30)
    if not op_response.ok: raise HTTPException(502, f"Could not read Cloud Run operation: {op_response.text[:800]}")
    op = op_response.json()
    if not op.get("done"):
        job["status"]="processing"; job["progress"]=50; write_job(job)
        return {"job_id":job_id,"status":"processing","progress":50}
    if op.get("error"):
        job["status"]="failed"; job["error"]=op["error"].get("message","Cloud Run execution failed.")
        write_job(job)
        return {"job_id":job_id,"status":"failed","progress":100,"error":job["error"]}
    execution = op.get("response",{})
    execution_name = execution.get("name") or (op.get("metadata") or {}).get("name")
    if not execution_name:
        job["status"]="failed"; job["error"]="Cloud Run operation completed without an execution name."
        write_job(job)
        return {"job_id":job_id,"status":"failed","progress":100,"error":job["error"]}
    exec_response = session.get(f"https://run.googleapis.com/v2/{execution_name}", timeout=30)
    if not exec_response.ok: raise HTTPException(502, f"Could not read execution: {exec_response.text[:800]}")
    execution_data = exec_response.json()
    conditions = execution_data.get("conditions",[])
    failed = any(c.get("type")=="Completed" and c.get("state")=="CONDITION_FAILED" for c in conditions)
    succeeded = any(c.get("type")=="Completed" and c.get("state")=="CONDITION_SUCCEEDED" for c in conditions)
    if failed:
        job["status"]="failed"; job["error"]="TrackNet Cloud Run execution failed. Check Cloud Run Job logs."
    elif succeeded:
        job["status"]="completed"; job["progress"]=100; job["execution_name"]=execution_name
    else:
        job["status"]="processing"; job["progress"]=75
    write_job(job)
    if job.get("session_id"):
        update_session_match(job["session_id"], {"job_id":job_id,"filename":job.get("filename"),"status":job["status"],"size_bytes":job.get("size_bytes",0),"match_type":job.get("match_type"),"max_players":job.get("max_players"),"result_verified":job.get("result_verified",False),"error":job.get("error")})
    return {"job_id":job_id,"session_id":job.get("session_id"),"status":job["status"],"progress":job["progress"],"error":job.get("error"),
            "upload":{"received":True,"size_bytes":job["size_bytes"],"movement_frame_count":job.get("movement_frame_count",0)}}

@app.get("/api/v1/jobs/{job_id}/result")
def job_result(job_id: str):
    job = read_job(job_id)
    if job.get("status") != "completed": raise HTTPException(409,"Analysis is not complete.")
    result_blob = bucket.blob(f"results/{job_id}/{job_id}_trajectory.json")
    movement_blob = bucket.blob(job["movement_path"])
    if not result_blob.exists(): raise HTTPException(404,"Trajectory JSON not found in Storage.")
    if not movement_blob.exists(): raise HTTPException(404,"Movement JSON not found in Storage.")
    try:
        shuttle = json.loads(result_blob.download_as_text())
        movement = json.loads(movement_blob.download_as_text())
        if not isinstance(shuttle, dict) or not isinstance(shuttle.get("points"), list): raise ValueError("Trajectory points missing.")
        if not isinstance(movement, dict) or not isinstance(movement.get("frames"), list): raise ValueError("Movement frames missing.")
    except Exception as exc:
        job["result_verified"] = False
        job["verification_error"] = str(exc)
        write_job(job)
        raise HTTPException(422, f"Stored results failed verification: {exc}")
    input_uri = job.get("input_uri", "")
    if input_uri.startswith(f"gs://{BUCKET_NAME}/") and not job.get("source_deleted"):
        source_name = input_uri.split(f"gs://{BUCKET_NAME}/",1)[1]
        source_blob = bucket.blob(source_name)
        if source_blob.exists(): source_blob.delete()
        job["source_deleted"] = True
    job["result_verified"] = True
    write_job(job)
    if job.get("session_id"):
        update_session_match(job["session_id"], {"job_id":job_id,"filename":job.get("filename"),"status":"completed","size_bytes":job.get("size_bytes",0),"match_type":job.get("match_type"),"max_players":job.get("max_players"),"result_verified":True,"source_deleted":job.get("source_deleted",False)})
    return {"job_id":job_id,"session_id":job.get("session_id"),"status":"completed","result_verified":True,"source_deleted":job.get("source_deleted",False),"video":{"frame_count":shuttle.get("frame_count"),"fps":shuttle.get("fps")},"shuttle":shuttle,"movement":movement}
