from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import AutoSyncView, CommandResult
from dns2bgp_resolver.application.commands.domain_lists import SyncDomainListCommand, SyncDomainListHandler
from dns2bgp_resolver.application.services.auto_list_sync import DomainListSyncService


@dataclass(frozen=True, slots=True)
class SyncAutoListCommand:
    pass


class SyncAutoListHandler:
    def __init__(self, sync_service: DomainListSyncService) -> None:
        self._handler = SyncDomainListHandler(sync_service)

    async def handle(self, command: SyncAutoListCommand) -> CommandResult[AutoSyncView]:
        del command
        return await self._handler.handle(SyncDomainListCommand())
