from __future__ import annotations

import asyncio
import logging
import signal
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
    if container.settings.auto_list.enabled:
        await container.auto_sync_scheduler.start()
    await container.pipeline.export_routes()

    tasks: list[asyncio.Task] = []
    web_server: uvicorn.Server | None = None
    stopped = False
    loop = asyncio.get_running_loop()

    async def _stop_services(reason: str) -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        logger.info("shutting down (%s)", reason)
        if web_server is not None:
            web_server.should_exit = True
            web_server.force_exit = True
        for task in tasks:
            if not task.done():
                task.cancel()
        await container.scheduler.stop()
        await container.auto_sync_scheduler.stop()
        await container.pipeline.flush_pending_export()

    def _on_signal(signum: int, _frame=None) -> None:
        loop.call_soon_threadsafe(lambda: asyncio.create_task(_stop_services(f"signal:{signum}")))

    async def _override_uvicorn_signal_handlers() -> None:
        await asyncio.sleep(1)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, _on_signal)

    if enable_web:
        from dns2bgp_resolver.interfaces.web.app import create_app

        web_host = host or container.settings.web.host
        web_port = port or container.settings.web.port
        fastapi_app = create_app(container)
        config = uvicorn.Config(
            fastapi_app,
            host=web_host,
            port=web_port,
            log_level="info",
            timeout_graceful_shutdown=5,
        )
        web_server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(web_server.serve(), name="web"))
        tasks.append(asyncio.create_task(_override_uvicorn_signal_handlers(), name="signals"))
        logger.info("web listening on http://%s:%s", web_host, web_port)
    else:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda s=sig: _on_signal(int(s)))
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, _on_signal)

    if enable_telegram and container.settings.telegram.token:
        from dns2bgp_resolver.interfaces.telegram.bot import run_telegram_bot

        tasks.append(asyncio.create_task(run_telegram_bot(container), name="telegram"))
        logger.info("telegram bot starting")
    elif enable_telegram:
        logger.warning("telegram token not set — bot disabled")

    if not tasks:
        logger.info("scheduler-only mode (web/telegram disabled)")
        try:
            while not stopped:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await _stop_services("finally")
        return

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        await _stop_services("finally")
