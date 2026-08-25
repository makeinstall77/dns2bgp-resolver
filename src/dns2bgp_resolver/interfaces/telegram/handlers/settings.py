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
    BTN_CANCEL,
    cancel_keyboard,
    filters_menu,
    main_menu_keyboard,
    settings_menu,
)
from dns2bgp_resolver.interfaces.telegram.states import SetGlobalInterval
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()


async def render_settings_summary(container: AppContainer) -> str:
    settings = await container.bus.execute(GetSettingsCommand())
    interval = settings.data.default_sync_interval if settings.data else 86400
    return f"Settings\nDefault sync interval: {interval}s"


async def _back_to_settings(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    await state.clear()
    text = await render_settings_summary(container)
    await ui.reply(
        message, text, reply_markup=settings_menu(), reply_keyboard=main_menu_keyboard()
    )


@router.callback_query(F.data == "st:interval")
async def cb_global_interval(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.set_state(SetGlobalInterval.waiting_seconds)
    if callback.message:
        await ui.reply(
            callback.message,
            "Default sync interval (seconds, min 60):",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(SetGlobalInterval.waiting_seconds, F.text)
async def set_global_interval(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_settings(message, container, state, ui)
        return
    try:
        seconds = int((message.text or "").strip())
    except ValueError:
        await ui.reply(message, "Enter a number.", reply_markup=cancel_keyboard())
        return
    result = await container.bus.execute(SetDefaultSyncIntervalCommand(seconds=seconds))
    if not result.ok:
        await ui.reply(message, f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    await ui.reply(
        message,
        f"{result.message or 'Updated.'}\nЕщё interval или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )


@router.callback_query(F.data == "st:filters")
async def cb_settings_filters(
    callback: CallbackQuery, container: AppContainer, ui: BotUi
) -> None:
    result = await container.bus.execute(ListExcludeKeywordsCommand())
    keywords = result.data or []
    text = "Exclude keywords:" if keywords else "No exclude keywords."
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=filters_menu(keywords))
    await callback.answer()
