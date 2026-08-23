from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from dns2bgp_resolver.application.commands import (
    AddDomainListCommand,
    ClearDomainListCommand,
    GetSettingsCommand,
    ListDomainListsCommand,
    RemoveDomainListCommand,
    SyncDomainListCommand,
    UpdateDomainListCommand,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_CANCEL,
    cancel_keyboard,
    confirm_menu,
    list_detail_menu,
    lists_menu,
    main_menu_keyboard,
)
from dns2bgp_resolver.interfaces.telegram.states import AddListFile, AddListUrl, SetListInterval

router = Router()


async def render_lists_menu(container: AppContainer) -> tuple[str, object]:
    result = await container.bus.execute(ListDomainListsCommand())
    settings = await container.bus.execute(GetSettingsCommand())
    default = settings.data.default_sync_interval if settings.data else 86400
    items = result.data or []
    if not items:
        return f"Domain lists (default interval {default}s):", lists_menu([])

    lines = [f"Domain lists (default {default}s):"]
    buttons: list[InlineKeyboardButton] = []
    for item in items:
        state = "on" if item.enabled else "off"
        interval = item.sync_interval or default
        lines.append(f"[{item.id}] {item.name} ({item.type}, {state}, {interval}s)")
        buttons.append(
            InlineKeyboardButton(text=item.name, callback_data=f"l:view:{item.id}")
        )
    return "\n".join(lines), lists_menu(buttons)


@router.callback_query(F.data == "l:syncall")
async def cb_sync_all(callback: CallbackQuery, container: AppContainer) -> None:
    result = await container.bus.execute(SyncDomainListCommand())
    if callback.message:
        await callback.message.answer(result.message or "Synced.")
    await callback.answer()


@router.callback_query(F.data.startswith("l:view:"))
async def cb_view_list(callback: CallbackQuery, container: AppContainer) -> None:
    list_id = int((callback.data or "").split(":")[-1])
    result = await container.bus.execute(ListDomainListsCommand())
    item = next((x for x in (result.data or []) if x.id == list_id), None)
    if item is None:
        await callback.answer("Not found.", show_alert=True)
        return
    src = item.url if item.type == "url" else "file"
    text = (
        f"{item.name}\n"
        f"type: {item.type}\n"
        f"enabled: {item.enabled}\n"
        f"interval: {item.sync_interval or 'default'}\n"
        f"source: {src}\n"
        f"last sync: {item.last_sync_at or 'never'}"
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=list_detail_menu(list_id, item.enabled))
    await callback.answer()


@router.callback_query(F.data.startswith("l:en:"))
async def cb_toggle(callback: CallbackQuery, container: AppContainer) -> None:
    list_id = int((callback.data or "").split(":")[-1])
    lists_result = await container.bus.execute(ListDomainListsCommand())
    item = next((x for x in (lists_result.data or []) if x.id == list_id), None)
    if item is None:
        await callback.answer("Not found.", show_alert=True)
        return
    await container.bus.execute(UpdateDomainListCommand(id=list_id, enabled=not item.enabled))
    await cb_view_list(callback, container)


@router.callback_query(F.data.startswith("l:sync:"))
async def cb_sync_one(callback: CallbackQuery, container: AppContainer) -> None:
    list_id = int((callback.data or "").split(":")[-1])
    result = await container.bus.execute(SyncDomainListCommand(id=list_id))
    await callback.answer(result.message or "Synced.", show_alert=True)


@router.callback_query(F.data.startswith("l:clr:"))
async def cb_clear_prompt(callback: CallbackQuery) -> None:
    list_id = int((callback.data or "").split(":")[-1])
    if callback.message:
        await callback.message.edit_text(
            "Clear all domains from this list?",
            reply_markup=confirm_menu("clr", list_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("l:del:"))
async def cb_delete_prompt(callback: CallbackQuery) -> None:
    list_id = int((callback.data or "").split(":")[-1])
    if callback.message:
        await callback.message.edit_text(
            "Delete this list and all its domains?",
            reply_markup=confirm_menu("del", list_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("l:cf:"))
async def cb_confirm(callback: CallbackQuery, container: AppContainer) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[4] != "yes":
        await callback.answer("Cancelled.")
        return
    action, list_id = parts[2], int(parts[3])
    if action == "clr":
        result = await container.bus.execute(ClearDomainListCommand(id=list_id))
    else:
        result = await container.bus.execute(RemoveDomainListCommand(id=list_id))
    text, markup = await render_lists_menu(container)
    if callback.message:
        await callback.message.edit_text(f"{result.message}\n\n{text}", reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "l:addurl")
async def cb_add_url(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddListUrl.waiting_name)
    if callback.message:
        await callback.message.answer("List name:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AddListUrl.waiting_name, F.text)
async def add_url_name(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Cancelled.", reply_markup=main_menu_keyboard())
        return
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(AddListUrl.waiting_url)
    await message.answer("List URL:", reply_markup=cancel_keyboard())


@router.message(AddListUrl.waiting_url, F.text)
async def add_url_url(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Cancelled.", reply_markup=main_menu_keyboard())
        return
    data = await state.get_data()
    await state.clear()
    result = await container.bus.execute(
        AddDomainListCommand(name=data["name"], type="url", url=(message.text or "").strip())
    )
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=main_menu_keyboard())
        return
    await message.answer(result.message or "Added.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "l:upload")
async def cb_upload(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddListFile.waiting_name)
    if callback.message:
        await callback.message.answer(
            "Send a .txt/.lst file (optional: enter name after upload):",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(AddListFile.waiting_name, F.text)
async def upload_cancel_or_name(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Cancelled.", reply_markup=main_menu_keyboard())


@router.message(AddListFile.waiting_name, F.document)
async def upload_file(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.document is None or message.bot is None:
        return
    file = await message.bot.download(message.document)
    content = file.read().decode("utf-8", errors="replace")
    name = message.document.file_name or "upload"
    list_name = name.rsplit(".", 1)[0]
    await state.clear()
    result = await container.bus.execute(
        AddDomainListCommand(name=list_name, type="file", file_content=content)
    )
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=main_menu_keyboard())
        return
    await message.answer(result.message or "Uploaded.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data.startswith("l:int:"))
async def cb_interval(callback: CallbackQuery, state: FSMContext) -> None:
    list_id = int((callback.data or "").split(":")[-1])
    await state.set_state(SetListInterval.waiting_seconds)
    await state.update_data(list_id=list_id)
    if callback.message:
        await callback.message.answer("Sync interval in seconds (min 60):", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(SetListInterval.waiting_seconds, F.text)
async def set_list_interval(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Cancelled.", reply_markup=main_menu_keyboard())
        return
    try:
        seconds = int((message.text or "").strip())
    except ValueError:
        await message.answer("Enter a number.")
        return
    data = await state.get_data()
    await state.clear()
    result = await container.bus.execute(
        UpdateDomainListCommand(id=int(data["list_id"]), sync_interval=seconds)
    )
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=main_menu_keyboard())
        return
    await message.answer(result.message or "Updated.", reply_markup=main_menu_keyboard())
