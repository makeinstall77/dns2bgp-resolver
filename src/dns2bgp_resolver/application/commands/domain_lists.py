from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import AutoSyncView, CommandResult, DomainListView
from dns2bgp_resolver.application.ports.repository import (
    DomainListCreate,
    DomainListUpdate,
    DomainRepository,
)
from dns2bgp_resolver.application.services.auto_list_sync import DomainListSyncService
from dns2bgp_resolver.application.services.domain_index_service import DomainIndexService
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline


def domain_list_to_view(domain_list, *, domain_count: int = 0) -> DomainListView:
    return DomainListView(
        id=domain_list.id,
        name=domain_list.name,
        type=domain_list.type,
        url=domain_list.url,
        enabled=domain_list.enabled,
        sync_interval=domain_list.sync_interval,
        last_sync_at=domain_list.last_sync_at.isoformat() if domain_list.last_sync_at else None,
        created_at=domain_list.created_at.isoformat() if domain_list.created_at else None,
        domain_count=domain_count,
        has_file=bool(domain_list.file_content),
    )


@dataclass(frozen=True, slots=True)
class ListDomainListsCommand:
    pass


class ListDomainListsHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: ListDomainListsCommand) -> CommandResult[list[DomainListView]]:
        del command
        lists = await self._repository.list_domain_lists()
        default_interval = await self._repository.get_default_sync_interval()
        views = [domain_list_to_view(item) for item in lists]
        return CommandResult.success(views, message=str(default_interval))


@dataclass(frozen=True, slots=True)
class AddDomainListCommand:
    name: str
    type: str
    url: str | None = None
    file_content: str | None = None
    sync_interval: int | None = None


class AddDomainListHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: AddDomainListCommand) -> CommandResult[DomainListView]:
        name = command.name.strip()
        if not name:
            return CommandResult.failure("name is required")
        if command.type not in ("url", "file"):
            return CommandResult.failure("type must be url or file")
        if command.type == "url" and not (command.url or "").strip():
            return CommandResult.failure("url is required for url lists")
        if command.type == "file" and not (command.file_content or "").strip():
            return CommandResult.failure("file content is required for file lists")

        created = await self._repository.add_domain_list(
            DomainListCreate(
                name=name,
                type=command.type,
                url=command.url.strip() if command.url else None,
                file_content=command.file_content if command.type == "file" else None,
                sync_interval=command.sync_interval,
            )
        )
        return CommandResult.success(
            domain_list_to_view(created),
            message=f"added list {name}",
        )


@dataclass(frozen=True, slots=True)
class UpdateDomainListCommand:
    id: int
    name: str | None = None
    enabled: bool | None = None
    sync_interval: int | None = None
    url: str | None = None
    file_content: str | None = None
    clear_sync_interval: bool = False


class UpdateDomainListHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: UpdateDomainListCommand) -> CommandResult[DomainListView]:
        update = DomainListUpdate(
            name=command.name.strip() if command.name else None,
            enabled=command.enabled,
            url=command.url,
            file_content=command.file_content,
            sync_interval=command.sync_interval,
            unset_sync_interval=command.clear_sync_interval,
        )

        updated = await self._repository.update_domain_list(command.id, update)
        if updated is None:
            return CommandResult.failure(f"list not found: {command.id}")
        return CommandResult.success(domain_list_to_view(updated), message=f"updated list {updated.name}")


@dataclass(frozen=True, slots=True)
class RemoveDomainListCommand:
    id: int


class RemoveDomainListAndExportHandler:
    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
        index_service: DomainIndexService | None = None,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._index_service = index_service

    async def handle(self, command: RemoveDomainListCommand) -> CommandResult[str]:
        existing = await self._repository.get_domain_list(command.id)
        if existing is None:
            return CommandResult.failure(f"list not found: {command.id}")
        await self._repository.clear_list_domains(command.id)
        removed = await self._repository.remove_domain_list(command.id)
        if not removed:
            return CommandResult.failure(f"list not found: {command.id}")
        if self._index_service is not None:
            await self._index_service.rebuild()
        await self._pipeline.export_after_mutation()
        return CommandResult.success(existing.name, message=f"removed list {existing.name}")


@dataclass(frozen=True, slots=True)
class ClearDomainListCommand:
    id: int


class ClearDomainListHandler:
    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
        index_service: DomainIndexService | None = None,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._index_service = index_service

    async def handle(self, command: ClearDomainListCommand) -> CommandResult[int]:
        existing = await self._repository.get_domain_list(command.id)
        if existing is None:
            return CommandResult.failure(f"list not found: {command.id}")
        count = await self._repository.clear_list_domains(command.id)
        if self._index_service is not None:
            await self._index_service.rebuild()
        await self._pipeline.export_after_mutation()
        return CommandResult.success(count, message=f"cleared {count} domain(s) from {existing.name}")


@dataclass(frozen=True, slots=True)
class SyncDomainListCommand:
    id: int | None = None


class SyncDomainListHandler:
    def __init__(self, sync_service: DomainListSyncService) -> None:
        self._sync_service = sync_service

    async def handle(self, command: SyncDomainListCommand) -> CommandResult[AutoSyncView]:
        if command.id is not None:
            result = await self._sync_service.sync_list(command.id)
            if result is None:
                return CommandResult.failure(f"list not found: {command.id}")
            view = AutoSyncView(
                added=result.added,
                removed=result.removed,
                skipped_manual=result.skipped_manual,
                list_id=result.list_id,
                list_name=result.list_name,
                needs_confirmation=result.needs_confirmation,
                pending_token=result.pending_token,
                would_add=result.would_add,
                would_remove=result.would_remove,
                current_count=result.current_count,
            )
            if result.needs_confirmation:
                return CommandResult.success(
                    view,
                    message=(
                        f"sync {result.list_name} blocked: "
                        f"remove {result.would_remove}/{result.current_count}, "
                        f"add {result.would_add} — confirm in Telegram"
                    ),
                )
            return CommandResult.success(
                view,
                message=f"sync {result.list_name}: +{result.added} -{result.removed}",
            )

        aggregate = await self._sync_service.sync()
        view = AutoSyncView(
            added=aggregate.added,
            removed=aggregate.removed,
            skipped_manual=aggregate.skipped_manual,
        )
        return CommandResult.success(
            view,
            message=f"sync all: +{aggregate.added} -{aggregate.removed}",
        )
