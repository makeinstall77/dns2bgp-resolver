from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    AddPrefixCommand,
    ListPrefixesCommand,
    RemovePrefixCommand,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_CANCEL,
    cancel_keyboard,
    main_menu_keyboard,
    prefixes_list_keyboard,
    prefixes_menu,
)
from dns2bgp_resolver.interfaces.telegram.states import AddPrefix, RemovePrefix

router = Router()


async def _back_to_prefixes(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Static prefixes (IP/CIDR) — сразу в bird, без DNS.",
        reply_markup=main_menu_keyboard(),
    )
    await message.answer("Выберите действие:", reply_markup=prefixes_menu())


@router.callback_query(F.data == "p:list")
async def cb_list(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    result = await container.bus.execute(ListPrefixesCommand())
    if not result.ok:
        await callback.answer(result.error or "Error", show_alert=True)
        return
    items = [(p.cidr, p.name) for p in (result.data or [])]
    if not items:
        text = "🛣 Static prefixes: пусто.\nCIDR идут в bird как есть (без /24)."
        markup = prefixes_menu()
    else:
        text = f"🛣 Static prefixes ({len(items)}):\nнажмите чтобы удалить"
        markup = prefixes_list_keyboard(items)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "p:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddPrefix.waiting_cidr)
    if callback.message:
        await callback.message.answer(
            "Введите IPv4 или CIDR (например 149.154.160.0/20):",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "p:rm")
async def cb_remove(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RemovePrefix.waiting_cidr)
    if callback.message:
        await callback.message.answer(
            "Введите CIDR для удаления:",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("p:rmok:"))
async def cb_remove_ok(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    cidr = (callback.data or "").split(":", 2)[-1]
    result = await container.bus.execute(RemovePrefixCommand(cidr=cidr))
    if not result.ok:
        await callback.answer(result.error or "Error", show_alert=True)
        return
    listed = await container.bus.execute(ListPrefixesCommand())
    items = [(p.cidr, p.name) for p in (listed.data or [])]
    if not items:
        text = "🛣 Static prefixes: пусто."
        markup = prefixes_menu()
    else:
        text = f"🛣 Static prefixes ({len(items)}):\nнажмите чтобы удалить"
        markup = prefixes_list_keyboard(items)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer(result.message or "Removed")


@router.message(AddPrefix.waiting_cidr, F.text)
async def add_prefix_text(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_prefixes(message, state)
        return
    raw = (message.text or "").strip()
    name = None
    cidr = raw
    if " " in raw:
        cidr, name = raw.split(None, 1)
    result = await container.bus.execute(AddPrefixCommand(cidr=cidr, name=name))
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    await message.answer(
        f"{result.message or f'Added {cidr}'}\nЕщё CIDR или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )


@router.message(RemovePrefix.waiting_cidr, F.text)
async def remove_prefix_text(
    message: Message, container: AppContainer, state: FSMContext
) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_prefixes(message, state)
        return
    result = await container.bus.execute(
        RemovePrefixCommand(cidr=(message.text or "").strip())
    )
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    await message.answer(
        f"{result.message or 'Removed.'}\nЕщё CIDR или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )
