from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_AUTO,
    BTN_DOMAINS,
    BTN_LISTS,
    BTN_PREFIXES,
    BTN_RESOLVE,
    BTN_SETTINGS,
    auto_menu,
    domains_menu,
    main_menu_keyboard,
    prefixes_menu,
    settings_menu,
)
from dns2bgp_resolver.interfaces.telegram.handlers import lists as lists_handlers
from dns2bgp_resolver.interfaces.telegram.handlers import settings as settings_handlers
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()

_START_TEXT = (
    "dns2bgp-resolver\n"
    "• Manual — pre-resolve + маски (*.example.com)\n"
    "• Auto — индекс, IP через dnstap (без resolve)\n"
    "• Prefixes — статические CIDR в bird\n"
    "• Resolve manual — только manual-домены"
)


@router.message(Command("start"))
async def cmd_start(message: Message, container: AppContainer, ui: BotUi) -> None:
    if not allowed(container, message.from_user.id if message.from_user else None):
        await ui.reply(message, "Access denied.")
        return
    await ui.reply(message, _START_TEXT, reply_keyboard=main_menu_keyboard())


@router.message(F.text == BTN_DOMAINS)
async def btn_domains(message: Message, ui: BotUi) -> None:
    await ui.reply(
        message,
        "Manual domains (pre-resolve).\n"
        "Можно: example.com или *.example.com\n"
        "Или .txt: домены и/или CIDR/IP.",
        reply_markup=domains_menu(),
        reply_keyboard=main_menu_keyboard(),
    )


@router.message(F.text == BTN_AUTO)
async def btn_auto(message: Message, ui: BotUi) -> None:
    await ui.reply(
        message,
        "Auto domains — только индекс.\nIP появляются по DNS-запросам (dnstap), без pre-resolve.",
        reply_markup=auto_menu(),
        reply_keyboard=main_menu_keyboard(),
    )


@router.message(F.text == BTN_PREFIXES)
async def btn_prefixes(message: Message, ui: BotUi) -> None:
    await ui.reply(
        message,
        "Static prefixes (IP/CIDR) — сразу в bird, без DNS.\n"
        "Можно прислать .txt со списком CIDR/IP.",
        reply_markup=prefixes_menu(),
        reply_keyboard=main_menu_keyboard(),
    )


@router.message(F.text == BTN_LISTS)
async def btn_lists(message: Message, container: AppContainer, ui: BotUi) -> None:
    text, markup = await lists_handlers.render_lists_menu(container)
    await ui.reply(
        message, text, reply_markup=markup, reply_keyboard=main_menu_keyboard()
    )


@router.message(F.text == BTN_SETTINGS)
async def btn_settings(message: Message, container: AppContainer, ui: BotUi) -> None:
    text = await settings_handlers.render_settings_summary(container)
    await ui.reply(
        message, text, reply_markup=settings_menu(), reply_keyboard=main_menu_keyboard()
    )


@router.message(F.text == BTN_RESOLVE)
async def btn_resolve(message: Message, container: AppContainer, ui: BotUi) -> None:
    from dns2bgp_resolver.application.commands import ResolveNowCommand

    result = await container.bus.execute(ResolveNowCommand())
    if not result.ok:
        await ui.reply(message, f"Error: {result.error}", reply_keyboard=main_menu_keyboard())
        return
    lines = []
    for s in result.data or []:
        if s.error:
            lines.append(f"{s.domain}: ERROR {s.error}")
        else:
            flag = "changed" if s.changed else "ok"
            lines.append(f"{s.domain}: {flag}")
    await ui.reply(
        message,
        "Resolve manual:\n" + ("\n".join(lines) if lines else "Nothing to resolve."),
        reply_keyboard=main_menu_keyboard(),
    )


@router.callback_query(F.data == "m:main")
async def cb_main(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    if callback.message:
        await ui.reply(
            callback.message,
            "Главное меню",
            reply_keyboard=main_menu_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "m:domains")
async def cb_domains(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    if callback.message:
        await ui.edit(
            callback.message,
            "Manual domains (pre-resolve).\nМожно: example.com или *.example.com",
            reply_markup=domains_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == "m:auto")
async def cb_auto(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    if callback.message:
        await ui.edit(
            callback.message,
            "Auto domains — индекс, IP через dnstap.",
            reply_markup=auto_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == "m:prefixes")
async def cb_prefixes(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    if callback.message:
        await ui.edit(
            callback.message,
            "Static prefixes (IP/CIDR) — сразу в bird.",
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
    text = await settings_handlers.render_settings_summary(container)
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=settings_menu())
    await callback.answer()
