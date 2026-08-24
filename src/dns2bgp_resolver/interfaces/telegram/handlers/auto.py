from __future__ import annotations

import hashlib

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    AddExcludeKeywordCommand,
    ListExcludeKeywordsCommand,
    RemoveExcludeKeywordCommand,
    ResolveNowCommand,
    SearchAutoDomainsCommand,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_CANCEL,
    auto_host_menu,
    auto_menu,
    cancel_keyboard,
    filters_menu,
    host_list_keyboard,
    main_menu_keyboard,
)
from dns2bgp_resolver.interfaces.telegram.states import AddFilter, SearchAuto

router = Router()

_PAGE_SIZE = 10
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


async def _render_auto_page(
    container: AppContainer,
    query: str,
    page: int,
    *,
    title: str,
    back_callback: str,
    with_query_key: bool,
) -> tuple[str, object]:
    result = await container.bus.execute(
        SearchAutoDomainsCommand(query=query, page=page, page_size=_PAGE_SIZE)
    )
    if not result.ok or result.data is None:
        return f"Error: {result.error}", auto_menu()

    data = result.data
    if not data.items:
        empty = f"🤖 {title}: пусто." if not query else f"Нет совпадений для {query!r}."
        return empty, auto_menu()

    header = f"🤖 {title} — стр. {data.page}/{data.pages} ({data.total})"
    if query:
        header = f"🔍 {query!r} — стр. {data.page}/{data.pages} ({data.total})"

    items = [
        (d.id or 0, d.name, len(d.addresses))
        for d in data.items
        if d.id is not None
    ]
    query_key = _cache_query(query) if with_query_key else None
    markup = host_list_keyboard(
        prefix="a",
        items=items,
        page=data.page,
        pages=data.pages,
        back_callback=back_callback,
        query_key=query_key,
    )
    return header, markup


@router.callback_query(F.data.startswith("a:list:"))
async def cb_list(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    try:
        page = int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        page = 1
    text, markup = await _render_auto_page(
        container,
        "",
        page,
        title="Auto",
        back_callback="m:auto",
        with_query_key=False,
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("a:h:"))
async def cb_host(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    # a:h:id:page or a:h:id:page:query_key
    if len(parts) < 4:
        await callback.answer("Invalid callback.")
        return
    try:
        domain_id = int(parts[2])
        page = int(parts[3])
    except ValueError:
        await callback.answer("Invalid callback.")
        return
    query_key = parts[4] if len(parts) > 4 else None

    domain = await container.repository.get_by_id(domain_id)
    if domain is None or domain.source != "auto":
        await callback.answer("Host not found.", show_alert=True)
        return
    ips = ", ".join(str(a.ip) for a in domain.addresses) or "—"
    text = f"🌐 {domain.name}\nIP ({len(domain.addresses)}): {ips}"
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=auto_host_menu(domain_id, page, query_key=query_key),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("a:rs:"))
async def cb_refresh(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    # a:rs:id:page or a:rs:id:page:query_key
    if len(parts) < 4:
        await callback.answer("Invalid callback.")
        return
    try:
        domain_id = int(parts[2])
        page = int(parts[3])
    except ValueError:
        await callback.answer("Invalid callback.")
        return
    query_key = parts[4] if len(parts) > 4 else None

    domain = await container.repository.get_by_id(domain_id)
    if domain is None:
        await callback.answer("Host not found.", show_alert=True)
        return

    result = await container.bus.execute(ResolveNowCommand(name=str(domain.name)))
    if not result.ok or not result.data:
        await callback.answer(result.error or "Error", show_alert=True)
        return
    summary = result.data[0]
    if summary.error:
        toast = f"ERROR: {summary.error}"
    elif summary.changed:
        toast = f"Обновлено: {len(summary.addresses)} IP"
    else:
        toast = f"Без изменений: {len(summary.addresses)} IP"

    domain = await container.repository.get_by_id(domain_id)
    if domain and callback.message:
        ips = ", ".join(str(a.ip) for a in domain.addresses) or "—"
        text = f"🌐 {domain.name}\nIP ({len(domain.addresses)}): {ips}"
        await callback.message.edit_text(
            text,
            reply_markup=auto_host_menu(domain_id, page, query_key=query_key),
        )
    await callback.answer(toast, show_alert=True)


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
    text, markup = await _render_auto_page(
        container,
        query,
        page=1,
        title="Search",
        back_callback="m:auto",
        with_query_key=True,
    )
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
    text, markup = await _render_auto_page(
        container,
        query,
        page=page,
        title="Search",
        back_callback="m:auto",
        with_query_key=True,
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "a:filters")
async def cb_filters(callback: CallbackQuery, container: AppContainer) -> None:
    result = await container.bus.execute(ListExcludeKeywordsCommand())
    keywords = result.data or []
    text = "🏷 Exclude keywords:" if keywords else "🏷 No exclude keywords."
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
    text = "🏷 Exclude keywords:" if keywords else "🏷 No exclude keywords."
    if callback.message:
        await callback.message.edit_text(
            result.message or text,
            reply_markup=filters_menu(keywords),
        )
    await callback.answer()
