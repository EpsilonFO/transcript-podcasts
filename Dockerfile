# syntax=docker/dockerfile:1
FROM python:3.12-slim

# System dependencies: ffmpeg for yt-dlp audio extraction, curl/unzip to fetch deno
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (yt-dlp needs a JS runtime to solve the YouTube n-sig challenge)
ENV DENO_INSTALL=/usr/local
RUN curl -fsSL https://deno.land/install.sh | sh

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Cache deps separately from source for faster rebuilds
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# App source
COPY main.py ./
COPY static ./static

# Persistence directory for conversations.json (mounted as a volume in compose)
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
