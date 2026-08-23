from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

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


class AutoListSyncService:
    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
        settings: AutoListSettings,
        downloader: AutoListDownloader | None = None,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._settings = settings
        self._downloader = downloader or HttpxAutoListDownloader()

    async def sync(self) -> AutoSyncResult:
        if not self._settings.enabled:
            logger.info("auto list sync disabled")
            return AutoSyncResult(added=0, removed=0, skipped_manual=0)

        logger.info("downloading auto domain list from %s", self._settings.url)
        text = await self._downloader.download(self._settings.url)
        names, skipped_invalid = parse_domain_lines(text)
        logger.info("parsed %d domain(s), skipped %d invalid", len(names), skipped_invalid)

        keywords = await self._repository.list_exclude_keywords()
        filtered = apply_keyword_filter(names, keywords)
        excluded = len(names) - len(filtered)
        if excluded:
            logger.info("keyword filter excluded %d domain(s)", excluded)

        result = await self._repository.sync_auto_domains(filtered)
        logger.info(
            "auto sync: added=%d removed=%d skipped_manual=%d",
            result.added,
            result.removed,
            result.skipped_manual,
        )
        if result.added or result.removed:
            await self._pipeline.export_after_mutation()
        return result
