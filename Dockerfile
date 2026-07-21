# yt-dlp Bot application image (non-root, pinned tools).
FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ytdlp \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin ytdlp

COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev \
    && chown -R ytdlp:ytdlp /app

USER ytdlp:ytdlp
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080
ENTRYPOINT ["ytdlp-bot"]
CMD ["--config", "/config/config.toml"]
