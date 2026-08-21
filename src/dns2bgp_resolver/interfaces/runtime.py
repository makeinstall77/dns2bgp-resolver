from __future__ import annotations

import asyncio
import logging
from typing import Optional

import uvicorn

from dns2bgp_resolver.container import AppContainer

logger = logging.getLogger(__name__)


async def run_services(
    container: AppContainer,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    enable_web: bool = True,
    enable_telegram: bool = True,
) -> None:
    await container.scheduler.start()
    # Ensure bird file exists even on empty DB
    await container.pipeline.export_routes()

    tasks: list[asyncio.Task] = []
    web_server: uvicorn.Server | None = None

    if enable_web:
        from dns2bgp_resolver.interfaces.web.app import create_app

        web_host = host or container.settings.web.host
        web_port = port or container.settings.web.port
        fastapi_app = create_app(container)
        config = uvicorn.Config(fastapi_app, host=web_host, port=web_port, log_level="info")
        web_server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(web_server.serve(), name="web"))
        logger.info("web listening on http://%s:%s", web_host, web_port)

    if enable_telegram and container.settings.telegram.token:
        from dns2bgp_resolver.interfaces.telegram.bot import run_telegram_bot

        tasks.append(
            asyncio.create_task(run_telegram_bot(container), name="telegram")
        )
        logger.info("telegram bot starting")
    elif enable_telegram:
        logger.warning("telegram token not set — bot disabled")

    if not tasks:
        logger.info("scheduler-only mode (web/telegram disabled)")
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        return

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        if web_server is not None:
            web_server.should_exit = True
        await container.scheduler.stop()
