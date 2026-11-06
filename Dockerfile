# Backend container for Cloud Run.
#
# Tesseract is the reason this is a container rather than a Cloud Function: the
# Functions Python runtime cannot install system binaries, and without OCR a
# screen-capture response sheet (one bitmap per page) cannot be read at all.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tesseract-ocr-eng is the language data; without it tesseract installs but
# refuses to run. libgl/libglib are pulled in by the imaging stack.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-api.txt ./
RUN pip install -r requirements-api.txt

COPY api/ ./api/
COPY parser.py topics.py scoring.py report.py image_source.py records.py ./
COPY config.yaml topics.yaml plan_templates.yaml ./

# Cloud Run supplies PORT; default to 8080 for local runs.
ENV PORT=8080
EXPOSE 8080

# One worker: the OCR path is CPU-bound, so concurrency is better handled by
# Cloud Run starting more instances than by threads fighting over one CPU.
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1
