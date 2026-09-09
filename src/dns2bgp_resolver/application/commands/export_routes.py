from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult, ExportSummary
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline


@dataclass(frozen=True, slots=True)
class ExportRoutesCommand:
    allow_empty: bool = False


class ExportRoutesHandler:
    def __init__(self, pipeline: ResolvePipeline) -> None:
        self._pipeline = pipeline

    async def handle(self, command: ExportRoutesCommand) -> CommandResult[ExportSummary]:
        summary = await self._pipeline.export_routes(allow_empty=command.allow_empty)
        if summary.skipped:
            return CommandResult.failure(
                summary.skip_reason
                or "export skipped: refused empty wipe of non-empty bird file",
                data=summary,
            )
        return CommandResult.success(
            summary, message=f"exported {summary.prefix_count} prefix(es)"
        )
