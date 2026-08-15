FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System deps for psycopg2, FFmpeg for compressed audio formats (MP3/AAC/FLAC),
# and curl for healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so source changes don't bust the layer cache.
# The lock file pins every transitive version (Linux target) for
# reproducible builds; it combines requirements.txt + requirements-audio.txt
# because the same image is used for the API server and Celery workers
# (workers need demucs/basic-pitch). Regenerate with the command in
# requirements-audio.txt.
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

# Copy the rest of the source.
COPY . .

# Pre-download the Basic Pitch ONNX model. Without this, the first user
# request blocks for several seconds while the worker fetches the model
# from Spotify's release. After the warm-up the ONNX file lives in the
# package's model/ directory and is baked into this image layer.
RUN python warmup_basic_pitch.py

# Persistent storage for uploads and worker outputs — mount a volume here in compose.
RUN mkdir -p /app/storage/uploads /app/storage/outputs

# Create a non-root user and grant ownership of the app directory.
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV STORAGE_DIR=/app/storage

EXPOSE 8000

# Run migrations on container start, then launch uvicorn.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
