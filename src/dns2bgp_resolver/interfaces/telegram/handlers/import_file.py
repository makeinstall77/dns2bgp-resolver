from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import AddDomainCommand, AddPrefixCommand
from dns2bgp_resolver.application.services.list_parse import parse_import_lines
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import confirm_import_menu, main_menu_keyboard

router = Router()
logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_ITEMS = 5000


@dataclass(frozen=True, slots=True)
class _PendingImport:
    domains: list[str]
    prefixes: list[str]


_pending: dict[str, _PendingImport] = {}


def _store_pending(domains: list[str], prefixes: list[str]) -> str:
    token = secrets.token_hex(4)
    _pending[token] = _PendingImport(domains=domains, prefixes=prefixes)
    if len(_pending) > 50:
        for old in list(_pending)[:20]:
            _pending.pop(old, None)
    return token


def _pop_pending(token: str) -> _PendingImport | None:
    return _pending.pop(token, None)


def _looks_like_binary(raw: bytes) -> bool:
    if not raw:
        return True
    sample = raw[:4096]
    if b"\x00" in sample:
        return True
    control = sum(1 for b in sample if b < 9 or (13 < b < 32) or b == 127)
    return (control / len(sample)) > 0.05


@router.message(StateFilter(None), F.document)
async def on_document(message: Message, container: AppContainer) -> None:
    if not allowed(container, message.from_user.id if message.from_user else None):
        return
    if message.document is None or message.bot is None:
        return

    size = message.document.file_size or 0
    if size > _MAX_FILE_BYTES:
        await message.answer(
            f"Файл слишком большой (макс. {_MAX_FILE_BYTES // (1024 * 1024)} МБ).",
            reply_markup=main_menu_keyboard(),
        )
        return

    file = await message.bot.download(message.document)
    raw = file.read()
    if _looks_like_binary(raw):
        await message.answer(
            "Не похоже на текстовый список доменов/префиксов.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = raw.decode("utf-8", errors="replace")
    parsed = parse_import_lines(text)
    domains = sorted(parsed.domains)
    prefixes = sorted(parsed.prefixes)
    if not domains and not prefixes:
        await message.answer(
            "Не удалось найти домены или префиксы "
            "(один домен/CIDR/IP на строку, # — комментарий).",
            reply_markup=main_menu_keyboard(),
        )
        return

    total = len(domains) + len(prefixes)
    if total > _MAX_ITEMS:
        await message.answer(
            f"Слишком много записей: {total} (макс. {_MAX_ITEMS}).",
            reply_markup=main_menu_keyboard(),
        )
        return

    token = _store_pending(domains, prefixes)
    fname = message.document.file_name or "file"
    lines = [f"📄 {fname}"]
    if domains:
        lines.append(f"Доменов: {len(domains)}")
    if prefixes:
        lines.append(f"Префиксов: {len(prefixes)}")
    if parsed.skipped:
        lines.append(f"Пропущено некорректных строк: {parsed.skipped}")

    preview_items: list[str] = []
    for n in domains[:5]:
        preview_items.append(f"• {n}")
    for c in prefixes[: 10 - len(preview_items)]:
        preview_items.append(f"• {c}")
    lines.append("Примеры:\n" + "\n".join(preview_items))
    if total > len(preview_items):
        lines.append(f"… и ещё {total - len(preview_items)}")

    dest: list[str] = []
    if domains:
        dest.append("ручной список (с резолвом)")
    if prefixes:
        dest.append("static prefixes → bird")
    lines.append("\nИмпортировать в " + " и ".join(dest) + "?")
    await message.answer("\n".join(lines), reply_markup=confirm_import_menu(token))


@router.callback_query(F.data.startswith("mi:ok:"))
async def cb_import_ok(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    token = (callback.data or "").split(":", 2)[-1]
    pending = _pop_pending(token)
    if not pending:
        await callback.answer("Импорт устарел. Пришлите файл снова.", show_alert=True)
        return

    await callback.answer()
    total = len(pending.domains) + len(pending.prefixes)
    if callback.message:
        await callback.message.edit_text(f"⏳ Импорт {total} запис(ей)…")

    d_added = d_exists = d_errors = 0
    for name in pending.domains:
        result = await container.bus.execute(AddDomainCommand(name=name))
        if result.ok:
            d_added += 1
        elif result.error and "already exists" in result.error.lower():
            d_exists += 1
        else:
            d_errors += 1
            logger.warning("import domain failed for %s: %s", name, result.error)

    p_added = p_exists = p_errors = 0
    for cidr in pending.prefixes:
        result = await container.bus.execute(AddPrefixCommand(cidr=cidr))
        if result.ok:
            p_added += 1
        elif result.error and "already exists" in result.error.lower():
            p_exists += 1
        else:
            p_errors += 1
            logger.warning("import prefix failed for %s: %s", cidr, result.error)

    parts = ["✅ Импорт завершён."]
    if pending.domains:
        parts.append(
            f"Домены — добавлено: {d_added}, уже были: {d_exists}, ошибки: {d_errors}"
        )
    if pending.prefixes:
        parts.append(
            f"Префиксы — добавлено: {p_added}, уже были: {p_exists}, ошибки: {p_errors}"
        )
    if callback.message:
        await callback.message.edit_text("\n".join(parts))
        await callback.message.answer("Готово.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data.startswith("mi:no:"))
async def cb_import_cancel(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    token = (callback.data or "").split(":", 2)[-1]
    _pop_pending(token)
    if callback.message:
        await callback.message.edit_text("Импорт отменён.")
    await callback.answer("Отменено.")
