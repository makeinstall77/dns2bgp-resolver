from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    AddDomainHandler,
    AddExcludeKeywordCommand,
    AddExcludeKeywordHandler,
    CommandBus,
    CommandResult,
    DomainView,
    ExportRoutesCommand,
    ExportRoutesHandler,
    ListDomainsCommand,
    ListDomainsHandler,
    ListExcludeKeywordsCommand,
    ListExcludeKeywordsHandler,
    RemoveDomainCommand,
    RemoveDomainHandler,
    RemoveExcludeKeywordCommand,
    RemoveExcludeKeywordHandler,
    ResolveNowCommand,
    ResolveNowHandler,
    SearchAutoDomainsCommand,
    SearchAutoDomainsHandler,
    SyncAutoListCommand,
    SyncAutoListHandler,
    ListDomainListsCommand,
    ListDomainListsHandler,
    AddDomainListCommand,
    AddDomainListHandler,
    UpdateDomainListCommand,
    UpdateDomainListHandler,
    RemoveDomainListCommand,
    RemoveDomainListAndExportHandler,
    ClearDomainListCommand,
    ClearDomainListHandler,
    SyncDomainListCommand,
    SyncDomainListHandler,
    GetSettingsCommand,
    GetSettingsHandler,
    SetDefaultSyncIntervalCommand,
    SetDefaultSyncIntervalHandler,
)
from dns2bgp_resolver.application.commands.dto import domain_to_view
from dns2bgp_resolver.application.ports.clock import SystemClock
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.services.auto_list_sync import DomainListSyncService
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.config import Settings
from dns2bgp_resolver.domain import DomainName
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import StaticFileBirdExporter
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository
from dns2bgp_resolver.infrastructure.dns.dnspython_resolver import DnspythonResolver
from dns2bgp_resolver.infrastructure.scheduling.auto_list_scheduler import AutoListSyncScheduler
from dns2bgp_resolver.infrastructure.scheduling.refresh_scheduler import RefreshScheduler


@dataclass
class AppContainer:
    settings: Settings
    repository: DomainRepository
    bus: CommandBus
    pipeline: ResolvePipeline
    scheduler: RefreshScheduler
    auto_sync_service: DomainListSyncService
    auto_sync_scheduler: AutoListSyncScheduler

    async def startup(self) -> None:
        url = self.settings.database.url
        if url.startswith("sqlite"):
            raw = url.split("///", 1)[-1]
            db_path = Path(raw)
            if not db_path.is_absolute():
                db_path = Path.cwd() / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
        bird_dir = Path(self.settings.bird.include_path).parent
        bird_dir.mkdir(parents=True, exist_ok=True)
        bird_dir.chmod(0o755)
        await self.repository.initialize()
        await self._seed_exclude_keywords()
        await self._seed_domain_lists()

    async def _seed_exclude_keywords(self) -> None:
        existing = await self.repository.list_exclude_keywords()
        if existing:
            return
        for keyword in self.settings.auto_list.exclude_keywords:
            await self.repository.add_exclude_keyword(keyword)

    async def _seed_domain_lists(self) -> None:
        if not hasattr(self.repository, "seed_domain_list"):
            return
        await self.repository.seed_domain_list(  # type: ignore[attr-defined]
            name="antifilter",
            list_type="url",
            url=self.settings.auto_list.url,
            sync_interval=self.settings.auto_list.sync_interval,
        )

    async def shutdown(self) -> None:
        await self.auto_sync_scheduler.stop()
        await self.scheduler.stop()
        await self.repository.close()


class AddDomainAndResolveHandler:
    """Add domain, then resolve immediately so the BGP pool updates."""

    def __init__(self, repository: DomainRepository, pipeline: ResolvePipeline) -> None:
        self._add = AddDomainHandler(repository)
        self._repository = repository
        self._pipeline = pipeline

    async def handle(self, command: AddDomainCommand) -> CommandResult[DomainView]:
        result = await self._add.handle(command)
        if result.ok:
            await self._pipeline.resolve_one(DomainName(command.name))
            updated = await self._repository.get(DomainName(command.name))
            if updated is not None:
                return CommandResult.success(
                    domain_to_view(updated),
                    message=f"added and resolved {command.name}",
                )
        return result


class RemoveDomainAndExportHandler:
    def __init__(self, repository: DomainRepository, pipeline: ResolvePipeline) -> None:
        self._remove = RemoveDomainHandler(repository)
        self._pipeline = pipeline

    async def handle(self, command: RemoveDomainCommand) -> CommandResult[str]:
        result = await self._remove.handle(command)
        if result.ok:
            await self._pipeline.export_after_mutation()
        return result


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or Settings.load()
    repository: DomainRepository = SqlAlchemyDomainRepository(settings.database.url)
    resolver = DnspythonResolver(settings.dns)
    exporter = StaticFileBirdExporter(settings.bird)
    clock = SystemClock()
    pipeline = ResolvePipeline(
        repository=repository,
        resolver=resolver,
        exporter=exporter,
        clock=clock,
        refresh=settings.refresh,
        export_path=settings.bird.include_path,
    )
    auto_sync_service = DomainListSyncService(
        repository=repository,
        pipeline=pipeline,
        settings=settings.auto_list,
        clock=clock,
    )
    bus = CommandBus()
    bus.register(AddDomainCommand, AddDomainAndResolveHandler(repository, pipeline))
    bus.register(RemoveDomainCommand, RemoveDomainAndExportHandler(repository, pipeline))
    bus.register(ListDomainsCommand, ListDomainsHandler(repository))
    bus.register(ResolveNowCommand, ResolveNowHandler(pipeline))
    bus.register(ExportRoutesCommand, ExportRoutesHandler(pipeline))
    bus.register(SearchAutoDomainsCommand, SearchAutoDomainsHandler(repository))
    bus.register(SyncAutoListCommand, SyncAutoListHandler(auto_sync_service))
    bus.register(SyncDomainListCommand, SyncDomainListHandler(auto_sync_service))
    bus.register(ListDomainListsCommand, ListDomainListsHandler(repository))
    bus.register(AddDomainListCommand, AddDomainListHandler(repository))
    bus.register(UpdateDomainListCommand, UpdateDomainListHandler(repository))
    bus.register(RemoveDomainListCommand, RemoveDomainListAndExportHandler(repository, pipeline))
    bus.register(ClearDomainListCommand, ClearDomainListHandler(repository, pipeline))
    bus.register(GetSettingsCommand, GetSettingsHandler(repository))
    bus.register(SetDefaultSyncIntervalCommand, SetDefaultSyncIntervalHandler(repository))
    bus.register(ListExcludeKeywordsCommand, ListExcludeKeywordsHandler(repository))
    bus.register(AddExcludeKeywordCommand, AddExcludeKeywordHandler(repository))
    bus.register(RemoveExcludeKeywordCommand, RemoveExcludeKeywordHandler(repository))

    scheduler = RefreshScheduler(pipeline)
    auto_sync_scheduler = AutoListSyncScheduler(
        auto_sync_service,
        repository,
        sync_on_startup=settings.auto_list.sync_on_startup,
    )
    return AppContainer(
        settings=settings,
        repository=repository,
        bus=bus,
        pipeline=pipeline,
        scheduler=scheduler,
        auto_sync_service=auto_sync_service,
        auto_sync_scheduler=auto_sync_scheduler,
    )
