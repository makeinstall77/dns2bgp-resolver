from __future__ import annotations

import logging
from collections.abc import Iterable

from dns2bgp_resolver.application.ports.ipv6_policy import Ipv6Policy
from dns2bgp_resolver.config import Ipv6Settings
from dns2bgp_resolver.domain.domain_index import DomainIndex
from dns2bgp_resolver.infrastructure.dns.unbound_cache_flusher import UnboundCacheFlusher
from dns2bgp_resolver.infrastructure.dnsdist.domain_list_exporter import DnsdistDomainListExporter

logger = logging.getLogger(__name__)


class ModeBasedIpv6Policy(Ipv6Policy):
    """
    off — no-op.
    suppress — export domain list for dnsdist AAAA NODATA.
    announce — stub (future Bird IPv6); log only.
    """

    def __init__(
        self,
        settings: Ipv6Settings,
        exporter: DnsdistDomainListExporter | None = None,
        cache_flusher: UnboundCacheFlusher | None = None,
    ) -> None:
        self._settings = settings
        self._exporter = exporter or DnsdistDomainListExporter(settings)
        self._cache_flusher = cache_flusher or UnboundCacheFlusher(settings)
        self._last_suppress: frozenset[str] = frozenset()
        self._seen_export = False

    async def apply(
        self,
        index: DomainIndex,
        *,
        suppress_names: Iterable[str] | None = None,
    ) -> None:
        mode = self._settings.mode
        try:
            if mode == "off":
                return
            if mode == "suppress":
                names = frozenset(
                    n.strip(".").lower()
                    for n in (
                        suppress_names
                        if suppress_names is not None
                        else index.names_snapshot()
                    )
                    if n and n.strip(".")
                )
                await self._exporter.export(names)
                if self._seen_export:
                    added = names - self._last_suppress
                    if added:
                        await self._cache_flusher.flush_zones(sorted(added))
                self._last_suppress = names
                self._seen_export = True
                return
            if mode == "announce":
                logger.info(
                    "ipv6.mode=announce is not implemented yet "
                    "(AAAA pool + Bird master6); DomainIndex size=%d",
                    index.size,
                )
                return
            logger.warning("unknown ipv6.mode=%r — no-op", mode)
        except Exception:  # noqa: BLE001
            logger.exception("ipv6 policy apply failed (mode=%s)", mode)
