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


@router.callback_query(F.data.startswith("l:syncforce:"))
async def cb_sync_force(callback: CallbackQuery, container: AppContainer) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Invalid.", show_alert=True)
        return
    list_id = int(parts[2])
    token = parts[3]
    pending = await container.repository.get_sync_pending(token)
    if pending is None or pending.list_id != list_id:
        await callback.answer("Expired or unknown.", show_alert=True)
        if callback.message:
            await callback.message.edit_text("Sync confirmation expired or already handled.")
        return
    result = await container.auto_sync_service.confirm_pending(token)
    if result is None:
        await callback.answer("Failed.", show_alert=True)
        if callback.message:
            await callback.message.edit_text("Sync confirmation expired or already handled.")
        return
    text = f"Confirmed sync {result.list_name}: +{result.added} -{result.removed}"
    if callback.message:
        await callback.message.edit_text(text)
    await callback.answer("Applied.")


@router.callback_query(F.data.startswith("l:synccancel:"))
async def cb_sync_cancel(callback: CallbackQuery, container: AppContainer) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Invalid.", show_alert=True)
        return
    list_id = int(parts[2])
    token = parts[3]
    pending = await container.repository.get_sync_pending(token)
    if pending is None or pending.list_id != list_id:
        await callback.answer("Already cancelled.", show_alert=True)
        if callback.message:
            await callback.message.edit_text("Sync confirmation expired or already handled.")
        return
    await container.auto_sync_service.cancel_pending(token)
    if callback.message:
        await callback.message.edit_text(
            f"Sync cancelled for {pending.list_name}. List left unchanged."
        )
    await callback.answer("Cancelled.")


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


async def _back_to_lists(message: Message, container: AppContainer, state: FSMContext) -> None:
    await state.clear()
    text, markup = await render_lists_menu(container)
    await message.answer(text, reply_markup=main_menu_keyboard())
    await message.answer("Списки:", reply_markup=markup)


@router.callback_query(F.data == "l:addurl")
async def cb_add_url(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddListUrl.waiting_name)
    if callback.message:
        await callback.message.answer("List name:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AddListUrl.waiting_name, F.text)
async def add_url_name(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_lists(message, container, state)
        return
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(AddListUrl.waiting_url)
    await message.answer("List URL:", reply_markup=cancel_keyboard())


@router.message(AddListUrl.waiting_url, F.text)
async def add_url_url(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_lists(message, container, state)
        return
    data = await state.get_data()
    result = await container.bus.execute(
        AddDomainListCommand(name=data["name"], type="url", url=(message.text or "").strip())
    )
    if not result.ok:
        await state.set_state(AddListUrl.waiting_name)
        await message.answer(f"Error: {result.error}\nList name:", reply_markup=cancel_keyboard())
        return
    await state.set_state(AddListUrl.waiting_name)
    await message.answer(
        f"{result.message or 'Added.'}\nЕщё list name или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )


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
async def upload_cancel_or_name(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_lists(message, container, state)


@router.message(AddListFile.waiting_name, F.document)
async def upload_file(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.document is None or message.bot is None:
        return
    file = await message.bot.download(message.document)
    content = file.read().decode("utf-8", errors="replace")
    name = message.document.file_name or "upload"
    list_name = name.rsplit(".", 1)[0]
    result = await container.bus.execute(
        AddDomainListCommand(name=list_name, type="file", file_content=content)
    )
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    await message.answer(
        f"{result.message or 'Uploaded.'}\nЕщё файл или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )


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
        await _back_to_lists(message, container, state)
        return
    try:
        seconds = int((message.text or "").strip())
    except ValueError:
        await message.answer("Enter a number.", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    result = await container.bus.execute(
        UpdateDomainListCommand(id=int(data["list_id"]), sync_interval=seconds)
    )
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    await message.answer(
        f"{result.message or 'Updated.'}\nЕщё interval или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )
