"""Cloud Run Job entrypoint for TrackNetV3 + InpaintNet inference."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import cv2
from google.cloud import storage

TRACKNET_DIR = Path(os.getenv("TRACKNET_DIR", "/opt/TrackNetV3"))
WORK_DIR = Path(os.getenv("WORK_DIR", "/tmp/badminton-job"))
CHECKPOINT_DIR = Path(os.getenv("CHECKPOINT_DIR", "/tmp/checkpoints"))
DELETE_SOURCE_AFTER_SUCCESS = os.getenv("DELETE_SOURCE_AFTER_SUCCESS", "false").lower() == "true"
BATCH_SIZE = os.getenv("BATCH_SIZE", "1")
LARGE_VIDEO = os.getenv("LARGE_VIDEO", "true").lower() == "true"
MAX_SAMPLE_NUM = os.getenv("MAX_SAMPLE_NUM", "1800")


def log_memory(stage: str) -> None:
    """Log container memory usage where the Cloud Run cgroup exposes it."""
    values = {}
    for key, path in (
        ("current_bytes", "/sys/fs/cgroup/memory.current"),
        ("limit_bytes", "/sys/fs/cgroup/memory.max"),
    ):
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
            values[key] = raw if raw == "max" else int(raw)
        except (OSError, ValueError):
            pass
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, value = line.split(":", 1)
                values[key] = value.strip()
    except OSError:
        pass
    print(json.dumps({"stage": stage, "memory": values}), flush=True)



def parse_gs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise ValueError(f"Expected gs://bucket/object URI, received {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def download_gcs(client: storage.Client, uri: str, destination: Path) -> tuple[str, str]:
    bucket_name, object_name = parse_gs_uri(uri)
    blob = client.bucket(bucket_name).blob(object_name)
    if not blob.exists():
        raise FileNotFoundError(f"GCS object does not exist: {uri}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(destination))
    return bucket_name, object_name


def upload_file(client: storage.Client, source: Path, destination_uri: str) -> None:
    bucket_name, object_name = parse_gs_uri(destination_uri)
    client.bucket(bucket_name).blob(object_name).upload_from_filename(str(source))


def main() -> int:
    input_uri = os.environ["INPUT_URI"]
    output_uri = os.environ["OUTPUT_URI"].rstrip("/")
    tracknet_uri = os.environ["TRACKNET_CHECKPOINT_URI"]
    inpaint_uri = os.environ["INPAINTNET_CHECKPOINT_URI"]
    client = storage.Client()

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    input_bucket_name, input_object_name = parse_gs_uri(input_uri)
    input_name = Path(input_object_name).name
    suffix = Path(input_name).suffix.lower() or ".mp4"
    local_video = WORK_DIR / f"input{suffix}"
    # Normalize every source container to H.264 MP4 before TrackNet. OpenCV/
    # the upstream TrackNet dataset reader may not decode phone WebM reliably.
    analysis_video = WORK_DIR / "tracknet_input.mp4"
    local_tracknet = CHECKPOINT_DIR / "TrackNet_best.pt"
    local_inpaint = CHECKPOINT_DIR / "InpaintNet_best.pt"
    result_dir = WORK_DIR / "prediction"
    result_dir.mkdir(parents=True, exist_ok=True)
    success = False

    try:
        print("Worker V2.9: downloading source video and model checkpoints", flush=True)
        log_memory("before_download")
        download_gcs(client, input_uri, local_video)
        download_gcs(client, tracknet_uri, local_tracknet)
        download_gcs(client, inpaint_uri, local_inpaint)
        log_memory("after_download")

        # Transcode to a decoder-friendly MP4 container (video only).
        transcode = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(local_video), "-map", "0:v:0", "-an",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(analysis_video),
        ]
        print("Worker V2.9: normalizing source video with ffmpeg", flush=True)
        converted = subprocess.run(transcode, capture_output=True, text=True)
        if converted.returncode != 0 or not analysis_video.exists() or analysis_video.stat().st_size == 0:
            detail = (converted.stderr or "ffmpeg produced no output")[-2000:]
            raise RuntimeError(f"Could not normalize uploaded video to MP4: {detail}")
        log_memory("after_transcode")
        probe = cv2.VideoCapture(str(analysis_video))
        readable, first_frame = probe.read()
        probe.release()
        if not readable or first_frame is None:
            raise RuntimeError("Uploaded video could not be decoded after MP4 normalization; no readable video frames found.")

        command = [
            sys.executable, str(TRACKNET_DIR / "predict.py"),
            "--video_file", str(analysis_video),
            "--tracknet_file", str(local_tracknet),
            "--inpaintnet_file", str(local_inpaint),
            "--batch_size", BATCH_SIZE,
            "--save_dir", str(result_dir),
        ]
        if LARGE_VIDEO:
            command.extend(["--large_video", "--max_sample_num", MAX_SAMPLE_NUM])

        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(
            f"Worker V2.9: starting TrackNetV3 inference on {device}; "
            f"batch_size={BATCH_SIZE}; large_video={LARGE_VIDEO}; "
            f"max_sample_num={MAX_SAMPLE_NUM}",
            flush=True,
        )
        log_memory("before_tracknet_inference")
        try:
            subprocess.run(command, check=True, cwd=str(TRACKNET_DIR))
        except subprocess.CalledProcessError as exc:
            print(
                f"TrackNet subprocess failed: returncode={exc.returncode}; "
                f"signal={-exc.returncode if exc.returncode < 0 else None}",
                flush=True,
            )
            log_memory("after_tracknet_failure")
            raise
        log_memory("after_tracknet_inference")

        csv_path = result_dir / f"{analysis_video.stem}_ball.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"TrackNetV3 did not create expected CSV: {csv_path}")

        cap = cv2.VideoCapture(str(analysis_video))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()

        points = []
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                frame = int(float(row["Frame"]))
                points.append({
                    "frame": frame,
                    "timestamp_seconds": round(frame / fps, 4) if fps > 0 else None,
                    "x": int(float(row["X"])),
                    "y": int(float(row["Y"])),
                    "visible": bool(int(float(row["Visibility"]))),
                })

        result = {
            "schema_version": "1.0",
            "engine": "TrackNetV3+InpaintNet",
            "device": device,
            "source_video": input_name,
            "fps": fps,
            "frame_count": frame_count,
            "point_count": len(points),
            "coordinate_space": "original_video_pixels",
            "points": points,
        }
        json_path = result_dir / "shuttle_trajectory.json"
        json_path.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")

        prefix = f"{output_uri}/{Path(input_name).stem}"
        csv_uri = f"{prefix}_ball.csv"
        json_uri = f"{prefix}_trajectory.json"
        upload_file(client, csv_path, csv_uri)
        upload_file(client, json_path, json_uri)
        success = True
        log_memory("after_result_upload")
        print(json.dumps({"worker_version": "2.9", "status": "completed", "point_count": len(points), "csv_uri": csv_uri, "result_uri": json_uri}), flush=True)
        return 0
    finally:
        # Delete the source object only after successful inference AND result upload.
        if success and DELETE_SOURCE_AFTER_SUCCESS:
            client.bucket(input_bucket_name).blob(input_object_name).delete()
        for path in (local_video, analysis_video, local_tracknet, local_inpaint):
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
