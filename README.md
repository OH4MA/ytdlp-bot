# yt-dlp Bot

Self-hosted Telegram and Discord media download bot. Users submit public HTTP(S) URLs; the service produces MP4 or MP3 artifacts (or playlist ZIP archives), reports progress, supports cancellation and status, uploads directly when platform limits allow, and otherwise issues reusable signed download URLs.

## Documentation

| Language | Document |
| --- | --- |
| English | [docs/en/README.md](docs/en/README.md) (added during OPS) |
| 繁體中文（臺灣） | [docs/zh-TW/README.md](docs/zh-TW/README.md)（於 OPS 階段補齊） |

Design sources live under `doc/`.

## Requirements

- CPython 3.13+
- [uv](https://github.com/astral-sh/uv)
- Docker and Docker Compose for deployment
- FFmpeg / ffprobe (bundled in the container image)

## Development

```bash
uv sync --all-extras --frozen
uv run ruff format --check
uv run ruff check
uv run pyright
uv run pytest
```

## Status

Implementation is tracked in `doc/tasks/progress.md`.
