from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from dns2bgp_resolver.domain.domain_index import DomainIndex


class Ipv6Policy(ABC):
    """Apply IPv6 handling for names in DomainIndex (suppress / announce / off)."""

    @abstractmethod
    async def apply(
        self,
        index: DomainIndex,
        *,
        suppress_names: Iterable[str] | None = None,
    ) -> None:
        """Run after index rebuild. Must not raise into the rebuild path."""
