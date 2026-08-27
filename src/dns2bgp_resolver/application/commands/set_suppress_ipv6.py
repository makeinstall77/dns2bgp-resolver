from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult, DomainView, domain_to_view
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.services.domain_index_service import DomainIndexService
from dns2bgp_resolver.domain import Ipv6SuppressMode, next_ipv6_suppress_mode


@dataclass(frozen=True, slots=True)
class SetSuppressIpv6Command:
    domain_id: int
    mode: Ipv6SuppressMode | None = None
    """If None, cycle default → on → off → default."""


class SetSuppressIpv6Handler:
    def __init__(
        self,
        repository: DomainRepository,
        index_service: DomainIndexService | None = None,
    ) -> None:
        self._repository = repository
        self._index_service = index_service

    async def handle(self, command: SetSuppressIpv6Command) -> CommandResult[DomainView]:
        domain = await self._repository.get_by_id(command.domain_id)
        if domain is None:
            return CommandResult.failure("domain not found")
        if domain.source != "manual":
            return CommandResult.failure("only manual domains support IPv6 toggle")
        mode = command.mode or next_ipv6_suppress_mode(domain.suppress_ipv6)
        updated = await self._repository.set_suppress_ipv6(command.domain_id, mode)
        if updated is None:
            return CommandResult.failure("domain not found")
        if self._index_service is not None:
            await self._index_service.rebuild()
        labels = {
            "default": "дефолт",
            "on": "выкл (блокируем)",
            "off": "вкл (отдаём)",
        }
        return CommandResult.success(
            domain_to_view(updated),
            message=f"AAAA: {labels.get(updated.suppress_ipv6, updated.suppress_ipv6)}",
        )
