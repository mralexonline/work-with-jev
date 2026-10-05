# One image for both processes (web + worker). Models are NOT baked in: fetch them into the mounted volume.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 app
WORKDIR /srv/app
COPY requirements.lock requirements.txt ./
RUN pip install --no-cache-dir -r requirements.lock
COPY app ./app
COPY config ./config
COPY frontend ./frontend
COPY scripts ./scripts
COPY cloud ./cloud
RUN mkdir -p /data /models && chown -R app:app /data /models /srv/app
USER app
ENV DATA_DIR=/data MODELS_DIR=/models HOST=0.0.0.0 PORT=8000
EXPOSE 8000
CMD ["python", "-m", "app.main"]
