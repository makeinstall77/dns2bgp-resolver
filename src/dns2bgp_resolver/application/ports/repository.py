from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from dns2bgp_resolver.domain import Domain, DomainName, ResolvedAddress


@dataclass(frozen=True, slots=True)
class AutoSyncResult:
    added: int
    removed: int
    skipped_manual: int


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
