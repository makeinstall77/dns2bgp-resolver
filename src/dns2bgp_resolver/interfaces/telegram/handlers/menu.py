from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_HOME,
    auto_menu,
    domains_menu,
    main_menu,
    prefixes_menu,
)
from dns2bgp_resolver.interfaces.telegram.handlers import lists as lists_handlers
from dns2bgp_resolver.interfaces.telegram.handlers import settings as settings_handlers
from dns2bgp_resolver.interfaces.telegram.handlers import status as status_handlers
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()

_START_TEXT = (
    "dns2bgp-resolver\n"
    "• Manual — pre-resolve + маски (*.example.com)\n"
    "• Auto — индекс, IP через dnstap (без resolve)\n"
    "• Prefixes — статические CIDR в bird\n"
    "• Resolve manual — только manual-домены"
)


async def show_main_menu(message: Message, ui: BotUi, *, edit: bool = False) -> None:
    if edit:
        await ui.edit(message, _START_TEXT, reply_markup=main_menu())
    else:
        await ui.reply(message, _START_TEXT, reply_markup=main_menu())


@router.message(Command("start"))
async def cmd_start(message: Message, container: AppContainer, ui: BotUi) -> None:
    if not allowed(container, message.from_user.id if message.from_user else None):
        await ui.reply(message, "Access denied.")
        return
    await show_main_menu(message, ui)


@router.message(F.text == BTN_HOME)
async def btn_home(message: Message, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    await show_main_menu(message, ui)


@router.callback_query(F.data == "m:main")
async def cb_main(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    if callback.message:
        await show_main_menu(callback.message, ui, edit=True)
    await callback.answer()


@router.callback_query(F.data == "m:domains")
async def cb_domains(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    if callback.message:
        await ui.edit(
            callback.message,
            "Manual domains (pre-resolve).\n"
            "Можно: example.com или *.example.com\n"
            "Или .txt: домены и/или CIDR/IP.",
            reply_markup=domains_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == "m:auto")
async def cb_auto(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    if callback.message:
        await ui.edit(
            callback.message,
            "Auto domains — только индекс.\n"
            "IP появляются по DNS-запросам (dnstap), без pre-resolve.",
            reply_markup=auto_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == "m:prefixes")
async def cb_prefixes(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    if callback.message:
        await ui.edit(
            callback.message,
            "Static prefixes (IP/CIDR) — сразу в bird, без DNS.\n"
            "Суммаризация: /32, 2+ IP в /24 → /24, соседние сливаются.\n"
            "Можно прислать .txt со списком CIDR/IP.",
            reply_markup=prefixes_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == "m:lists")
async def cb_lists(
    callback: CallbackQuery, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    await state.clear()
    text, markup = await lists_handlers.render_lists_menu(container)
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "m:settings")
async def cb_settings(
    callback: CallbackQuery, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    await state.clear()
    text, markup = await settings_handlers.render_settings_summary(container)
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "m:status")
@router.callback_query(F.data == "m:status:r")
async def cb_status(
    callback: CallbackQuery, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    await state.clear()
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    if callback.message:
        await status_handlers.show_status(callback.message, container, ui, edit=True)
    toast = "Обновлено" if (callback.data or "").endswith(":r") else None
    if toast:
        await callback.answer(toast)
    else:
        await callback.answer()


@router.callback_query(F.data == "m:resolve")
async def cb_resolve(
    callback: CallbackQuery, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    await state.clear()
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    from dns2bgp_resolver.application.commands import ResolveNowCommand

    result = await container.bus.execute(ResolveNowCommand())
    if not result.ok:
        text = f"Error: {result.error}"
    else:
        lines = []
        for s in result.data or []:
            if s.error:
                lines.append(f"{s.domain}: ERROR {s.error}")
            else:
                flag = "changed" if s.changed else "ok"
                lines.append(f"{s.domain}: {flag}")
        text = "Resolve manual:\n" + ("\n".join(lines) if lines else "Nothing to resolve.")
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=main_menu())
    await callback.answer()
