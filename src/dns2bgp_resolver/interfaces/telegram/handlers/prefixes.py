from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    AddPrefixCommand,
    ListPrefixesCommand,
    RemovePrefixCommand,
    SetPrefixRoutePolicyCommand,
)
from dns2bgp_resolver.application.services.list_parse import format_prefixes_export
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    add_route_options_menu,
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
    text = (
        f"🛣 Prefixes — стр. {data.page}/{data.pages} ({data.total})\n"
        "🛡/🔀 — toggle announce/direct; 🗑 — удалить"
    )
    items = [
        (p.id or 0, p.cidr, p.name, p.route_policy)
        for p in data.items
        if p.id is not None
    ]
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
            "Префикс `direct:` — исключение на строку (не в bird-пуле).\n"
            "Можно несколько строк сразу.\n"
            "После ввода спрошу режим announce/direct.",
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


@router.callback_query(F.data.startswith("p:rt:"))
async def cb_toggle_route(
    callback: CallbackQuery, container: AppContainer, ui: BotUi
) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await callback.answer("Invalid callback.")
        return
    try:
        page = int(parts[2])
        prefix_id = int(parts[3])
    except ValueError:
        await callback.answer("Invalid callback.")
        return
    result = await container.bus.execute(SetPrefixRoutePolicyCommand(prefix_id=prefix_id))
    if not result.ok:
        await callback.answer(result.error or "Error", show_alert=True)
        return
    text, markup = await _render_prefix_page(container, page)
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer(result.message or "OK")


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


def _parse_prefix_lines(text: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for ln in text.splitlines():
        raw = ln.strip()
        if not raw or raw.startswith("#"):
            continue
        force_direct = False
        cidr = raw
        name = None
        if cidr.lower().startswith("direct:"):
            force_direct = True
            cidr = cidr.split(":", 1)[1].strip()
        if " " in cidr:
            cidr, name = cidr.split(None, 1)
        items.append({"cidr": cidr, "name": name, "force_direct": force_direct})
    return items


async def _commit_pending_prefixes(
    *,
    container: AppContainer,
    state: FSMContext,
    message: Message,
    ui: BotUi,
    route_policy: str,
) -> None:
    data = await state.get_data()
    pending: list[dict[str, object]] = list(data.get("pending_prefixes") or [])
    await state.update_data(pending_prefixes=None)
    await state.set_state(AddPrefix.waiting_cidr)

    if not pending:
        await ui.reply(message, "Нечего добавлять.", reply_markup=_CANCEL)
        return

    added = 0
    last_ok = ""
    errors: list[str] = []
    for item in pending:
        cidr = str(item["cidr"])
        name = item.get("name")
        name_s = str(name) if name else None
        policy = "direct" if item.get("force_direct") else route_policy
        result = await container.bus.execute(
            AddPrefixCommand(cidr=cidr, name=name_s, route_policy=policy)
        )
        if result.ok:
            added += 1
            last_ok = result.message or f"added {cidr}"
        else:
            errors.append(f"{cidr}: {result.error}")

    if len(pending) == 1 and added == 1:
        await ui.reply(
            message,
            f"{last_ok}\nЕщё CIDR (можно несколько строк) или ◀ Отмена:",
            reply_markup=_CANCEL,
        )
        return

    parts = [f"Добавлено: {added}/{len(pending)}"]
    if errors:
        parts.append("Ошибки:\n" + "\n".join(f"• {e}" for e in errors[:10]))
        if len(errors) > 10:
            parts.append(f"… и ещё {len(errors) - 10}")
    parts.append("Ещё CIDR (можно несколько строк) или ◀ Отмена:")
    await ui.reply(message, "\n".join(parts), reply_markup=_CANCEL)


@router.message(AddPrefix.waiting_cidr, F.text)
async def add_prefix_text(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    items = _parse_prefix_lines(message.text or "")
    if not items:
        await ui.reply(message, "Введите IPv4 или CIDR.", reply_markup=_CANCEL)
        return

    await state.update_data(pending_prefixes=items)
    await state.set_state(AddPrefix.waiting_route)
    count = len(items)
    hint = f"для {count} запис(ей)" if count > 1 else "для префикса"
    await ui.reply(
        message,
        f"Режим анонсирования {hint} (announce = в bird-пуле, direct = исключение):",
        reply_markup=add_route_options_menu("padd:rt"),
    )


@router.callback_query(AddPrefix.waiting_route, F.data.startswith("padd:rt:"))
async def cb_add_prefix_route(
    callback: CallbackQuery, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    choice = (callback.data or "").rsplit(":", 1)[-1]
    if choice == "cancel":
        await state.update_data(pending_prefixes=None)
        await state.set_state(AddPrefix.waiting_cidr)
        if callback.message:
            await ui.edit(
                callback.message,
                "Отменено. Ещё CIDR или ◀ Отмена:",
                reply_markup=_CANCEL,
            )
        await callback.answer("Отменено")
        return
    if choice not in ("announce", "direct"):
        await callback.answer("Invalid")
        return
    await callback.answer()
    if callback.message:
        await ui.edit(callback.message, "⏳ Добавляю…")
        await _commit_pending_prefixes(
            container=container,
            state=state,
            message=callback.message,  # type: ignore[arg-type]
            ui=ui,
            route_policy=choice,
        )


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
