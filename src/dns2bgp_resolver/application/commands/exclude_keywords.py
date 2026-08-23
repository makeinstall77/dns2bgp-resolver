from __future__ import annotations

from dataclasses import dataclass

from dns2bgp_resolver.application.commands.dto import CommandResult
from dns2bgp_resolver.application.ports.repository import DomainRepository


@dataclass(frozen=True, slots=True)
class ListExcludeKeywordsCommand:
    pass


@dataclass(frozen=True, slots=True)
class AddExcludeKeywordCommand:
    keyword: str


@dataclass(frozen=True, slots=True)
class RemoveExcludeKeywordCommand:
    keyword: str


class ListExcludeKeywordsHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: ListExcludeKeywordsCommand) -> CommandResult[list[str]]:
        del command
        keywords = await self._repository.list_exclude_keywords()
        return CommandResult.success(keywords, message=f"{len(keywords)} keyword(s)")


class AddExcludeKeywordHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: AddExcludeKeywordCommand) -> CommandResult[str]:
        keyword = command.keyword.strip().lower()
        if not keyword:
            return CommandResult.failure("keyword is empty")
        added = await self._repository.add_exclude_keyword(keyword)
        if not added:
            return CommandResult.failure(f"keyword already exists: {keyword}")
        return CommandResult.success(keyword, message=f"added filter {keyword}")


class RemoveExcludeKeywordHandler:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def handle(self, command: RemoveExcludeKeywordCommand) -> CommandResult[str]:
        keyword = command.keyword.strip().lower()
        if not keyword:
            return CommandResult.failure("keyword is empty")
        removed = await self._repository.remove_exclude_keyword(keyword)
        if not removed:
            return CommandResult.failure(f"keyword not found: {keyword}")
        return CommandResult.success(keyword, message=f"removed filter {keyword}")
