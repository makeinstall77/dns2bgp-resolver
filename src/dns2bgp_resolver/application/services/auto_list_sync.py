from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from dns2bgp_resolver.application.ports.clock import Clock
from dns2bgp_resolver.application.ports.repository import AutoSyncResult, DomainRepository
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.config import AutoListSettings
from dns2bgp_resolver.domain import DomainName

logger = logging.getLogger(__name__)


class AutoListDownloader(ABC):
    @abstractmethod
    async def download(self, url: str) -> str: ...


class HttpxAutoListDownloader(AutoListDownloader):
    def __init__(self, *, timeout: float = 60.0) -> None:
        self._timeout = timeout

    async def download(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text


def parse_domain_lines(text: str) -> tuple[set[str], int]:
    names: set[str] = set()
    skipped = 0
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            names.add(str(DomainName(raw)))
        except ValueError:
            skipped += 1
            logger.debug("skipped invalid domain line: %r", raw)
    return names, skipped


def apply_keyword_filter(names: set[str], keywords: list[str]) -> set[str]:
    if not keywords:
        return names
    lowered = [k.lower() for k in keywords if k.strip()]
    if not lowered:
        return names
    return {n for n in names if not any(kw in n for kw in lowered)}


@dataclass(frozen=True, slots=True)
class ListSyncResult:
    list_id: int
    list_name: str
    added: int
    removed: int
    skipped_manual: int


class DomainListSyncService:
    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
        settings: AutoListSettings,
        clock: Clock,
        downloader: AutoListDownloader | None = None,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._settings = settings
        self._clock = clock
        self._downloader = downloader or HttpxAutoListDownloader()

    async def sync_list(self, list_id: int) -> ListSyncResult | None:
        domain_list = await self._repository.get_domain_list(list_id)
        if domain_list is None:
            logger.warning("domain list not found: %d", list_id)
            return None
        if not domain_list.enabled:
            logger.info("domain list %d disabled, skipping sync", list_id)
            return ListSyncResult(list_id, domain_list.name, 0, 0, 0)

        if domain_list.type == "url":
            if not domain_list.url:
                logger.warning("url list %d has no url", list_id)
                return ListSyncResult(list_id, domain_list.name, 0, 0, 0)
            logger.info("downloading domain list %s from %s", domain_list.name, domain_list.url)
            text = await self._downloader.download(domain_list.url)
        else:
            if not domain_list.file_content:
                logger.warning("file list %d has no content", list_id)
                return ListSyncResult(list_id, domain_list.name, 0, 0, 0)
            text = domain_list.file_content

        names, skipped_invalid = parse_domain_lines(text)
        logger.info(
            "list %s: parsed %d domain(s), skipped %d invalid",
            domain_list.name,
            len(names),
            skipped_invalid,
        )

        keywords = await self._repository.list_exclude_keywords()
        filtered = apply_keyword_filter(names, keywords)
        excluded = len(names) - len(filtered)
        if excluded:
            logger.info("keyword filter excluded %d domain(s)", excluded)

        result = await self._repository.sync_list_domains(list_id, filtered)
        await self._repository.mark_list_synced(list_id, self._clock.now())
        logger.info(
            "list %s sync: added=%d removed=%d skipped_manual=%d",
            domain_list.name,
            result.added,
            result.removed,
            result.skipped_manual,
        )
        if result.added or result.removed:
            await self._pipeline.export_after_mutation()
        return ListSyncResult(
            list_id=list_id,
            list_name=domain_list.name,
            added=result.added,
            removed=result.removed,
            skipped_manual=result.skipped_manual,
        )

    async def sync_all_enabled(self) -> list[ListSyncResult]:
        if not self._settings.enabled:
            logger.info("auto list sync disabled globally")
            return []
        results: list[ListSyncResult] = []
        for domain_list in await self._repository.list_domain_lists():
            if not domain_list.enabled:
                continue
            synced = await self.sync_list(domain_list.id)
            if synced is not None:
                results.append(synced)
        return results

    async def sync(self) -> AutoSyncResult:
        results = await self.sync_all_enabled()
        return AutoSyncResult(
            added=sum(r.added for r in results),
            removed=sum(r.removed for r in results),
            skipped_manual=sum(r.skipped_manual for r in results),
        )


AutoListSyncService = DomainListSyncService
