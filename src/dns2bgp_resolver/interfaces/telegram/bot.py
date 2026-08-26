from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, TelegramObject

from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.handlers import (
    auto,
    import_file,
    lists,
    manual,
    menu,
    prefixes,
    settings,
    status,
)
from dns2bgp_resolver.interfaces.telegram.keyboards import BTN_HOME, main_menu
from dns2bgp_resolver.interfaces.telegram.sync_alert import TelegramSyncAlertNotifier
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

logger = logging.getLogger(__name__)


class ContainerMiddleware(BaseMiddleware):
    def __init__(self, container: AppContainer, ui: BotUi) -> None:
        self._container = container
        self._ui = ui

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["container"] = self._container
        data["ui"] = self._ui
        return await handler(event, data)


async def run_telegram_bot(container: AppContainer) -> None:
    token = container.settings.telegram.token
    if not token:
        raise RuntimeError("telegram token is empty")

    bot = Bot(token=token)
    ui = BotUi()
    notifier = container.sync_alert_notifier
    if isinstance(notifier, TelegramSyncAlertNotifier):
        notifier.bind_bot(bot)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(ContainerMiddleware(container, ui))

    dp.include_router(menu.router)
    dp.include_router(status.router)
    dp.include_router(manual.router)
    dp.include_router(auto.router)
    dp.include_router(prefixes.router)
    dp.include_router(lists.router)
    dp.include_router(settings.router)
    dp.include_router(import_file.router)

    # Must be a nested router included last. Handlers on Dispatcher run before
    # child routers, so a catch-all here would steal FSM text input.
    fallback_router = Router()

    @fallback_router.message(StateFilter(None), F.text & ~F.text.in_({BTN_HOME}))
    async def fallback(message: Message, container: AppContainer, ui: BotUi) -> None:
        if not allowed(container, message.from_user.id if message.from_user else None):
            return
        await ui.reply(
            message,
            "Выберите действие в меню.",
            reply_markup=main_menu(),
        )

    dp.include_router(fallback_router)

    logger.info("telegram polling started")
    await dp.start_polling(bot)
