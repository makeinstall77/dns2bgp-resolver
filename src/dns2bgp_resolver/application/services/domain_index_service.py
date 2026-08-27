from __future__ import annotations

import logging

from dns2bgp_resolver.application.ports.ipv6_policy import Ipv6Policy
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.domain.domain_index import DomainIndex

logger = logging.getLogger(__name__)


class DomainIndexService:
    """Rebuilds the hot-path DomainIndex from the repository."""

    def __init__(
        self,
        repository: DomainRepository,
        index: DomainIndex,
        ipv6_policy: Ipv6Policy | None = None,
    ) -> None:
        self._repository = repository
        self._index = index
        self._ipv6_policy = ipv6_policy

    @property
    def index(self) -> DomainIndex:
        return self._index

    async def rebuild(self) -> int:
        rules = await self._repository.list_index_rules()
        size = self._index.rebuild(rules=rules)
        logger.info("domain index rebuilt: %d name(s)", size)
        if self._ipv6_policy is not None:
            names = await self._repository.list_ipv6_suppress_names()
            await self._ipv6_policy.apply(self._index, suppress_names=names)
        return size
