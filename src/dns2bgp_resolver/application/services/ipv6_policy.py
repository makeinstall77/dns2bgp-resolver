from __future__ import annotations

import logging

from dns2bgp_resolver.application.ports.ipv6_policy import Ipv6Policy
from dns2bgp_resolver.config import Ipv6Settings
from dns2bgp_resolver.domain.domain_index import DomainIndex
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
    ) -> None:
        self._settings = settings
        self._exporter = exporter or DnsdistDomainListExporter(settings)

    async def apply(self, index: DomainIndex) -> None:
        mode = self._settings.mode
        try:
            if mode == "off":
                return
            if mode == "suppress":
                await self._exporter.export(index.names_snapshot())
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
