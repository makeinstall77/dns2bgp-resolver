from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    GetSettingsCommand,
    ListExcludeKeywordsCommand,
    SetDefaultSyncIntervalCommand,
    SetSuppressIpv6DefaultCommand,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    cancel_inline,
    filters_menu,
    settings_menu,
)
from dns2bgp_resolver.interfaces.telegram.states import SetGlobalInterval
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()

_CANCEL = cancel_inline("m:settings")


async def render_settings_summary(container: AppContainer) -> tuple[str, object]:
    settings = await container.bus.execute(GetSettingsCommand())
    interval = settings.data.default_sync_interval if settings.data else 86400
    manual = (
        settings.data.suppress_ipv6_manual_default if settings.data else True
    )
    auto = settings.data.suppress_ipv6_auto_default if settings.data else True
    mode = container.settings.ipv6.mode

    def aaaa_state(suppress: bool) -> str:
        return "выкл (блокируем)" if suppress else "вкл (отдаём)"

    text = (
        "Settings\n"
        f"Default sync interval: {interval}s\n"
        f"ipv6.mode (config): {mode}\n"
        "\n"
        "AAAA: вкл = отдаём записи, выкл = блокируем\n"
        f"• Manual default: {aaaa_state(manual)}\n"
        f"• Auto default: {aaaa_state(auto)}\n"
        "\n"
        "У manual-хоста: дефолт → вкл → выкл (цикл).\n"
        "Manual всегда приоритетнее Auto (даже при «дефолт»)."
    )
    return text, settings_menu(suppress_manual=manual, suppress_auto=auto)


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


@router.callback_query(F.data.in_({"st:v6:manual", "st:v6:auto"}))
async def cb_toggle_v6_default(
    callback: CallbackQuery, container: AppContainer, ui: BotUi
) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    scope = "manual" if (callback.data or "").endswith(":manual") else "auto"
    settings = await container.bus.execute(GetSettingsCommand())
    if settings.data is None:
        await callback.answer("Error", show_alert=True)
        return
    current = (
        settings.data.suppress_ipv6_manual_default
        if scope == "manual"
        else settings.data.suppress_ipv6_auto_default
    )
    result = await container.bus.execute(
        SetSuppressIpv6DefaultCommand(scope=scope, enabled=not current)
    )
    if not result.ok:
        await callback.answer(result.error or "Error", show_alert=True)
        return
    text, markup = await render_settings_summary(container)
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer(result.message or "OK")


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
