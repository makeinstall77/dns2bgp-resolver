from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from dns2bgp_resolver.application.commands.dto import ExportSummary, ResolveSummary
from dns2bgp_resolver.application.ports.clock import Clock
from dns2bgp_resolver.application.ports.dns_resolver import DnsResolver
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.ports.route_exporter import RouteExporter
from dns2bgp_resolver.config import RefreshSettings
from dns2bgp_resolver.domain import Domain, DomainName, ip_to_prefix24

logger = logging.getLogger(__name__)


class ResolvePipeline:
    """Orchestrates resolve → persist → coalesced export-if-changed."""

    def __init__(
        self,
        repository: DomainRepository,
        resolver: DnsResolver,
        exporter: RouteExporter,
        clock: Clock,
        refresh: RefreshSettings,
        *,
        export_path: str,
        export_min_interval: float = 60.0,
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._exporter = exporter
        self._clock = clock
        self._refresh = refresh
        self._export_path = export_path
        self._export_min_interval = max(0.0, float(export_min_interval))
        self._export_dirty = False
        self._last_export_mono: float | None = None
        self._export_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._resolve_concurrency = max(1, int(refresh.resolve_concurrency))
        self._resolve_batch_size = max(1, int(refresh.resolve_batch_size))

    def _next_resolve_delay(self, ttl_seconds: int | None) -> timedelta:
        if ttl_seconds is None or ttl_seconds <= 0:
            interval = self._refresh.max_interval
        else:
            interval = min(ttl_seconds, self._refresh.max_interval)
        interval = max(interval, self._refresh.min_interval)
        return timedelta(seconds=interval)

    async def resolve_one(self, name: DomainName) -> ResolveSummary:
        domain = await self._repository.get(name)
        if domain is None or domain.id is None:
            return ResolveSummary(
                domain=str(name),
                addresses=[],
                changed=False,
                exported=False,
                error=f"domain not found: {name}",
            )
        return await self._resolve_domain(domain)

    async def resolve_all(self) -> list[ResolveSummary]:
        domains = [
            d for d in await self._repository.list_all() if d.enabled and d.id is not None
        ]
        return await self._resolve_many(domains)

    async def resolve_due(self) -> list[ResolveSummary]:
        now = self._clock.now()
        due = await self._repository.list_due(now)
        due = [d for d in due if d.id is not None][: self._resolve_batch_size]
        return await self._resolve_many(due)

    async def _resolve_many(self, domains: list[Domain]) -> list[ResolveSummary]:
        if not domains:
            return []
        if self._resolve_concurrency == 1 or len(domains) == 1:
            return [await self._resolve_domain(d) for d in domains]

        sem = asyncio.Semaphore(self._resolve_concurrency)

        async def _one(domain: Domain) -> ResolveSummary:
            async with sem:
                return await self._resolve_domain(domain)

        return list(await asyncio.gather(*(_one(d) for d in domains)))

    async def _resolve_domain(self, domain: Domain) -> ResolveSummary:
        assert domain.id is not None
        now = self._clock.now()
        old_ips = {str(a.ip) for a in domain.addresses}

        try:
            resolved = await self._resolver.resolve_a(domain.name)
        except Exception as exc:  # noqa: BLE001 — surface as domain error
            logger.warning("resolve failed for %s: %s", domain.name, exc)
            next_at = now + self._next_resolve_delay(None)
            await self._repository.mark_resolve_error(
                domain.id, error=str(exc), next_resolve_at=next_at
            )
            return ResolveSummary(
                domain=str(domain.name),
                addresses=sorted(old_ips),
                changed=False,
                exported=False,
                error=str(exc),
            )

        min_ttl = min((a.ttl_seconds for a in resolved), default=self._refresh.max_interval)
        next_at = now + self._next_resolve_delay(min_ttl)
        updated = await self._repository.replace_addresses(
            domain.id,
            resolved,
            resolved_at=now,
            next_resolve_at=next_at,
            error=None,
        )
        new_ips = {str(a.ip) for a in updated.addresses}
        changed = new_ips != old_ips
        exported = False
        if changed:
            exported = await self._request_export()

        return ResolveSummary(
            domain=str(domain.name),
            addresses=sorted(new_ips),
            changed=changed,
            exported=exported,
            error=None,
        )

    async def _request_export(self) -> bool:
        """Mark pool dirty; export now only if min interval elapsed."""
        self._export_dirty = True
        return await self._flush_export(force=False)

    def _schedule_flush(self, delay: float) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return

        async def _run() -> None:
            try:
                await asyncio.sleep(delay)
                await self._flush_export(force=False)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("deferred bird export failed")

        self._flush_task = asyncio.create_task(_run(), name="bird-export-flush")

    async def _flush_export(self, *, force: bool) -> bool:
        async with self._export_lock:
            if not force and not self._export_dirty:
                return False

            now = time.monotonic()
            if not force and self._last_export_mono is not None and self._export_min_interval > 0:
                elapsed = now - self._last_export_mono
                remaining = self._export_min_interval - elapsed
                if remaining > 0:
                    self._schedule_flush(remaining)
                    logger.debug(
                        "bird export deferred (%.1fs remaining, dirty=%s)",
                        remaining,
                        self._export_dirty,
                    )
                    return False

            summary = await self._write_export()
            self._export_dirty = False
            self._last_export_mono = now
            logger.info("bird export flushed (%d prefix(es))", summary.prefix_count)
            return True

    async def _write_export(self) -> ExportSummary:
        ips = await self._repository.all_active_ips()
        prefixes = sorted({ip_to_prefix24(ip) for ip in ips})
        await self._exporter.export(prefixes)
        return ExportSummary(prefix_count=len(prefixes), path=self._export_path)

    async def export_routes(self) -> ExportSummary:
        """Immediate export (CLI/API/startup). Resets coalescing timer."""
        async with self._export_lock:
            summary = await self._write_export()
            self._export_dirty = False
            self._last_export_mono = time.monotonic()
            return summary

    async def export_after_mutation(self) -> ExportSummary:
        """Re-export pool after add/remove without resolving."""
        return await self.export_routes()

    async def flush_pending_export(self) -> None:
        """Flush deferred export if any (e.g. on shutdown)."""
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        if self._export_dirty:
            await self._flush_export(force=True)
