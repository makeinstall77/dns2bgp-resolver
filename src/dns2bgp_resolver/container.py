from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    AddDomainHandler,
    CommandBus,
    ExportRoutesCommand,
    ExportRoutesHandler,
    ListDomainsCommand,
    ListDomainsHandler,
    RemoveDomainCommand,
    RemoveDomainHandler,
    ResolveNowCommand,
    ResolveNowHandler,
)
from dns2bgp_resolver.application.commands.dto import CommandResult, DomainView
from dns2bgp_resolver.application.ports.clock import SystemClock
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.config import Settings
from dns2bgp_resolver.domain import DomainName
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import StaticFileBirdExporter
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository
from dns2bgp_resolver.infrastructure.dns.dnspython_resolver import DnspythonResolver
from dns2bgp_resolver.infrastructure.scheduling.refresh_scheduler import RefreshScheduler


@dataclass
class AppContainer:
    settings: Settings
    repository: DomainRepository
    bus: CommandBus
    pipeline: ResolvePipeline
    scheduler: RefreshScheduler

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

    async def shutdown(self) -> None:
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
                from dns2bgp_resolver.application.commands.dto import domain_to_view

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
    bus = CommandBus()
    bus.register(AddDomainCommand, AddDomainAndResolveHandler(repository, pipeline))
    bus.register(RemoveDomainCommand, RemoveDomainAndExportHandler(repository, pipeline))
    bus.register(ListDomainsCommand, ListDomainsHandler(repository))
    bus.register(ResolveNowCommand, ResolveNowHandler(pipeline))
    bus.register(ExportRoutesCommand, ExportRoutesHandler(pipeline))

    scheduler = RefreshScheduler(pipeline)
    return AppContainer(
        settings=settings,
        repository=repository,
        bus=bus,
        pipeline=pipeline,
        scheduler=scheduler,
    )
