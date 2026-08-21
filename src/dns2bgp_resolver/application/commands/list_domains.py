from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult, DomainView, domain_to_view
from dns2bgp_resolver.application.ports.repository import DomainRepository


@dataclass(frozen=True, slots=True)
class ListDomainsCommand:
    pass


class ListDomainsHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: ListDomainsCommand) -> CommandResult[list[DomainView]]:
        del command
        domains = await self._repository.list_all()
        views = [domain_to_view(d) for d in domains]
        return CommandResult.success(views, message=f"{len(views)} domain(s)")
