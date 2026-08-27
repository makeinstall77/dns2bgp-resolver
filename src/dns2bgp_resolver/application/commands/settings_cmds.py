from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dns2bgp_resolver.application.commands.dto import CommandResult, SettingsView
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.services.domain_index_service import DomainIndexService


async def _load_settings(repository: DomainRepository) -> SettingsView:
    interval = await repository.get_default_sync_interval()
    manual, auto = await repository.get_suppress_ipv6_defaults()
    return SettingsView(
        default_sync_interval=interval,
        suppress_ipv6_manual_default=manual,
        suppress_ipv6_auto_default=auto,
    )


@dataclass(frozen=True, slots=True)
class GetSettingsCommand:
    pass


class GetSettingsHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: GetSettingsCommand) -> CommandResult[SettingsView]:
        del command
        return CommandResult.success(await _load_settings(self._repository))


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
        view = await _load_settings(self._repository)
        return CommandResult.success(
            view,
            message=f"default sync interval set to {command.seconds}s",
        )


@dataclass(frozen=True, slots=True)
class SetSuppressIpv6DefaultCommand:
    scope: Literal["manual", "auto"]
    enabled: bool


class SetSuppressIpv6DefaultHandler:
    def __init__(
        self,
        repository: DomainRepository,
        index_service: DomainIndexService | None = None,
    ) -> None:
        self._repository = repository
        self._index_service = index_service

    async def handle(
        self, command: SetSuppressIpv6DefaultCommand
    ) -> CommandResult[SettingsView]:
        if command.scope == "manual":
            await self._repository.set_suppress_ipv6_manual_default(command.enabled)
        else:
            await self._repository.set_suppress_ipv6_auto_default(command.enabled)
            if self._index_service is not None:
                await self._index_service.rebuild()
        view = await _load_settings(self._repository)
        label = "Manual" if command.scope == "manual" else "Auto"
        state = "вкл" if command.enabled else "выкл"
        return CommandResult.success(
            view,
            message=f"{label} AAAA suppress: {state}",
        )
