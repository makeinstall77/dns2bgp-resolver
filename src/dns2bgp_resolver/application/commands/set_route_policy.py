from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dns2bgp_resolver.application.commands.dto import CommandResult, DomainView, domain_to_view
from dns2bgp_resolver.application.commands.prefixes import PrefixView
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.domain import RoutePolicy

if TYPE_CHECKING:
    from dns2bgp_resolver.application.services.domain_index_service import DomainIndexService
    from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline


def next_route_policy(policy: RoutePolicy) -> RoutePolicy:
    return "direct" if policy != "direct" else "vpn"


@dataclass(frozen=True, slots=True)
class SetRoutePolicyCommand:
    domain_id: int
    policy: RoutePolicy | None = None
    """If None, toggle vpn ↔ direct."""


@dataclass(frozen=True, slots=True)
class SetPrefixRoutePolicyCommand:
    prefix_id: int
    policy: RoutePolicy | None = None


class SetRoutePolicyHandler:
    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
        index_service: DomainIndexService | None = None,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._index_service = index_service

    async def handle(self, command: SetRoutePolicyCommand) -> CommandResult[DomainView]:
        domain = await self._repository.get_by_id(command.domain_id)
        if domain is None:
            return CommandResult.failure("domain not found")
        if domain.source != "manual":
            return CommandResult.failure("only manual domains support route_policy toggle")
        policy = command.policy or next_route_policy(domain.route_policy)
        if policy not in ("vpn", "direct"):
            return CommandResult.failure("route_policy must be vpn|direct")
        updated = await self._repository.set_route_policy(command.domain_id, policy)
        if updated is None:
            return CommandResult.failure("domain not found")
        if self._index_service is not None:
            await self._index_service.rebuild()
        await self._pipeline.export_after_mutation(allow_empty=True)
        label = "direct (исключение)" if updated.route_policy == "direct" else "vpn"
        return CommandResult.success(
            domain_to_view(updated),
            message=f"route: {label}",
        )


class SetPrefixRoutePolicyHandler:
    def __init__(
        self,
        repository: DomainRepository,
        pipeline: ResolvePipeline,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline

    async def handle(
        self, command: SetPrefixRoutePolicyCommand
    ) -> CommandResult[PrefixView]:
        prefixes = await self._repository.list_static_prefixes()
        current = next((p for p in prefixes if p.id == command.prefix_id), None)
        if current is None:
            return CommandResult.failure("prefix not found")
        policy = command.policy or next_route_policy(current.route_policy)
        if policy not in ("vpn", "direct"):
            return CommandResult.failure("route_policy must be vpn|direct")
        updated = await self._repository.set_static_prefix_route_policy(
            command.prefix_id, policy
        )
        if updated is None:
            return CommandResult.failure("prefix not found")
        await self._pipeline.export_after_mutation(allow_empty=True)
        label = "direct (исключение)" if updated.route_policy == "direct" else "vpn"
        return CommandResult.success(
            PrefixView(
                cidr=updated.cidr,
                name=updated.name,
                enabled=updated.enabled,
                id=updated.id,
                route_policy=updated.route_policy,
            ),
            message=f"route: {label}",
        )
