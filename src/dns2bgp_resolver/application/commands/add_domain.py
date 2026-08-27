from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult, DomainView, domain_to_view
from dns2bgp_resolver.application.errors import DomainAlreadyExistsError, ValidationError
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.domain import Domain


@dataclass(frozen=True, slots=True)
class AddDomainCommand:
    name: str


class AddDomainHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: AddDomainCommand) -> CommandResult[DomainView]:
        try:
            manual_default, _ = await self._repository.get_suppress_ipv6_defaults()
            domain = Domain.create(
                command.name, source="manual", suppress_ipv6=manual_default
            )
        except ValueError as exc:
            return CommandResult.failure(str(exc))

        try:
            saved = await self._repository.add(domain)
        except DomainAlreadyExistsError as exc:
            return CommandResult.failure(str(exc))
        except ValidationError as exc:
            return CommandResult.failure(str(exc))

        return CommandResult.success(domain_to_view(saved), message=f"added {saved.name}")
