from __future__ import annotations

import math
from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import (
    CommandResult,
    DomainPageView,
    domain_to_view,
)
from dns2bgp_resolver.application.ports.repository import DomainRepository


@dataclass(frozen=True, slots=True)
class ListDomainsCommand:
    page: int = 1
    page_size: int | None = None


class ListDomainsHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: ListDomainsCommand) -> CommandResult[DomainPageView]:
        page = max(1, command.page)
        if command.page_size is None:
            items, total = await self._repository.list_manual()
            view = DomainPageView(
                items=[domain_to_view(d) for d in items],
                total=total,
                page=1,
                pages=1,
                page_size=total or 1,
            )
            return CommandResult.success(view, message=f"{total} domain(s)")

        page_size = max(1, min(command.page_size, 100))
        offset = (page - 1) * page_size
        items, total = await self._repository.list_manual(offset=offset, limit=page_size)
        pages = max(1, math.ceil(total / page_size)) if total else 1
        view = DomainPageView(
            items=[domain_to_view(d) for d in items],
            total=total,
            page=page,
            pages=pages,
            page_size=page_size,
        )
        return CommandResult.success(view, message=f"{total} domain(s)")
