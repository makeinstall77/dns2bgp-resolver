from __future__ import annotations

from abc import ABC, abstractmethod

from dns2bgp_resolver.domain import DomainName, ResolvedAddress


class DnsResolver(ABC):
    """DNS lookup port. IPv4 (A) only for now; extend for AAAA later."""

    @abstractmethod
    async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
        """Return A records with TTLs. Empty list if NXDOMAIN / no answers."""
