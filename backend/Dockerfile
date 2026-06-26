FROM python:3.12-slim

# System deps for psycopg2 and any future audio tooling.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so source changes don't bust the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source.
COPY . .

# Pre-download the Basic Pitch ONNX model. Without this, the first user
# request blocks for several seconds while the worker fetches the model
# from Spotify's release. After the warm-up the ONNX file lives in the
# package's model/ directory and is baked into this image layer.
RUN python warmup_basic_pitch.py

# Persistent storage for uploads — mount a volume here in compose.
RUN mkdir -p /app/uploads
ENV UPLOAD_DIR=/app/uploads

EXPOSE 8000

# Run migrations on container start, then launch uvicorn.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
