from __future__ import annotations

from typing import Any, Protocol, TypeVar


C = TypeVar("C")
R = TypeVar("R")


class CommandHandler(Protocol[C, R]):
    async def handle(self, command: C) -> R: ...


class CommandBus:
    """Dispatches commands to registered handlers — shared by CLI, web, telegram."""

    def __init__(self) -> None:
        self._handlers: dict[type, CommandHandler[Any, Any]] = {}

    def register(self, command_type: type[C], handler: CommandHandler[C, R]) -> None:
        self._handlers[command_type] = handler

    async def execute(self, command: C) -> R:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise KeyError(f"no handler registered for {type(command).__name__}")
        return await handler.handle(command)
