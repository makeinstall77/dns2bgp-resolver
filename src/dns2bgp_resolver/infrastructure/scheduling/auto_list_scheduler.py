from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from dns2bgp_resolver.application.services.auto_list_sync import DomainListSyncService
from dns2bgp_resolver.application.ports.repository import DomainRepository

logger = logging.getLogger(__name__)


class AutoListSyncScheduler:
    """Periodically sync enabled domain lists."""

    def __init__(
        self,
        sync_service: DomainListSyncService,
        repository: DomainRepository,
        *,
        sync_on_startup: bool = True,
        poll_interval: float = 60.0,
    ) -> None:
        self._sync_service = sync_service
        self._repository = repository
        self._sync_on_startup = sync_on_startup
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

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
        await self._sync_service.sync_all_enabled()

    async def _run(self) -> None:
        logger.info("domain list scheduler started (poll=%.1fs)", self._poll_interval)
        if self._sync_on_startup:
            await self._sync_due_lists(force_all=True)

        while not self._stop.is_set():
            await self._sync_due_lists(force_all=False)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue
        logger.info("domain list scheduler stopped")

    async def _sync_due_lists(self, *, force_all: bool) -> None:
        now = datetime.now(timezone.utc)
        default_interval = await self._repository.get_default_sync_interval()
        lists = await self._repository.list_domain_lists()
        for domain_list in lists:
            if not domain_list.enabled:
                continue
            interval = domain_list.sync_interval or default_interval
            if not force_all and domain_list.last_sync_at is not None:
                elapsed = (now - domain_list.last_sync_at).total_seconds()
                if elapsed < interval:
                    continue
            try:
                await self._sync_service.sync_list(domain_list.id)
            except Exception:  # noqa: BLE001
                logger.exception("domain list sync failed for %s", domain_list.name)
