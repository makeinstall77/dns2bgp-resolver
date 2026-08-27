from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult, DomainView, domain_to_view
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.services.domain_index_service import DomainIndexService


@dataclass(frozen=True, slots=True)
class SetSuppressIpv6Command:
    domain_id: int
    suppress: bool


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
        updated = await self._repository.set_suppress_ipv6(command.domain_id, command.suppress)
        if updated is None:
            return CommandResult.failure("domain not found")
        if self._index_service is not None:
            await self._index_service.rebuild()
        state = "выкл" if updated.suppress_ipv6 else "вкл"
        return CommandResult.success(
            domain_to_view(updated),
            message=f"IPv6 AAAA: {state}",
        )
