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
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()


async def _back_to_prefixes(message: Message, state: FSMContext, ui: BotUi) -> None:
    await state.clear()
    await ui.reply(
        message,
        "Static prefixes (IP/CIDR) — сразу в bird, без DNS.",
        reply_markup=prefixes_menu(),
        reply_keyboard=main_menu_keyboard(),
    )


@router.callback_query(F.data == "p:list")
async def cb_list(callback: CallbackQuery, container: AppContainer, ui: BotUi) -> None:
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
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "p:add")
async def cb_add(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.set_state(AddPrefix.waiting_cidr)
    if callback.message:
        await ui.reply(
            callback.message,
            "Введите IPv4 или CIDR (например 149.154.160.0/20).\n"
            "Можно несколько строк сразу.",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "p:rm")
async def cb_remove(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.set_state(RemovePrefix.waiting_cidr)
    if callback.message:
        await ui.reply(
            callback.message,
            "Введите CIDR для удаления:",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("p:rmok:"))
async def cb_remove_ok(callback: CallbackQuery, container: AppContainer, ui: BotUi) -> None:
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
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer(result.message or "Removed")


@router.message(AddPrefix.waiting_cidr, F.text)
async def add_prefix_text(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_prefixes(message, state, ui)
        return
    lines = [
        ln.strip()
        for ln in (message.text or "").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        await ui.reply(message, "Введите IPv4 или CIDR.", reply_markup=cancel_keyboard())
        return

    added = 0
    last_ok = ""
    errors: list[str] = []
    for raw in lines:
        name = None
        cidr = raw
        if " " in raw:
            cidr, name = raw.split(None, 1)
        result = await container.bus.execute(AddPrefixCommand(cidr=cidr, name=name))
        if result.ok:
            added += 1
            last_ok = result.message or f"added {cidr}"
        else:
            errors.append(f"{cidr}: {result.error}")

    if len(lines) == 1 and added == 1:
        await ui.reply(
            message,
            f"{last_ok}\nЕщё CIDR (можно несколько строк) или ◀ Отмена:",
            reply_markup=cancel_keyboard(),
        )
        return

    parts = [f"Добавлено: {added}/{len(lines)}"]
    if errors:
        parts.append("Ошибки:\n" + "\n".join(f"• {e}" for e in errors[:10]))
        if len(errors) > 10:
            parts.append(f"… и ещё {len(errors) - 10}")
    parts.append("Ещё CIDR (можно несколько строк) или ◀ Отмена:")
    await ui.reply(message, "\n".join(parts), reply_markup=cancel_keyboard())


@router.message(RemovePrefix.waiting_cidr, F.text)
async def remove_prefix_text(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_prefixes(message, state, ui)
        return
    result = await container.bus.execute(
        RemovePrefixCommand(cidr=(message.text or "").strip())
    )
    if not result.ok:
        await ui.reply(message, f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    await ui.reply(
        message,
        f"{result.message or 'Removed.'}\nЕщё CIDR или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )
