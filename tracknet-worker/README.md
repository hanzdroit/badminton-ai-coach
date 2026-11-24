# Badminton AI Coach — TrackNetV3 Cloud Run Worker (V1.9)

On-demand CPU-first inference container for Google Cloud Run Jobs. It is not a permanent web server.

## What it does
1. Downloads a temporary video and the official TrackNetV3 and InpaintNet checkpoints from private Google Cloud Storage.
2. Runs the upstream TrackNetV3 prediction pipeline with InpaintNet.
3. Produces the upstream CSV fields Frame, X, Y, Visibility and a JSON trajectory with pixel coordinates and timestamps.
4. Uploads CSV and JSON results to GCS.
5. Optionally deletes the source video only after inference and both result uploads succeed.

Failed jobs preserve the source object for retry/debugging. Local copies of the video and checkpoints are removed when the container exits.

## Upstream model and CPU compatibility
Upstream project: https://github.com/qaz812345/TrackNetV3 (MIT).
The upstream README requires TrackNet_best.pt and InpaintNet_best.pt checkpoints. Do not commit checkpoints or match videos to GitHub.

The upstream predict.py currently uses hard-coded .cuda() calls and torch.load without map_location. The Docker build applies patch_cpu.py to the cloned upstream predict.py so inference selects CUDA when available and otherwise uses CPU. The model architecture and prediction pipeline are otherwise upstream.

## Build
From the repository root:
docker build -t badminton-tracknet-worker:1.9 ./tracknet-worker

For reproducible production builds, pin the upstream TrackNetV3 Git commit in the Docker build instead of following its default branch.

## GCS preparation
Create a private bucket, preferably in asia-southeast1, and upload:
- incoming/test-match.mp4
- checkpoints/TrackNet_best.pt
- checkpoints/InpaintNet_best.pt

The Cloud Run Job service account needs object read access to input and checkpoint objects and object create access to results. Grant object delete permission only if source deletion is enabled. Use the attached service account and Application Default Credentials; do not upload service-account JSON keys.

## Cloud Run Job environment variables
INPUT_URI=gs://YOUR_BUCKET/incoming/test-match.mp4
OUTPUT_URI=gs://YOUR_BUCKET/results/test-run
TRACKNET_CHECKPOINT_URI=gs://YOUR_BUCKET/checkpoints/TrackNet_best.pt
INPAINTNET_CHECKPOINT_URI=gs://YOUR_BUCKET/checkpoints/InpaintNet_best.pt
DELETE_SOURCE_AFTER_SUCCESS=false
BATCH_SIZE=2
LARGE_VIDEO=true
MAX_SAMPLE_NUM=1800

Suggested first CPU test: Singapore region, one task, 4 vCPU, 16 GiB RAM, 60-minute timeout, parallelism 1, one retry. Keep source deletion disabled until result uploads are verified.

## Important integration status
- This is a standalone inference worker, not yet connected to the Render API job queue.
- Render V1.8 currently deletes uploaded videos immediately after the upload smoke test. It must be changed to stage video in GCS and dispatch this Cloud Run Job before end-to-end browser analysis is possible.
- Checkpoint download from the official project is a separate manual step.
- The first run must validate checkpoint compatibility with the selected PyTorch build and measure actual CPU runtime. Do not assume real-time processing.
- Coordinates are original-video pixel coordinates. Court-coordinate mapping and player/shuttle event correlation are later stages.

## Privacy
Keep GCS buckets private. Use a short non-sensitive clip for the first test. The worker removes local temporary files on exit and can delete the source GCS object after successful result uploads.
