from __future__ import annotations

import logging

from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.domain.domain_index import DomainIndex

logger = logging.getLogger(__name__)


class PassiveDnsCollector:
    """Match DNS responses against DomainIndex and feed the prefix pool."""

    def __init__(self, index: DomainIndex, pipeline: ResolvePipeline) -> None:
        self._index = index
        self._pipeline = pipeline
        self._hits = 0
        self._matched = 0

    @property
    def stats(self) -> tuple[int, int]:
        return self._hits, self._matched

    async def on_response(self, qname: str, ips: list[str]) -> None:
        self._hits += 1
        matched = self._index.matches(qname)
        if matched is None:
            return
        self._matched += 1
        for ip in ips:
            is_new = await self._pipeline.record_passive_hit(ip, matched)
            if is_new:
                logger.info("passive hit %s → %s (rule %s)", qname, ip, matched)
