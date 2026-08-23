from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    ListDomainsCommand,
    RemoveDomainCommand,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import BTN_CANCEL, cancel_keyboard, domains_menu, main_menu_keyboard
from dns2bgp_resolver.interfaces.telegram.states import AddDomain, RemoveDomain

router = Router()


@router.callback_query(F.data == "d:list")
async def cb_list(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    result = await container.bus.execute(ListDomainsCommand())
    domains = result.data or []
    if not domains:
        text = "No manual domains."
    else:
        lines = ["Manual domains:"]
        for d in domains:
            ips = ", ".join(d.addresses) if d.addresses else "-"
            lines.append(f"{d.name}: {ips}")
        text = "\n".join(lines)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=domains_menu())
    await callback.answer()


@router.callback_query(F.data == "d:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddDomain.waiting_name)
    if callback.message:
        await callback.message.answer("Enter domain name:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "d:rm")
async def cb_remove(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RemoveDomain.waiting_name)
    if callback.message:
        await callback.message.answer("Enter domain to remove:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AddDomain.waiting_name, F.text)
async def add_domain_text(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Cancelled.", reply_markup=main_menu_keyboard())
        return
    result = await container.bus.execute(AddDomainCommand(name=(message.text or "").strip()))
    await state.clear()
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=main_menu_keyboard())
        return
    ips = ", ".join(result.data.addresses) if result.data and result.data.addresses else "-"
    await message.answer(f"Added {message.text}: {ips}", reply_markup=main_menu_keyboard())


@router.message(RemoveDomain.waiting_name, F.text)
async def remove_domain_text(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Cancelled.", reply_markup=main_menu_keyboard())
        return
    result = await container.bus.execute(RemoveDomainCommand(name=(message.text or "").strip()))
    await state.clear()
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=main_menu_keyboard())
        return
    await message.answer(result.message or "Removed.", reply_markup=main_menu_keyboard())
