# Wallpaper Engine Vinyl as a shared website (public mode).
#
#   docker build -t wallpaper-engine-vinyl .
#   docker run -p 7860:7860 -e CANVAS2WE_DONATE_URL=https://paypal.me/you wallpaper-engine-vinyl
#
# Works as-is on Hugging Face Spaces (Docker SDK, port 7860). Optional env vars:
#   SPOTIFY_SP_DC          a Spotify session cookie for the server to use (a spare account)
#   CANVAS2WE_DONATE_URL   shows the Donate button
#   CANVAS2WE_MAX_RENDERS  wallpapers rendered at once (default 2)
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs the container as user 1000.
RUN useradd -m -u 1000 app
WORKDIR /home/app/src
COPY --chown=app . .
RUN pip install --no-cache-dir . && playwright install-deps chromium

USER app
RUN playwright install chromium

ENV PORT=7860 \
    CANVAS2WE_CONFIG_DIR=/home/app/.canvas2we \
    PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["canvas2we", "ui", "--public", "--host", "0.0.0.0", "--no-browser"]
