FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libglib2.0-0 libsm6 libxext6 libxrender1 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/worker

# CPU-only PyTorch. patch_cpu.py adapts upstream inference code to CPU/CUDA device selection.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.2.2 torchvision==0.17.2
COPY requirements.txt .
RUN pip install -r requirements.txt

ARG TRACKNET_REPO=https://github.com/qaz812345/TrackNetV3.git
RUN git clone --depth 1 ${TRACKNET_REPO} /opt/TrackNetV3
COPY patch_cpu.py /opt/worker/patch_cpu.py
RUN python /opt/worker/patch_cpu.py /opt/TrackNetV3/predict.py

COPY worker.py /opt/worker/worker.py
ENV TRACKNET_DIR=/opt/TrackNetV3 CHECKPOINT_DIR=/tmp/checkpoints WORK_DIR=/tmp/badminton-job
ENTRYPOINT ["python", "/opt/worker/worker.py"]
