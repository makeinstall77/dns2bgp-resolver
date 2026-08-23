from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_AUTO,
    BTN_DOMAINS,
    BTN_LISTS,
    BTN_RESOLVE,
    BTN_SETTINGS,
    auto_menu,
    domains_menu,
    lists_menu,
    main_menu_keyboard,
    settings_menu,
)
from dns2bgp_resolver.interfaces.telegram.handlers import lists as lists_handlers
from dns2bgp_resolver.interfaces.telegram.handlers import settings as settings_handlers

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, container: AppContainer) -> None:
    if not allowed(container, message.from_user.id if message.from_user else None):
        await message.answer("Access denied.")
        return
    await message.answer(
        "dns2bgp-resolver\nВыберите раздел в меню.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(lambda m: m.text == BTN_DOMAINS)
async def btn_domains(message: Message) -> None:
    await message.answer("Manual domains:", reply_markup=domains_menu())


@router.message(lambda m: m.text == BTN_AUTO)
async def btn_auto(message: Message) -> None:
    await message.answer("Auto domains:", reply_markup=auto_menu())


@router.message(lambda m: m.text == BTN_LISTS)
async def btn_lists(message: Message, container: AppContainer) -> None:
    text, markup = await lists_handlers.render_lists_menu(container)
    await message.answer(text, reply_markup=markup)


@router.message(lambda m: m.text == BTN_SETTINGS)
async def btn_settings(message: Message, container: AppContainer) -> None:
    text = await settings_handlers.render_settings_summary(container)
    await message.answer(text, reply_markup=settings_menu())


@router.message(lambda m: m.text == BTN_RESOLVE)
async def btn_resolve(message: Message, container: AppContainer) -> None:
    from dns2bgp_resolver.application.commands import ResolveNowCommand

    result = await container.bus.execute(ResolveNowCommand())
    if not result.ok:
        await message.answer(f"Error: {result.error}")
        return
    lines = []
    for s in result.data or []:
        if s.error:
            lines.append(f"{s.domain}: ERROR {s.error}")
        else:
            flag = "changed" if s.changed else "ok"
            lines.append(f"{s.domain}: {flag}")
    await message.answer("\n".join(lines) or "Nothing to resolve.")


@router.callback_query(lambda c: c.data == "m:main")
async def cb_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.answer("Главное меню", reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(lambda c: c.data == "m:domains")
async def cb_domains(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Manual domains:", reply_markup=domains_menu())
    await callback.answer()


@router.callback_query(lambda c: c.data == "m:auto")
async def cb_auto(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Auto domains:", reply_markup=auto_menu())
    await callback.answer()


@router.callback_query(lambda c: c.data == "m:lists")
async def cb_lists(callback: CallbackQuery, container: AppContainer, state: FSMContext) -> None:
    await state.clear()
    text, markup = await lists_handlers.render_lists_menu(container)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(lambda c: c.data == "m:settings")
async def cb_settings(callback: CallbackQuery, container: AppContainer, state: FSMContext) -> None:
    await state.clear()
    text = await settings_handlers.render_settings_summary(container)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=settings_menu())
    await callback.answer()
