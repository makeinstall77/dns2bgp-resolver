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

router = Router()


async def render_settings_summary(container: AppContainer) -> str:
    settings = await container.bus.execute(GetSettingsCommand())
    interval = settings.data.default_sync_interval if settings.data else 86400
    return f"Settings\nDefault sync interval: {interval}s"


async def _back_to_settings(message: Message, container: AppContainer, state: FSMContext) -> None:
    await state.clear()
    text = await render_settings_summary(container)
    await message.answer(text, reply_markup=main_menu_keyboard())
    await message.answer("Настройки:", reply_markup=settings_menu())


@router.callback_query(F.data == "st:interval")
async def cb_global_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SetGlobalInterval.waiting_seconds)
    if callback.message:
        await callback.message.answer(
            "Default sync interval (seconds, min 60):", reply_markup=cancel_keyboard()
        )
    await callback.answer()


@router.message(SetGlobalInterval.waiting_seconds, F.text)
async def set_global_interval(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_settings(message, container, state)
        return
    try:
        seconds = int((message.text or "").strip())
    except ValueError:
        await message.answer("Enter a number.", reply_markup=cancel_keyboard())
        return
    result = await container.bus.execute(SetDefaultSyncIntervalCommand(seconds=seconds))
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    await message.answer(
        f"{result.message or 'Updated.'}\nЕщё interval или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )


@router.callback_query(F.data == "st:filters")
async def cb_settings_filters(callback: CallbackQuery, container: AppContainer) -> None:
    result = await container.bus.execute(ListExcludeKeywordsCommand())
    keywords = result.data or []
    text = "Exclude keywords:" if keywords else "No exclude keywords."
    if callback.message:
        await callback.message.edit_text(text, reply_markup=filters_menu(keywords))
    await callback.answer()
