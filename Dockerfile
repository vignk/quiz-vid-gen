## Dockerfile

##This container installs FFmpeg and fonts, then runs the FastAPI app on the port Render provides. Render fully supports building and deploying from a `Dockerfile`. [1][2]

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/tmp /app/rendered /app/credentials

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
```
