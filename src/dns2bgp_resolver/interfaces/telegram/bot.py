from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    ListDomainsCommand,
    RemoveDomainCommand,
    ResolveNowCommand,
)
from dns2bgp_resolver.container import AppContainer

logger = logging.getLogger(__name__)


def _allowed(container: AppContainer, user_id: int | None) -> bool:
    allow = container.settings.telegram.allowed_user_ids
    if not allow:
        # Empty allowlist = deny all (safer default)
        return False
    return user_id is not None and user_id in allow


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
            "/resolve [domain]"
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
            await message.answer("No domains.")
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

    @dp.message(F.text)
    async def fallback(message: Message) -> None:
        if not _allowed(container, message.from_user.id if message.from_user else None):
            return
        await message.answer("Unknown command. Try /help")

    logger.info("telegram polling started")
    await dp.start_polling(bot)
