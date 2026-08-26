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
from dns2bgp_resolver.application.commands.prefixes import (
    AddPrefixCommand,
    AddPrefixHandler,
    ListPrefixesCommand,
    ListPrefixesHandler,
    RemovePrefixCommand,
    RemovePrefixHandler,
)
from dns2bgp_resolver.application.ports.clock import SystemClock
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.ports.sync_alert import SyncAlertNotifier
from dns2bgp_resolver.application.services.auto_list_sync import (
    DomainListSyncService,
    NullSyncAlertNotifier,
)
from dns2bgp_resolver.application.services.domain_index_service import DomainIndexService
from dns2bgp_resolver.application.services.passive_dns import PassiveDnsCollector
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.config import Settings
from dns2bgp_resolver.domain import DomainName
from dns2bgp_resolver.domain.domain_index import DomainIndex
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import StaticFileBirdExporter
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository
from dns2bgp_resolver.infrastructure.dns.dnspython_resolver import DnspythonResolver
from dns2bgp_resolver.infrastructure.dnstap.consumer import DnstapUnixServer
from dns2bgp_resolver.infrastructure.scheduling.auto_list_scheduler import AutoListSyncScheduler
from dns2bgp_resolver.infrastructure.scheduling.refresh_scheduler import RefreshScheduler
from dns2bgp_resolver.interfaces.telegram.sync_alert import TelegramSyncAlertNotifier


@dataclass
class AppContainer:
    settings: Settings
    repository: DomainRepository
    bus: CommandBus
    pipeline: ResolvePipeline
    scheduler: RefreshScheduler
    auto_sync_service: DomainListSyncService
    auto_sync_scheduler: AutoListSyncScheduler
    sync_alert_notifier: SyncAlertNotifier
    domain_index: DomainIndex
    index_service: DomainIndexService
    passive_collector: PassiveDnsCollector
    dnstap_server: DnstapUnixServer | None

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
        await self.auto_sync_service.cleanup_expired_pending()
        await self._seed_exclude_keywords()
        await self._seed_domain_lists()
        await self.index_service.rebuild()

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
        if self.dnstap_server is not None:
            await self.dnstap_server.stop()
        await self.auto_sync_scheduler.stop()
        await self.scheduler.stop()
        await self.pipeline.flush_pending_export()
        closer = getattr(self.sync_alert_notifier, "close", None)
        if closer is not None:
            await closer()
        await self.repository.close()


class AddDomainAndResolveHandler:
    """Add domain, then resolve immediately so the BGP pool updates."""

    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
        index_service: DomainIndexService,
    ) -> None:
        self._add = AddDomainHandler(repository)
        self._repository = repository
        self._pipeline = pipeline
        self._index_service = index_service

    async def handle(self, command: AddDomainCommand) -> CommandResult[DomainView]:
        result = await self._add.handle(command)
        if result.ok and result.data is not None:
            await self._index_service.rebuild()
            if result.data.match_mode == "suffix":
                return result
            name = DomainName(result.data.name)
            await self._pipeline.resolve_one(name)
            updated = await self._repository.get(name)
            if updated is not None:
                return CommandResult.success(
                    domain_to_view(updated),
                    message=f"added and resolved {updated.name}",
                )
        return result


class RemoveDomainAndExportHandler:
    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
        index_service: DomainIndexService,
    ) -> None:
        self._remove = RemoveDomainHandler(repository)
        self._pipeline = pipeline
        self._index_service = index_service

    async def handle(self, command: RemoveDomainCommand) -> CommandResult[str]:
        result = await self._remove.handle(command)
        if result.ok:
            await self._index_service.rebuild()
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
        export_min_interval=settings.bird.export_min_interval,
    )
    domain_index = DomainIndex()
    index_service = DomainIndexService(repository, domain_index)
    passive_collector = PassiveDnsCollector(domain_index, pipeline)
    auto_sync_service = DomainListSyncService(
        repository=repository,
        pipeline=pipeline,
        settings=settings.auto_list,
        clock=clock,
        index_service=index_service,
    )
    if settings.telegram.token and settings.telegram.allowed_user_ids:
        sync_alert_notifier: SyncAlertNotifier = TelegramSyncAlertNotifier(settings.telegram)
    else:
        sync_alert_notifier = NullSyncAlertNotifier()
    auto_sync_service.set_notifier(sync_alert_notifier)
    bus = CommandBus()
    bus.register(AddDomainCommand, AddDomainAndResolveHandler(repository, pipeline, index_service))
    bus.register(
        RemoveDomainCommand, RemoveDomainAndExportHandler(repository, pipeline, index_service)
    )
    bus.register(ListDomainsCommand, ListDomainsHandler(repository))
    bus.register(ResolveNowCommand, ResolveNowHandler(pipeline))
    bus.register(ExportRoutesCommand, ExportRoutesHandler(pipeline))
    bus.register(SearchAutoDomainsCommand, SearchAutoDomainsHandler(repository))
    bus.register(SyncAutoListCommand, SyncAutoListHandler(auto_sync_service))
    bus.register(SyncDomainListCommand, SyncDomainListHandler(auto_sync_service))
    bus.register(ListDomainListsCommand, ListDomainListsHandler(repository))
    bus.register(AddDomainListCommand, AddDomainListHandler(repository))
    bus.register(UpdateDomainListCommand, UpdateDomainListHandler(repository))
    bus.register(
        RemoveDomainListCommand,
        RemoveDomainListAndExportHandler(repository, pipeline, index_service),
    )
    bus.register(ClearDomainListCommand, ClearDomainListHandler(repository, pipeline, index_service))
    bus.register(GetSettingsCommand, GetSettingsHandler(repository))
    bus.register(SetDefaultSyncIntervalCommand, SetDefaultSyncIntervalHandler(repository))
    bus.register(ListExcludeKeywordsCommand, ListExcludeKeywordsHandler(repository))
    bus.register(AddExcludeKeywordCommand, AddExcludeKeywordHandler(repository))
    bus.register(RemoveExcludeKeywordCommand, RemoveExcludeKeywordHandler(repository))
    bus.register(AddPrefixCommand, AddPrefixHandler(repository, pipeline))
    bus.register(RemovePrefixCommand, RemovePrefixHandler(repository, pipeline))
    bus.register(ListPrefixesCommand, ListPrefixesHandler(repository))

    scheduler = RefreshScheduler(pipeline)
    auto_sync_scheduler = AutoListSyncScheduler(
        auto_sync_service,
        repository,
        sync_on_startup=settings.auto_list.sync_on_startup,
    )
    dnstap_server: DnstapUnixServer | None = None
    if settings.dnstap.enabled:
        dnstap_server = DnstapUnixServer(
            settings.dnstap.listen_unix,
            passive_collector.on_response,
            socket_mode=settings.dnstap.socket_mode,
            listen_tcp=settings.dnstap.listen_tcp,
        )
    return AppContainer(
        settings=settings,
        repository=repository,
        bus=bus,
        pipeline=pipeline,
        scheduler=scheduler,
        auto_sync_service=auto_sync_service,
        auto_sync_scheduler=auto_sync_scheduler,
        sync_alert_notifier=sync_alert_notifier,
        domain_index=domain_index,
        index_service=index_service,
        passive_collector=passive_collector,
        dnstap_server=dnstap_server,
    )
