from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from dns2bgp_resolver.application.ports.clock import SystemClock
from dns2bgp_resolver.application.services.service_status import (
    collect_service_status,
    format_status_text,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import BTN_STATUS, main_menu_keyboard
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()
logger = logging.getLogger(__name__)

_LIVE_SECONDS = 180
_LIVE_INTERVAL = 10
_clock = SystemClock()


async def _render(container: AppContainer, *, live_left_sec: int | None) -> str:
    status = await collect_service_status(
        repository=container.repository,
        domain_index=container.domain_index,
        passive_collector=container.passive_collector,
        bird=container.settings.bird,
        dnstap=container.settings.dnstap,
        clock_now=_clock.now(),
    )
    return format_status_text(status, live_left_sec=live_left_sec)


async def _live_update(
    *,
    bot,
    chat_id: int,
    message_id: int,
    generation: int,
    container: AppContainer,
    ui: BotUi,
) -> None:
    deadline = time.monotonic() + _LIVE_SECONDS
    while True:
        left = max(0, int(deadline - time.monotonic()))
        sleep_for = _LIVE_INTERVAL if left > _LIVE_INTERVAL else max(1, left)
        await asyncio.sleep(sleep_for)
        if ui.generation(chat_id) != generation or ui.screen_id(chat_id) != message_id:
            return
        left = max(0, int(deadline - time.monotonic()))
        text = await _render(container, live_left_sec=left)
        ok = await ui.edit_by_id(bot, chat_id, message_id, text)
        if not ok or left <= 0:
            return


async def show_status(message: Message, container: AppContainer, ui: BotUi) -> None:
    if message.bot is None:
        return
    text = await _render(container, live_left_sec=_LIVE_SECONDS)
    msg = await ui.reply(message, text, reply_keyboard=main_menu_keyboard())
    gen = ui.generation(message.chat.id)
    asyncio.create_task(
        _live_update(
            bot=message.bot,
            chat_id=message.chat.id,
            message_id=msg.message_id,
            generation=gen,
            container=container,
            ui=ui,
        ),
        name=f"tg-status-{message.chat.id}",
    )


@router.message(Command("status"))
@router.message(F.text == BTN_STATUS)
async def cmd_status(message: Message, container: AppContainer, ui: BotUi) -> None:
    if not allowed(container, message.from_user.id if message.from_user else None):
        await ui.reply(message, "Access denied.")
        return
    await show_status(message, container, ui)
