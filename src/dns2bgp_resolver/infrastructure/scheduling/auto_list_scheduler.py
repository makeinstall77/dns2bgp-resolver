from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from dns2bgp_resolver.application.services.auto_list_sync import AutoListSyncService

logger = logging.getLogger(__name__)


class AutoListSyncScheduler:
    """Periodically sync auto domain list from remote URL."""

    def __init__(
        self,
        sync_service: AutoListSyncService,
        *,
        sync_interval: int = 86400,
        sync_on_startup: bool = True,
        poll_interval: float = 3600.0,
    ) -> None:
        self._sync_service = sync_service
        self._sync_interval = sync_interval
        self._sync_on_startup = sync_on_startup
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_sync: datetime | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="auto-list-sync")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def sync_now(self) -> None:
        await self._do_sync()

    async def _run(self) -> None:
        logger.info(
            "auto list scheduler started (interval=%ds, poll=%.1fs)",
            self._sync_interval,
            self._poll_interval,
        )
        if self._sync_on_startup:
            await self._do_sync()

        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            if self._last_sync is None or (now - self._last_sync).total_seconds() >= self._sync_interval:
                await self._do_sync()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue
        logger.info("auto list scheduler stopped")

    async def _do_sync(self) -> None:
        try:
            await self._sync_service.sync()
            self._last_sync = datetime.now(timezone.utc)
        except Exception:  # noqa: BLE001
            logger.exception("auto list sync failed")
