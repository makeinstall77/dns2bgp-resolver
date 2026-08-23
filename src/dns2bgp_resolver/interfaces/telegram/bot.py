from __future__ import annotations

import hashlib
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    AddExcludeKeywordCommand,
    ListDomainsCommand,
    ListExcludeKeywordsCommand,
    RemoveDomainCommand,
    RemoveExcludeKeywordCommand,
    ResolveNowCommand,
    SearchAutoDomainsCommand,
)
from dns2bgp_resolver.container import AppContainer

logger = logging.getLogger(__name__)

_SEARCH_PAGE_SIZE = 15
_search_cache: dict[str, str] = {}


def _allowed(container: AppContainer, user_id: int | None) -> bool:
    allow = container.settings.telegram.allowed_user_ids
    if not allow:
        return False
    return user_id is not None and user_id in allow


def _cache_query(query: str) -> str:
    key = hashlib.sha256(query.encode()).hexdigest()[:8]
    _search_cache[key] = query
    if len(_search_cache) > 200:
        for old_key in list(_search_cache)[:50]:
            _search_cache.pop(old_key, None)
    return key


def _resolve_query(key: str) -> str:
    return _search_cache.get(key, "")


def _search_keyboard(query_key: str, page: int, pages: int) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if page > 1:
        buttons.append(
            InlineKeyboardButton(text="◀ Prev", callback_data=f"s:{page - 1}:{query_key}")
        )
    if page < pages:
        buttons.append(
            InlineKeyboardButton(text="Next ▶", callback_data=f"s:{page + 1}:{query_key}")
        )
    return InlineKeyboardMarkup(inline_keyboard=[buttons] if buttons else [])


async def _render_search(
    container: AppContainer,
    query: str,
    page: int,
) -> tuple[str, InlineKeyboardMarkup | None]:
    result = await container.bus.execute(
        SearchAutoDomainsCommand(query=query, page=page, page_size=_SEARCH_PAGE_SIZE)
    )
    if not result.ok or result.data is None:
        return f"Error: {result.error}", None

    data = result.data
    if not data.items:
        return f"No auto domains match {query!r}.", None

    lines = [f"Auto search {query!r} — page {data.page}/{data.pages} ({data.total} total)"]
    for d in data.items:
        ips = ", ".join(d.addresses) if d.addresses else "-"
        lines.append(f"{d.name}: {ips}")

    query_key = _cache_query(query)
    markup = _search_keyboard(query_key, data.page, data.pages)
    return "\n".join(lines), markup


async def run_telegram_bot(container: AppContainer) -> None:
    token = container.settings.telegram.token
    if not token:
        raise RuntimeError("telegram token is empty")

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(Command("start", "help"))
    async def cmd_help(message: Message) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            await message.answer("Access denied.")
            return
        await message.answer(
            "dns2bgp commands:\n"
            "/add <domain>\n"
            "/remove <domain>\n"
            "/list\n"
            "/resolve [domain]\n"
            "/search <query>\n"
            "/filter\n"
            "/filter add <word>\n"
            "/filter remove <word>"
        )

    @dp.message(Command("add"))
    async def cmd_add(message: Message, command: CommandObject) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            await message.answer("Access denied.")
            return
        name = (command.args or "").strip()
        if not name:
            await message.answer("Usage: /add example.com")
            return
        result = await container.bus.execute(AddDomainCommand(name=name))
        if not result.ok:
            await message.answer(f"Error: {result.error}")
            return
        ips = ", ".join(result.data.addresses) if result.data and result.data.addresses else "-"
        await message.answer(f"Added {name}: {ips}")

    @dp.message(Command("remove"))
    async def cmd_remove(message: Message, command: CommandObject) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            await message.answer("Access denied.")
            return
        name = (command.args or "").strip()
        if not name:
            await message.answer("Usage: /remove example.com")
            return
        result = await container.bus.execute(RemoveDomainCommand(name=name))
        if not result.ok:
            await message.answer(f"Error: {result.error}")
            return
        await message.answer(result.message or f"Removed {name}")

    @dp.message(Command("list"))
    async def cmd_list(message: Message) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            await message.answer("Access denied.")
            return
        result = await container.bus.execute(ListDomainsCommand())
        if not result.ok:
            await message.answer(f"Error: {result.error}")
            return
        domains = result.data or []
        if not domains:
            await message.answer("No manual domains.")
            return
        lines = []
        for d in domains:
            ips = ", ".join(d.addresses) if d.addresses else "-"
            lines.append(f"{d.name}: {ips}")
        await message.answer("\n".join(lines))

    @dp.message(Command("resolve"))
    async def cmd_resolve(message: Message, command: CommandObject) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            await message.answer("Access denied.")
            return
        name = (command.args or "").strip() or None
        result = await container.bus.execute(ResolveNowCommand(name=name))
        if not result.ok:
            await message.answer(f"Error: {result.error}")
            return
        lines = []
        for s in result.data or []:
            if s.error:
                lines.append(f"{s.domain}: ERROR {s.error}")
            else:
                flag = "changed" if s.changed else "ok"
                lines.append(f"{s.domain}: {flag} [{', '.join(s.addresses)}]")
        await message.answer("\n".join(lines) or "Nothing to resolve.")

    @dp.message(Command("search"))
    async def cmd_search(message: Message, command: CommandObject) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            await message.answer("Access denied.")
            return
        query = (command.args or "").strip()
        if not query:
            await message.answer("Usage: /search casino")
            return
        text, markup = await _render_search(container, query, page=1)
        await message.answer(text, reply_markup=markup)

    @dp.callback_query(F.data.startswith("s:"))
    async def cb_search_page(callback: CallbackQuery) -> None:
        if not _allowed(container, callback.from_user.id if callback.from_user else None):
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

    @dp.message(Command("filter"))
    async def cmd_filter(message: Message, command: CommandObject) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            await message.answer("Access denied.")
            return
        args = (command.args or "").strip()
        if not args:
            result = await container.bus.execute(ListExcludeKeywordsCommand())
            if not result.ok:
                await message.answer(f"Error: {result.error}")
                return
            keywords = result.data or []
            if not keywords:
                await message.answer("No exclude keywords.")
                return
            await message.answer("Exclude keywords:\n" + "\n".join(keywords))
            return

        parts = args.split(maxsplit=1)
        action = parts[0].lower()
        if action not in ("add", "remove") or len(parts) < 2:
            await message.answer("Usage: /filter add <word> | /filter remove <word>")
            return
        keyword = parts[1].strip()
        if action == "add":
            result = await container.bus.execute(AddExcludeKeywordCommand(keyword=keyword))
        else:
            result = await container.bus.execute(RemoveExcludeKeywordCommand(keyword=keyword))
        if not result.ok:
            await message.answer(f"Error: {result.error}")
            return
        await message.answer(result.message or "OK")

    @dp.message(F.text)
    async def fallback(message: Message) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            return
        await message.answer("Unknown command. Try /help")

    logger.info("telegram polling started")
    await dp.start_polling(bot)
