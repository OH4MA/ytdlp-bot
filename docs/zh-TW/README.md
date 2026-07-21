# yt-dlp Bot — 營運手冊（繁體中文）

## 概要

自架 Telegram / Discord 媒體下載機器人。使用者提交公開 HTTP(S) 網址；服務產出 MP4/MP3（或播放清單 ZIP）、回報進度、支援取消與狀態查詢，在平台上傳上限內直接上傳，否則簽發可重複使用的範圍下載連結。

## 需求

- 具備 Docker 與 Docker Compose 的 Linux 主機
- 開發需 CPython 3.13 與 [uv](https://github.com/astral-sh/uv)
- FFmpeg / ffprobe（部署映像已內建）
- 由營運者依主機磁碟選擇 `capacity_bytes`

## 開發快速開始

```bash
uv sync --all-extras --frozen
uv run ruff format --check
uv run ruff check
uv run pyright
uv run pytest
```

## 設定

將 `config.example.toml` 複製到安全路徑。密鑰僅透過：

- `env:變數名稱`
- `file:/run/secrets/...`

重點：

- `storage.capacity_bytes` — 必須由營運者設定（範例值僅供示意）
- `artifacts.public_base_url` — HTTPS，不可含 query/fragment/結尾斜線
- `artifacts.signing_secret_ref` — 至少 32 位元組熵
- 至少啟用一個平台並提供 token
- 靜態 `access.administrators` 無法透過聊天指令變更

## 部署

```bash
docker compose config
docker compose build
# 啟動前將密鑰檔放入 ./secrets
docker compose up -d
```

健康檢查：私有 `/healthz`、`/readyz`。公開下載僅提供 `/v1/artifacts/{id}/{name}`。

## 安全

- 必須強制控制出口網路；僅 URL 驗證不足。
- 日誌不得出現 bot token、簽章密鑰、完整 bearer URL 或敏感來源 URL 元件。
- 應用程式以非 root、唯讀根檔案系統期望執行。

## 即時煙霧測試（手動）

需真實憑證（不納入例行 CI）：於兩平台送出 `/ytdl`、`/ytmp3`，驗證進度、取消、狀態、上限內上傳、上限外簽章連結與重啟對帳。
