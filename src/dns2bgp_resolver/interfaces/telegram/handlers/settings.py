from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    GetSettingsCommand,
    ListExcludeKeywordsCommand,
    SetDefaultSyncIntervalCommand,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    cancel_inline,
    filters_menu,
)
from dns2bgp_resolver.interfaces.telegram.states import SetGlobalInterval
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()

_CANCEL = cancel_inline("m:settings")


async def render_settings_summary(container: AppContainer) -> str:
    settings = await container.bus.execute(GetSettingsCommand())
    interval = settings.data.default_sync_interval if settings.data else 86400
    return f"Settings\nDefault sync interval: {interval}s"


@router.callback_query(F.data == "st:interval")
async def cb_global_interval(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.set_state(SetGlobalInterval.waiting_seconds)
    if callback.message:
        await ui.edit(
            callback.message,
            "Default sync interval (seconds, min 60):",
            reply_markup=_CANCEL,
        )
    await callback.answer()


@router.message(SetGlobalInterval.waiting_seconds, F.text)
async def set_global_interval(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    try:
        seconds = int((message.text or "").strip())
    except ValueError:
        await ui.reply(message, "Enter a number.", reply_markup=_CANCEL)
        return
    result = await container.bus.execute(SetDefaultSyncIntervalCommand(seconds=seconds))
    if not result.ok:
        await ui.reply(message, f"Error: {result.error}", reply_markup=_CANCEL)
        return
    await ui.reply(
        message,
        f"{result.message or 'Updated.'}\nЕщё interval или ◀ Отмена:",
        reply_markup=_CANCEL,
    )


@router.callback_query(F.data == "st:filters")
async def cb_settings_filters(
    callback: CallbackQuery, container: AppContainer, ui: BotUi
) -> None:
    result = await container.bus.execute(ListExcludeKeywordsCommand())
    keywords = result.data or []
    text = "Exclude keywords:" if keywords else "No exclude keywords."
    if callback.message:
        await ui.edit(
            callback.message,
            text,
            reply_markup=filters_menu(keywords, back_callback="m:settings"),
        )
    await callback.answer()
