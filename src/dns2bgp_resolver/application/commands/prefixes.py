from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult
from dns2bgp_resolver.application.errors import DomainAlreadyExistsError
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.domain import StaticPrefix, is_announcable_prefix


@dataclass(frozen=True, slots=True)
class PrefixView:
    cidr: str
    name: str | None
    enabled: bool
    id: int | None = None


@dataclass(frozen=True, slots=True)
class AddPrefixCommand:
    cidr: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class RemovePrefixCommand:
    cidr: str


@dataclass(frozen=True, slots=True)
class ListPrefixesCommand:
    pass


class AddPrefixHandler:
    def __init__(self, repository: DomainRepository, pipeline: ResolvePipeline) -> None:
        self._repository = repository
        self._pipeline = pipeline

    async def handle(self, command: AddPrefixCommand) -> CommandResult[PrefixView]:
        try:
            prefix = StaticPrefix(cidr=command.cidr, name=command.name)
        except ValueError as exc:
            return CommandResult.failure(str(exc))
        if not is_announcable_prefix(prefix.cidr):
            return CommandResult.failure(f"prefix not announcable: {prefix.cidr}")
        try:
            saved = await self._repository.add_static_prefix(prefix)
        except DomainAlreadyExistsError:
            return CommandResult.failure(f"prefix already exists: {prefix.cidr}")
        await self._pipeline.export_after_mutation()
        return CommandResult.success(
            PrefixView(cidr=saved.cidr, name=saved.name, enabled=saved.enabled, id=saved.id),
            message=f"added {saved.cidr}",
        )


class RemovePrefixHandler:
    def __init__(self, repository: DomainRepository, pipeline: ResolvePipeline) -> None:
        self._repository = repository
        self._pipeline = pipeline

    async def handle(self, command: RemovePrefixCommand) -> CommandResult[str]:
        try:
            StaticPrefix(cidr=command.cidr)
        except ValueError as exc:
            return CommandResult.failure(str(exc))
        removed = await self._repository.remove_static_prefix(command.cidr)
        if not removed:
            return CommandResult.failure(f"prefix not found: {command.cidr}")
        await self._pipeline.export_after_mutation()
        return CommandResult.success(command.cidr, message=f"removed {command.cidr}")


class ListPrefixesHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: ListPrefixesCommand) -> CommandResult[list[PrefixView]]:
        items = [
            PrefixView(cidr=p.cidr, name=p.name, enabled=p.enabled, id=p.id)
            for p in await self._repository.list_static_prefixes()
        ]
        return CommandResult.success(items)
