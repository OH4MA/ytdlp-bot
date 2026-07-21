"""aiohttp application factory for the private download origin."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl

from aiohttp import web

from ytdlp_bot.adapters.http.downloads import DownloadService
from ytdlp_bot.adapters.http.health import HealthController
from ytdlp_bot.adapters.security.redaction import redact_value


def create_app(
    *,
    downloads: DownloadService,
    health: HealthController,
    expose_health: bool = True,
) -> web.Application:
    app = web.Application()
    app["downloads"] = downloads
    app["health"] = health

    async def download_handler(request: web.Request) -> web.StreamResponse:
        artifact_id = request.match_info["artifact_id"]
        display_name = request.match_info["display_name"]
        # Do not log query string.
        query = dict(parse_qsl(request.query_string, keep_blank_values=False))
        status, headers, body = await downloads.handle(
            method=request.method,
            artifact_id=artifact_id,
            display_name_path=display_name,
            query=query,
            range_header=request.headers.get("Range"),
            holder_id=f"http:{id(request)}",
        )
        # Async generators expose __aiter__; bytes/bytearray do not.
        if isinstance(body, (bytes, bytearray)) or body is None:
            return web.Response(status=status, headers=headers, body=body or b"")

        resp = web.StreamResponse(status=status, headers=headers)
        await resp.prepare(request)
        async for chunk in body:
            await resp.write(chunk)
        await resp.write_eof()
        return resp

    app.router.add_route(
        "GET",
        "/v1/artifacts/{artifact_id}/{display_name}",
        download_handler,
    )
    app.router.add_route(
        "HEAD",
        "/v1/artifacts/{artifact_id}/{display_name}",
        download_handler,
    )

    if expose_health:

        async def live(_request: web.Request) -> web.Response:
            return web.json_response(health.live())

        async def ready(_request: web.Request) -> web.Response:
            code, view = health.ready()
            # Ensure no secrets in readiness.
            return web.json_response(redact_value(view), status=code)

        app.router.add_get("/healthz", live)
        app.router.add_get("/readyz", ready)

    return app


async def start_test_server(app: web.Application) -> tuple[Any, str]:
    """Start an aiohttp test server; returns (runner, base_url)."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    # sockets available on site
    sockets = site._server.sockets  # type: ignore[attr-defined]
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"
