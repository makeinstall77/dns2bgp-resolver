from __future__ import annotations

import logging
import secrets

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import AddDomainCommand
from dns2bgp_resolver.application.services.auto_list_sync import parse_domain_lines
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import confirm_import_menu, main_menu_keyboard

router = Router()
logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_DOMAINS = 5000
_pending: dict[str, list[str]] = {}


def _store_pending(names: list[str]) -> str:
    token = secrets.token_hex(4)
    _pending[token] = names
    if len(_pending) > 50:
        for old in list(_pending)[:20]:
            _pending.pop(old, None)
    return token


def _pop_pending(token: str) -> list[str] | None:
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
            "Не похоже на текстовый список доменов.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = raw.decode("utf-8", errors="replace")
    names_set, skipped_invalid = parse_domain_lines(text)
    names = sorted(names_set)
    if not names:
        await message.answer(
            "Не удалось найти домены (ожидается один домен на строку).",
            reply_markup=main_menu_keyboard(),
        )
        return

    if len(names) > _MAX_DOMAINS:
        await message.answer(
            f"Слишком много доменов: {len(names)} (макс. {_MAX_DOMAINS}).",
            reply_markup=main_menu_keyboard(),
        )
        return

    token = _store_pending(names)
    fname = message.document.file_name or "file"
    lines = [
        f"📄 {fname}",
        f"Найдено доменов: {len(names)}",
    ]
    if skipped_invalid:
        lines.append(f"Пропущено некорректных строк: {skipped_invalid}")
    preview = names[:10]
    lines.append("Примеры:\n" + "\n".join(f"• {n}" for n in preview))
    if len(names) > 10:
        lines.append(f"… и ещё {len(names) - 10}")
    lines.append("\nИмпортировать в ручной список (с резолвом IP)?")
    await message.answer("\n".join(lines), reply_markup=confirm_import_menu(token))


@router.callback_query(F.data.startswith("mi:ok:"))
async def cb_import_ok(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    token = (callback.data or "").split(":", 2)[-1]
    names = _pop_pending(token)
    if not names:
        await callback.answer("Импорт устарел. Пришлите файл снова.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(f"⏳ Импорт {len(names)} домен(ов)…")

    added = 0
    exists = 0
    errors = 0
    for name in names:
        result = await container.bus.execute(AddDomainCommand(name=name))
        if result.ok:
            added += 1
        elif result.error and "already exists" in result.error.lower():
            exists += 1
        else:
            errors += 1
            logger.warning("import failed for %s: %s", name, result.error)

    summary = (
        f"✅ Импорт завершён.\n"
        f"Добавлено: {added}\n"
        f"Уже были: {exists}\n"
        f"Ошибки: {errors}"
    )
    if callback.message:
        await callback.message.edit_text(summary)
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
