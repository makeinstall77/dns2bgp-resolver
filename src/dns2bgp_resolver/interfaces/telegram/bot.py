from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
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
)
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_AUTO,
    BTN_CANCEL,
    BTN_DOMAINS,
    BTN_LISTS,
    BTN_PREFIXES,
    BTN_RESOLVE,
    BTN_SETTINGS,
    main_menu_keyboard,
)
from dns2bgp_resolver.interfaces.telegram.sync_alert import TelegramSyncAlertNotifier

_MENU_BUTTONS = {
    BTN_DOMAINS,
    BTN_AUTO,
    BTN_PREFIXES,
    BTN_LISTS,
    BTN_SETTINGS,
    BTN_RESOLVE,
    BTN_CANCEL,
}

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
    notifier = container.sync_alert_notifier
    if isinstance(notifier, TelegramSyncAlertNotifier):
        notifier.bind_bot(bot)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(ContainerMiddleware(container))

    dp.include_router(menu.router)
    dp.include_router(manual.router)
    dp.include_router(auto.router)
    dp.include_router(prefixes.router)
    dp.include_router(lists.router)
    dp.include_router(settings.router)
    dp.include_router(import_file.router)

    # Must be a nested router included last. Handlers on Dispatcher run before
    # child routers, so a catch-all here would steal FSM text input.
    fallback_router = Router()

    @fallback_router.message(F.text & ~F.text.in_(_MENU_BUTTONS))
    async def fallback(message: Message, container: AppContainer) -> None:
        if not allowed(container, message.from_user.id if message.from_user else None):
            return
        await message.answer("Выберите действие в меню.", reply_markup=main_menu_keyboard())

    dp.include_router(fallback_router)

    logger.info("telegram polling started")
    await dp.start_polling(bot)
