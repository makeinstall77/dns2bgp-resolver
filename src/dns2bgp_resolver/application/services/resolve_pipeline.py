from __future__ import annotations

import logging
from datetime import timedelta

from dns2bgp_resolver.application.commands.dto import ExportSummary, ResolveSummary
from dns2bgp_resolver.application.ports.clock import Clock
from dns2bgp_resolver.application.ports.dns_resolver import DnsResolver
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.ports.route_exporter import RouteExporter
from dns2bgp_resolver.config import RefreshSettings
from dns2bgp_resolver.domain import Domain, DomainName

logger = logging.getLogger(__name__)


class ResolvePipeline:
    """Orchestrates resolve → persist → export-if-changed."""

    def __init__(
        self,
        repository: DomainRepository,
        resolver: DnsResolver,
        exporter: RouteExporter,
        clock: Clock,
        refresh: RefreshSettings,
        *,
        export_path: str,
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._exporter = exporter
        self._clock = clock
        self._refresh = refresh
        self._export_path = export_path

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
        domains = await self._repository.list_all()
        results: list[ResolveSummary] = []
        for domain in domains:
            if not domain.enabled or domain.id is None:
                continue
            results.append(await self._resolve_domain(domain))
        return results

    async def resolve_due(self) -> list[ResolveSummary]:
        now = self._clock.now()
        due = await self._repository.list_due(now)
        results: list[ResolveSummary] = []
        for domain in due:
            if domain.id is None:
                continue
            results.append(await self._resolve_domain(domain))
        return results

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
            await self.export_routes()
            exported = True

        return ResolveSummary(
            domain=str(domain.name),
            addresses=sorted(new_ips),
            changed=changed,
            exported=exported,
            error=None,
        )

    async def export_routes(self) -> ExportSummary:
        ips = await self._repository.all_active_ips()
        prefixes = [f"{ip}/32" for ip in sorted(set(ips))]
        await self._exporter.export(prefixes)
        return ExportSummary(prefix_count=len(prefixes), path=self._export_path)

    async def export_after_mutation(self) -> ExportSummary:
        """Re-export pool after add/remove without resolving."""
        return await self.export_routes()
