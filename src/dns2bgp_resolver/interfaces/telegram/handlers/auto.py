from __future__ import annotations

import hashlib

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    AddExcludeKeywordCommand,
    ListExcludeKeywordsCommand,
    RemoveExcludeKeywordCommand,
    SearchAutoDomainsCommand,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_CANCEL,
    auto_menu,
    cancel_keyboard,
    filters_menu,
    main_menu_keyboard,
    search_keyboard,
)
from dns2bgp_resolver.interfaces.telegram.states import AddFilter, SearchAuto

router = Router()

_SEARCH_PAGE_SIZE = 15
_search_cache: dict[str, str] = {}


def _cache_query(query: str) -> str:
    key = hashlib.sha256(query.encode()).hexdigest()[:8]
    _search_cache[key] = query
    if len(_search_cache) > 200:
        for old_key in list(_search_cache)[:50]:
            _search_cache.pop(old_key, None)
    return key


def _resolve_query(key: str) -> str:
    return _search_cache.get(key, "")


async def _render_search(container: AppContainer, query: str, page: int) -> tuple[str, object]:
    result = await container.bus.execute(
        SearchAutoDomainsCommand(query=query, page=page, page_size=_SEARCH_PAGE_SIZE)
    )
    if not result.ok or result.data is None:
        return f"Error: {result.error}", auto_menu()

    data = result.data
    if not data.items:
        return f"No auto domains match {query!r}.", auto_menu()

    lines = [f"Auto search {query!r} — page {data.page}/{data.pages} ({data.total} total)"]
    for d in data.items:
        ips = ", ".join(d.addresses) if d.addresses else "-"
        lines.append(f"{d.name}: {ips}")

    query_key = _cache_query(query)
    markup = search_keyboard(query_key, data.page, data.pages)
    return "\n".join(lines), markup


@router.callback_query(F.data == "a:search")
async def cb_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchAuto.waiting_query)
    if callback.message:
        await callback.message.answer("Enter search query:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(SearchAuto.waiting_query, F.text)
async def search_query(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Cancelled.", reply_markup=main_menu_keyboard())
        return
    query = (message.text or "").strip()
    await state.clear()
    text, markup = await _render_search(container, query, page=1)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("s:"))
async def cb_search_page(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Invalid callback.")
        return
    try:
        page = int(parts[1])
    except ValueError:
        await callback.answer("Invalid page.")
        return
    query = _resolve_query(parts[2])
    text, markup = await _render_search(container, query, page=page)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "a:filters")
async def cb_filters(callback: CallbackQuery, container: AppContainer) -> None:
    result = await container.bus.execute(ListExcludeKeywordsCommand())
    keywords = result.data or []
    text = "Exclude keywords:" if keywords else "No exclude keywords."
    if callback.message:
        await callback.message.edit_text(text, reply_markup=filters_menu(keywords))
    await callback.answer()


@router.callback_query(F.data == "f:add")
async def cb_filter_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddFilter.waiting_keyword)
    if callback.message:
        await callback.message.answer("Enter keyword:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AddFilter.waiting_keyword, F.text)
async def filter_add_text(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Cancelled.", reply_markup=main_menu_keyboard())
        return
    result = await container.bus.execute(AddExcludeKeywordCommand(keyword=(message.text or "").strip()))
    await state.clear()
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=main_menu_keyboard())
        return
    await message.answer(result.message or "Added.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data.startswith("f:rm:"))
async def cb_filter_remove(callback: CallbackQuery, container: AppContainer) -> None:
    keyword = (callback.data or "").split(":", 2)[-1]
    result = await container.bus.execute(RemoveExcludeKeywordCommand(keyword=keyword))
    keywords_result = await container.bus.execute(ListExcludeKeywordsCommand())
    keywords = keywords_result.data or []
    text = "Exclude keywords:" if keywords else "No exclude keywords."
    if callback.message:
        await callback.message.edit_text(
            result.message or text,
            reply_markup=filters_menu(keywords),
        )
    await callback.answer()
