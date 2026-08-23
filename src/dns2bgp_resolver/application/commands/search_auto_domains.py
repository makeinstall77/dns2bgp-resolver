from __future__ import annotations

import math
from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import (
    AutoDomainSearchView,
    CommandResult,
    domain_to_view,
)
from dns2bgp_resolver.application.ports.repository import DomainRepository


@dataclass(frozen=True, slots=True)
class SearchAutoDomainsCommand:
    query: str = ""
    page: int = 1
    page_size: int = 50


class SearchAutoDomainsHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: SearchAutoDomainsCommand) -> CommandResult[AutoDomainSearchView]:
        page = max(1, command.page)
        page_size = max(1, min(command.page_size, 100))
        offset = (page - 1) * page_size
        items, total = await self._repository.search_auto(
            command.query.strip().lower(),
            offset=offset,
            limit=page_size,
        )
        pages = max(1, math.ceil(total / page_size)) if total else 1
        view = AutoDomainSearchView(
            items=[domain_to_view(d) for d in items],
            total=total,
            page=page,
            pages=pages,
            page_size=page_size,
        )
        return CommandResult.success(view, message=f"{total} match(es)")
