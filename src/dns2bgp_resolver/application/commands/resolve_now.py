from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult, ResolveSummary
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.domain import DomainName


@dataclass(frozen=True, slots=True)
class ResolveNowCommand:
    name: str | None = None
    """If None, resolve all enabled domains."""


class ResolveNowHandler:
    def __init__(self, pipeline: ResolvePipeline) -> None:
        self._pipeline = pipeline

    async def handle(self, command: ResolveNowCommand) -> CommandResult[list[ResolveSummary]]:
        if command.name is None:
            summaries = await self._pipeline.resolve_all()
        else:
            try:
                name = DomainName(command.name)
            except ValueError as exc:
                return CommandResult.failure(str(exc))
            summary = await self._pipeline.resolve_one(name)
            summaries = [summary]
        return CommandResult.success(summaries, message=f"resolved {len(summaries)} domain(s)")
