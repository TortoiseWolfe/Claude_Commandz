# Private offline meeting transcriber. Models are NOT baked in: they live in the
# named volume meeting-whisper-models, mounted at HF_HOME.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 \
    HF_HOME=/models HOME=/tmp
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg \
 && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir --upgrade pip \
 && pip3 install --no-cache-dir faster-whisper==1.1.1 requests
COPY transcribe.py /app/transcribe.py
WORKDIR /app
ENTRYPOINT ["python3", "/app/transcribe.py"]
