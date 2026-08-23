from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult, SettingsView
from dns2bgp_resolver.application.ports.repository import DomainRepository


@dataclass(frozen=True, slots=True)
class GetSettingsCommand:
    pass


class GetSettingsHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: GetSettingsCommand) -> CommandResult[SettingsView]:
        del command
        interval = await self._repository.get_default_sync_interval()
        return CommandResult.success(SettingsView(default_sync_interval=interval))


@dataclass(frozen=True, slots=True)
class SetDefaultSyncIntervalCommand:
    seconds: int


class SetDefaultSyncIntervalHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: SetDefaultSyncIntervalCommand) -> CommandResult[SettingsView]:
        if command.seconds < 60:
            return CommandResult.failure("sync interval must be at least 60 seconds")
        await self._repository.set_default_sync_interval(command.seconds)
        return CommandResult.success(
            SettingsView(default_sync_interval=command.seconds),
            message=f"default sync interval set to {command.seconds}s",
        )
