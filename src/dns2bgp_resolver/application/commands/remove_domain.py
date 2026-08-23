from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.domain import DomainName


@dataclass(frozen=True, slots=True)
class RemoveDomainCommand:
    name: str


class RemoveDomainHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: RemoveDomainCommand) -> CommandResult[str]:
        try:
            name = DomainName(command.name)
        except ValueError as exc:
            return CommandResult.failure(str(exc))

        existing = await self._repository.get(name)
        if existing is None:
            return CommandResult.failure(f"domain not found: {name}")
        if existing.source == "auto":
            return CommandResult.failure(f"domain is managed by a domain list: {name}")

        removed = await self._repository.remove(name)
        if not removed:
            return CommandResult.failure(f"domain not found: {name}")
        return CommandResult.success(str(name), message=f"removed {name}")
