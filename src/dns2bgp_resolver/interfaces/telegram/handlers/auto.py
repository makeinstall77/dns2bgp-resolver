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
    auto_host_menu,
    auto_menu,
    cancel_inline,
    filters_menu,
    host_list_keyboard,
)
from dns2bgp_resolver.interfaces.telegram.states import AddFilter, SearchAuto
from dns2bgp_resolver.interfaces.telegram.ui import BotUi

router = Router()

_PAGE_SIZE = 10
_search_cache: dict[str, str] = {}
_CANCEL_AUTO = cancel_inline("m:auto")
_CANCEL_FILTERS = cancel_inline("a:filters")


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
    header += "\n(индекс; IP — через dnstap)"

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
        show_addr_count=False,
    )
    return header, markup


@router.callback_query(F.data.startswith("a:list:"))
async def cb_list(callback: CallbackQuery, container: AppContainer, ui: BotUi) -> None:
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
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("a:h:"))
async def cb_host(callback: CallbackQuery, container: AppContainer, ui: BotUi) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
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
    hits = await container.repository.list_passive_hits(limit=500)
    matched_ips = [h.ip for h in hits if h.matched_name == str(domain.name)]
    if matched_ips:
        ips_line = ", ".join(matched_ips[:20])
        if len(matched_ips) > 20:
            ips_line += f" … (+{len(matched_ips) - 20})"
        text = (
            f"🌐 {domain.name}\n"
            f"режим: индекс + dnstap\n"
            f"passive IP ({len(matched_ips)}): {ips_line}"
        )
    else:
        text = (
            f"🌐 {domain.name}\n"
            f"режим: индекс + dnstap\n"
            f"passive IP: пока нет (ждём DNS-запросы)"
        )
    if callback.message:
        await ui.edit(
            callback.message,
            text,
            reply_markup=auto_host_menu(domain_id, page, query_key=query_key),
        )
    await callback.answer()


@router.callback_query(F.data == "a:search")
async def cb_search(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.set_state(SearchAuto.waiting_query)
    if callback.message:
        await ui.edit(
            callback.message,
            "Enter search query:",
            reply_markup=_CANCEL_AUTO,
        )
    await callback.answer()


@router.message(SearchAuto.waiting_query, F.text)
async def search_query(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    query = (message.text or "").strip()
    text, markup = await _render_auto_page(
        container,
        query,
        page=1,
        title="Search",
        back_callback="m:auto",
        with_query_key=True,
    )
    await state.clear()
    await ui.reply(message, text, reply_markup=markup)


@router.callback_query(F.data.startswith("s:"))
async def cb_search_page(callback: CallbackQuery, container: AppContainer, ui: BotUi) -> None:
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
        await ui.edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "a:filters")
async def cb_filters(
    callback: CallbackQuery, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    await state.clear()
    result = await container.bus.execute(ListExcludeKeywordsCommand())
    keywords = result.data or []
    text = "🏷 Exclude keywords:" if keywords else "🏷 No exclude keywords."
    if callback.message:
        await ui.edit(callback.message, text, reply_markup=filters_menu(keywords))
    await callback.answer()


@router.callback_query(F.data == "f:add")
async def cb_filter_add(callback: CallbackQuery, state: FSMContext, ui: BotUi) -> None:
    await state.set_state(AddFilter.waiting_keyword)
    if callback.message:
        await ui.edit(callback.message, "Enter keyword:", reply_markup=_CANCEL_FILTERS)
    await callback.answer()


@router.message(AddFilter.waiting_keyword, F.text)
async def filter_add_text(
    message: Message, container: AppContainer, state: FSMContext, ui: BotUi
) -> None:
    result = await container.bus.execute(
        AddExcludeKeywordCommand(keyword=(message.text or "").strip())
    )
    if not result.ok:
        await ui.reply(message, f"Error: {result.error}", reply_markup=_CANCEL_FILTERS)
        return
    await ui.reply(
        message,
        f"{result.message or 'Added.'}\nЕщё keyword или ◀ Отмена:",
        reply_markup=_CANCEL_FILTERS,
    )


@router.callback_query(F.data.startswith("f:rm:"))
async def cb_filter_remove(callback: CallbackQuery, container: AppContainer, ui: BotUi) -> None:
    keyword = (callback.data or "").split(":", 2)[-1]
    result = await container.bus.execute(RemoveExcludeKeywordCommand(keyword=keyword))
    keywords_result = await container.bus.execute(ListExcludeKeywordsCommand())
    keywords = keywords_result.data or []
    text = "🏷 Exclude keywords:" if keywords else "🏷 No exclude keywords."
    if callback.message:
        await ui.edit(
            callback.message,
            result.message or text,
            reply_markup=filters_menu(keywords),
        )
    await callback.answer()
