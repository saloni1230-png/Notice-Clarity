FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 notice && mkdir -p /app /data/objects \
    && chown -R notice:notice /app /data
WORKDIR /app
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY --chown=notice:notice . .
RUN pip install --no-deps --no-build-isolation .
USER notice
EXPOSE 8000
CMD ["uvicorn", "notice.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
