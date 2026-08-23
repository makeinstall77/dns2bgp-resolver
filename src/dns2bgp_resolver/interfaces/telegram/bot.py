from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, TelegramObject

from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.handlers import auto, lists, manual, menu, settings
from dns2bgp_resolver.interfaces.telegram.keyboards import main_menu_keyboard

logger = logging.getLogger(__name__)


class ContainerMiddleware(BaseMiddleware):
    def __init__(self, container: AppContainer) -> None:
        self._container = container

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["container"] = self._container
        return await handler(event, data)


async def run_telegram_bot(container: AppContainer) -> None:
    token = container.settings.telegram.token
    if not token:
        raise RuntimeError("telegram token is empty")

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(ContainerMiddleware(container))

    dp.include_router(menu.router)
    dp.include_router(manual.router)
    dp.include_router(auto.router)
    dp.include_router(lists.router)
    dp.include_router(settings.router)

    from aiogram import F

    @dp.message(F.text)
    async def fallback(message: Message, container: AppContainer) -> None:
        if not allowed(container, message.from_user.id if message.from_user else None):
            return
        await message.answer("Выберите действие в меню.", reply_markup=main_menu_keyboard())

    logger.info("telegram polling started")
    await dp.start_polling(bot)
