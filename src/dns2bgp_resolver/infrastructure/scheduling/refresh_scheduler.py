from __future__ import annotations

import asyncio
import logging

from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline

logger = logging.getLogger(__name__)


class RefreshScheduler:
    """Periodically resolve domains whose next_resolve_at is due."""

    def __init__(self, pipeline: ResolvePipeline, *, poll_interval: float = 15.0) -> None:
        self._pipeline = pipeline
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="refresh-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("refresh scheduler started (poll=%.1fs)", self._poll_interval)
        while not self._stop.is_set():
            try:
                summaries = await self._pipeline.resolve_due()
                if summaries:
                    logger.info("scheduler resolved %d domain(s)", len(summaries))
                    for s in summaries:
                        if s.error:
                            logger.warning("%s: %s", s.domain, s.error)
                        elif s.changed:
                            logger.info("%s updated → %s", s.domain, s.addresses)
            except Exception:  # noqa: BLE001
                logger.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue
        logger.info("refresh scheduler stopped")
