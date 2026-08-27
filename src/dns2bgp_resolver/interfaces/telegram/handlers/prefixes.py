from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    AddPrefixCommand,
    ListPrefixesCommand,
    RemovePrefixCommand,
)
from dns2bgp_resolver.application.services.list_parse import format_prefixes_export
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    cancel_inline,
    prefixes_list_keyboard,
    prefixes_menu,
)
from dns2bgp_resolver.interfaces.telegram.states import AddPrefix, RemovePrefix
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()

_CANCEL = cancel_inline("m:prefixes")
_PAGE_SIZE = 10


async def _render_prefix_page(container: AppContainer, page: int) -> tuple[str, object]:
    result = await container.bus.execute(
        ListPrefixesCommand(page=page, page_size=_PAGE_SIZE)
    )
    if not result.ok or result.data is None:
        return f"Error: {result.error}", prefixes_menu()
    data = result.data
    if not data.items:
        return (
            "🛣 Static prefixes: пусто.\nПри экспорте: /32 → /24 → соседние сливаются.",
            prefixes_menu(),
        )
    text = f"🛣 Prefixes — стр. {data.page}/{data.pages} ({data.total})\nнажмите чтобы удалить"
    items = [(p.cidr, p.name) for p in data.items]
    return text, prefixes_list_keyboard(items, page=data.page, pages=data.pages)


@router.callback_query(F.data == "p:list")
@router.callback_query(F.data.startswith("p:list:"))
async def cb_list(callback: CallbackQuery, container: AppContainer, ui: BotUi) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    page = 1
    raw = callback.data or ""
    if raw.startswith("p:list:"):
        try:
            page = int(raw.split(":")[2])
        except (IndexError, ValueError):
            page = 1
    text, markup = await _render_prefix_page(container, page)
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "p:export")
async def cb_export(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    result = await container.bus.execute(ListPrefixesCommand())
    if not result.ok or result.data is None:
        await callback.answer(result.error or "Error", show_alert=True)
        return
    items = [(p.cidr, p.name) for p in result.data.items]
    text = format_prefixes_export(items)
    if not text:
        await callback.answer("Список пуст.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    doc = BufferedInputFile(text.encode("utf-8"), filename="prefixes.txt")
    await callback.message.answer_document(doc, caption=f"Prefixes: {result.data.total}")
    await callback.answer()


@router.callback_query(F.data == "p:add")
async def cb_add(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.set_state(AddPrefix.waiting_cidr)
    if callback.message:
        await ui.edit(
            callback.message,
            "Введите IPv4 или CIDR (например 149.154.160.0/20).\n"
            "Можно несколько строк сразу.",
            reply_markup=_CANCEL,
        )
    await callback.answer()


@router.callback_query(F.data == "p:rm")
async def cb_remove(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.set_state(RemovePrefix.waiting_cidr)
    if callback.message:
        await ui.edit(
            callback.message,
            "Введите CIDR для удаления:",
            reply_markup=_CANCEL,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("p:rmok:"))
async def cb_remove_ok(callback: CallbackQuery, container: AppContainer, ui: BotUi) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts = (callback.data or "").split(":", 3)
    page = 1
    if len(parts) >= 4:
        try:
            page = int(parts[2])
        except ValueError:
            page = 1
        cidr = parts[3]
    else:
        cidr = parts[-1]
    result = await container.bus.execute(RemovePrefixCommand(cidr=cidr))
    if not result.ok:
        await callback.answer(result.error or "Error", show_alert=True)
        return
    text, markup = await _render_prefix_page(container, page)
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer(result.message or "Removed")


@router.message(AddPrefix.waiting_cidr, F.text)
async def add_prefix_text(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    lines = [
        ln.strip()
        for ln in (message.text or "").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        await ui.reply(message, "Введите IPv4 или CIDR.", reply_markup=_CANCEL)
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
            reply_markup=_CANCEL,
        )
        return

    parts = [f"Добавлено: {added}/{len(lines)}"]
    if errors:
        parts.append("Ошибки:\n" + "\n".join(f"• {e}" for e in errors[:10]))
        if len(errors) > 10:
            parts.append(f"… и ещё {len(errors) - 10}")
    parts.append("Ещё CIDR (можно несколько строк) или ◀ Отмена:")
    await ui.reply(message, "\n".join(parts), reply_markup=_CANCEL)


@router.message(RemovePrefix.waiting_cidr, F.text)
async def remove_prefix_text(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    result = await container.bus.execute(
        RemovePrefixCommand(cidr=(message.text or "").strip())
    )
    if not result.ok:
        await ui.reply(message, f"Error: {result.error}", reply_markup=_CANCEL)
        return
    await ui.reply(
        message,
        f"{result.message or 'Removed.'}\nЕщё CIDR или ◀ Отмена:",
        reply_markup=_CANCEL,
    )
