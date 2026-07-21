"""Process entrypoint: signals and top-level async lifecycle."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path

from aiohttp import web
from aiohttp.web import Application

from ytdlp_bot.bootstrap import bootstrap, shutdown_runtime

log = logging.getLogger("ytdlp_bot.main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ytdlp-bot")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to static configuration TOML",
    )
    parser.add_argument(
        "--fixture-workers",
        action="store_true",
        help="Use fixture media worker (deterministic CI / dry-run)",
    )
    return parser


async def _async_main(config_path: Path, *, fixture_workers: bool) -> int:
    try:
        runtime = await bootstrap(
            config_path,
            fixture_workers=fixture_workers or os.environ.get("YTDLP_BOT_FIXTURE_WORKER") == "1",
            start_background=True,
        )
    except Exception as exc:
        log.error("startup failed: %s", type(exc).__name__)
        return 1

    assert runtime.http_app is not None
    app = runtime.http_app
    assert isinstance(app, Application)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(
        runner,
        runtime.config.http.bind_host,
        runtime.config.http.bind_port,
    )

    def _signal_handler() -> None:
        runtime.health.close_admission()
        if runtime.command_service is not None:
            runtime.command_service.admission_open = False
        runtime.stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    try:
        await site.start()
        log.info(
            "listening http://%s:%s ready=%s",
            runtime.config.http.bind_host,
            runtime.config.http.bind_port,
            runtime.readiness.is_ready(),
        )
        await runtime.stop_event.wait()
    finally:
        await runner.cleanup()
        await shutdown_runtime(runtime)
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        code = asyncio.run(_async_main(args.config, fixture_workers=args.fixture_workers))
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
