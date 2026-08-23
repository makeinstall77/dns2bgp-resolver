from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from dns2bgp_resolver.domain import Domain, DomainList, DomainName, ResolvedAddress


DEFAULT_SYNC_INTERVAL_KEY = "default_sync_interval"
DEFAULT_SYNC_INTERVAL_SECONDS = 86400


@dataclass(frozen=True, slots=True)
class AutoSyncResult:
    added: int
    removed: int
    skipped_manual: int


@dataclass(frozen=True, slots=True)
class DomainListCreate:
    name: str
    type: str
    url: str | None = None
    file_content: str | None = None
    enabled: bool = True
    sync_interval: int | None = None


@dataclass(frozen=True, slots=True)
class DomainListUpdate:
    name: str | None = None
    enabled: bool | None = None
    sync_interval: int | None = None
    url: str | None = None
    file_content: str | None = None
    unset_sync_interval: bool = False


class DomainRepository(ABC):
    """Persistence port — SQLite now, PostgreSQL (or any store) later."""

    @abstractmethod
    async def initialize(self) -> None:
        """Prepare schema / connection."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""

    @abstractmethod
    async def add(self, domain: Domain) -> Domain:
        """Insert domain; raise DomainAlreadyExistsError if duplicate."""

    @abstractmethod
    async def remove(self, name: DomainName) -> bool:
        """Delete domain and its addresses. Return False if missing."""

    @abstractmethod
    async def get(self, name: DomainName) -> Domain | None:
        ...

    @abstractmethod
    async def list_all(self) -> list[Domain]:
        ...

    @abstractmethod
    async def list_manual(self) -> list[Domain]:
        ...

    @abstractmethod
    async def search_auto(
        self, query: str, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[Domain], int]:
        ...

    @abstractmethod
    async def sync_auto_domains(self, names: set[str]) -> AutoSyncResult:
        """Replace auto domain set; skip names that exist as manual."""

    @abstractmethod
    async def sync_list_domains(self, list_id: int, names: set[str]) -> AutoSyncResult:
        """Replace domains for one list; skip names that exist as manual."""

    @abstractmethod
    async def clear_list_domains(self, list_id: int) -> int:
        """Remove all domains belonging to list_id. Return count removed."""

    @abstractmethod
    async def list_domain_lists(self) -> list[DomainList]:
        ...

    @abstractmethod
    async def get_domain_list(self, list_id: int) -> DomainList | None:
        ...

    @abstractmethod
    async def add_domain_list(self, data: DomainListCreate) -> DomainList:
        ...

    @abstractmethod
    async def update_domain_list(self, list_id: int, data: DomainListUpdate) -> DomainList | None:
        ...

    @abstractmethod
    async def remove_domain_list(self, list_id: int) -> bool:
        """Delete list and its domains."""

    @abstractmethod
    async def mark_list_synced(self, list_id: int, synced_at: datetime) -> None:
        ...

    @abstractmethod
    async def get_default_sync_interval(self) -> int:
        ...

    @abstractmethod
    async def set_default_sync_interval(self, seconds: int) -> None:
        ...

    @abstractmethod
    async def list_exclude_keywords(self) -> list[str]:
        ...

    @abstractmethod
    async def add_exclude_keyword(self, keyword: str) -> bool:
        """Return False if keyword already exists."""

    @abstractmethod
    async def remove_exclude_keyword(self, keyword: str) -> bool:
        """Return False if keyword not found."""

    @abstractmethod
    async def list_due(self, now: datetime) -> list[Domain]:
        """Enabled domains with next_resolve_at <= now (or never resolved)."""

    @abstractmethod
    async def replace_addresses(
        self,
        domain_id: int,
        addresses: list[ResolvedAddress],
        *,
        resolved_at: datetime,
        next_resolve_at: datetime,
        error: str | None = None,
    ) -> Domain:
        """Replace address set for a domain (replace-set policy)."""

    @abstractmethod
    async def mark_resolve_error(
        self,
        domain_id: int,
        *,
        error: str,
        next_resolve_at: datetime,
    ) -> None:
        ...

    @abstractmethod
    async def all_active_ips(self) -> list[str]:
        """All IPv4 addresses for enabled domains (for bird export)."""
