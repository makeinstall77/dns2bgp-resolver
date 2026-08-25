from __future__ import annotations

import logging

from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.domain.domain_index import DomainIndex

logger = logging.getLogger(__name__)


class DomainIndexService:
    """Rebuilds the hot-path DomainIndex from the repository."""

    def __init__(self, repository: DomainRepository, index: DomainIndex) -> None:
        self._repository = repository
        self._index = index

    @property
    def index(self) -> DomainIndex:
        return self._index

    async def rebuild(self) -> int:
        rules = await self._repository.list_index_rules()
        size = self._index.rebuild(rules=rules)
        logger.info("domain index rebuilt: %d name(s)", size)
        return size
