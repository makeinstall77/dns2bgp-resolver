from __future__ import annotations

import logging
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta

import httpx

from dns2bgp_resolver.application.ports.clock import Clock
from dns2bgp_resolver.application.ports.repository import (
    AutoSyncResult,
    DomainRepository,
    SyncPendingConfirmation,
)
from dns2bgp_resolver.application.ports.sync_alert import SyncAlertNotifier
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


class NullSyncAlertNotifier(SyncAlertNotifier):
    async def notify_dangerous_sync(self, pending: SyncPendingConfirmation) -> None:
        logger.warning(
            "dangerous sync blocked for list %s (%d): remove=%d/%d add=%d (no notifier)",
            pending.list_name,
            pending.list_id,
            pending.would_remove,
            pending.current_count,
            pending.would_add,
        )


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
    needs_confirmation: bool = False
    pending_token: str | None = None
    would_add: int = 0
    would_remove: int = 0
    current_count: int = 0


class DomainListSyncService:
    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
        settings: AutoListSettings,
        clock: Clock,
        downloader: AutoListDownloader | None = None,
        notifier: SyncAlertNotifier | None = None,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._settings = settings
        self._clock = clock
        self._downloader = downloader or HttpxAutoListDownloader()
        self._notifier = notifier or NullSyncAlertNotifier()

    def set_notifier(self, notifier: SyncAlertNotifier) -> None:
        self._notifier = notifier

    def _needs_confirmation(self, current_count: int, would_remove: int, target_count: int) -> bool:
        if current_count > 0 and target_count == 0:
            return True
        if current_count <= 0:
            return False
        ratio = would_remove / current_count
        return ratio >= self._settings.max_removal_ratio

    async def _load_filtered_names(self, domain_list) -> set[str]:
        if domain_list.type == "url":
            if not domain_list.url:
                logger.warning("url list %d has no url", domain_list.id)
                return set()
            logger.info("downloading domain list %s from %s", domain_list.name, domain_list.url)
            text = await self._downloader.download(domain_list.url)
        else:
            if not domain_list.file_content:
                logger.warning("file list %d has no content", domain_list.id)
                return set()
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
        return filtered

    async def _apply_names(self, list_id: int, list_name: str, names: set[str]) -> ListSyncResult:
        result = await self._repository.sync_list_domains(list_id, names)
        await self._repository.mark_list_synced(list_id, self._clock.now())
        logger.info(
            "list %s sync: added=%d removed=%d skipped_manual=%d",
            list_name,
            result.added,
            result.removed,
            result.skipped_manual,
        )
        if result.added or result.removed:
            await self._pipeline.export_after_mutation()
        return ListSyncResult(
            list_id=list_id,
            list_name=list_name,
            added=result.added,
            removed=result.removed,
            skipped_manual=result.skipped_manual,
        )

    async def sync_list(self, list_id: int, *, force: bool = False) -> ListSyncResult | None:
        domain_list = await self._repository.get_domain_list(list_id)
        if domain_list is None:
            logger.warning("domain list not found: %d", list_id)
            return None
        if not domain_list.enabled:
            logger.info("domain list %d disabled, skipping sync", list_id)
            return ListSyncResult(list_id, domain_list.name, 0, 0, 0)

        filtered = await self._load_filtered_names(domain_list)
        preview = await self._repository.preview_list_sync(list_id, filtered)

        if not force and self._needs_confirmation(
            preview.current_count, preview.would_remove, preview.target_count
        ):
            existing = await self._repository.get_sync_pending_by_list(list_id)
            if existing is not None and existing.expires_at > self._clock.now():
                logger.info(
                    "list %s: dangerous sync already pending (token=%s)",
                    domain_list.name,
                    existing.token,
                )
                return ListSyncResult(
                    list_id=list_id,
                    list_name=domain_list.name,
                    added=0,
                    removed=0,
                    skipped_manual=preview.skipped_manual,
                    needs_confirmation=True,
                    pending_token=existing.token,
                    would_add=existing.would_add,
                    would_remove=existing.would_remove,
                    current_count=existing.current_count,
                )

            now = self._clock.now()
            token = secrets.token_urlsafe(16)
            pending = await self._repository.save_sync_pending(
                token=token,
                list_id=list_id,
                list_name=domain_list.name,
                target_names=set(preview.target_names),
                would_add=preview.would_add,
                would_remove=preview.would_remove,
                current_count=preview.current_count,
                created_at=now,
                expires_at=now + timedelta(seconds=self._settings.confirm_ttl_seconds),
            )
            logger.warning(
                "list %s: sync blocked pending confirmation "
                "(remove=%d/%d add=%d target=%d token=%s)",
                domain_list.name,
                preview.would_remove,
                preview.current_count,
                preview.would_add,
                preview.target_count,
                token,
            )
            await self._notifier.notify_dangerous_sync(pending)
            return ListSyncResult(
                list_id=list_id,
                list_name=domain_list.name,
                added=0,
                removed=0,
                skipped_manual=preview.skipped_manual,
                needs_confirmation=True,
                pending_token=token,
                would_add=preview.would_add,
                would_remove=preview.would_remove,
                current_count=preview.current_count,
            )

        return await self._apply_names(list_id, domain_list.name, filtered)

    async def confirm_pending(self, token: str) -> ListSyncResult | None:
        pending = await self._repository.get_sync_pending(token)
        if pending is None:
            return None
        now = self._clock.now()
        if pending.expires_at <= now:
            await self._repository.delete_sync_pending(token)
            return None
        result = await self._apply_names(
            pending.list_id, pending.list_name, set(pending.target_names)
        )
        await self._repository.delete_sync_pending(token)
        return result

    async def cancel_pending(self, token: str) -> bool:
        return await self._repository.delete_sync_pending(token)

    async def cleanup_expired_pending(self) -> int:
        return await self._repository.cleanup_expired_sync_pending(self._clock.now())

    async def sync_all_enabled(self) -> list[ListSyncResult]:
        if not self._settings.enabled:
            logger.info("auto list sync disabled globally")
            return []
        await self.cleanup_expired_pending()
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
