from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import AutoSyncView, CommandResult
from dns2bgp_resolver.application.services.auto_list_sync import AutoListSyncService


@dataclass(frozen=True, slots=True)
class SyncAutoListCommand:
    pass


class SyncAutoListHandler:
    def __init__(self, sync_service: AutoListSyncService) -> None:
        self._sync_service = sync_service

    async def handle(self, command: SyncAutoListCommand) -> CommandResult[AutoSyncView]:
        del command
        result = await self._sync_service.sync()
        view = AutoSyncView(
            added=result.added,
            removed=result.removed,
            skipped_manual=result.skipped_manual,
        )
        return CommandResult.success(
            view,
            message=f"sync: +{result.added} -{result.removed} skipped={result.skipped_manual}",
        )
