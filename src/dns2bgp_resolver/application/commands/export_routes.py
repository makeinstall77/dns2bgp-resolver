from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult, ExportSummary
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline


@dataclass(frozen=True, slots=True)
class ExportRoutesCommand:
    pass


class ExportRoutesHandler:
    def __init__(self, pipeline: ResolvePipeline) -> None:
        self._pipeline = pipeline

    async def handle(self, command: ExportRoutesCommand) -> CommandResult[ExportSummary]:
        del command
        summary = await self._pipeline.export_routes()
        return CommandResult.success(summary, message=f"exported {summary.prefix_count} prefix(es)")
