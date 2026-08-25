from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    ListDomainsCommand,
    RemoveDomainCommand,
    ResolveNowCommand,
)
from dns2bgp_resolver.container import AppContainer
from dns2bgp_resolver.domain import format_domain_label
from dns2bgp_resolver.interfaces.telegram.auth import allowed
from dns2bgp_resolver.interfaces.telegram.keyboards import (
    BTN_CANCEL,
    cancel_keyboard,
    confirm_remove_host_menu,
    domains_menu,
    host_list_keyboard,
    main_menu_keyboard,
    manual_host_menu,
)
from dns2bgp_resolver.interfaces.telegram.states import AddDomain, RemoveDomain

router = Router()

_PAGE_SIZE = 10


async def _render_manual_page(container: AppContainer, page: int) -> tuple[str, object]:
    result = await container.bus.execute(
        ListDomainsCommand(page=page, page_size=_PAGE_SIZE)
    )
    if not result.ok or result.data is None:
        return f"Error: {result.error}", domains_menu()

    data = result.data
    if not data.items:
        return "📋 Manual domains: пусто.", domains_menu()

    text = f"📋 Manual — стр. {data.page}/{data.pages} ({data.total})"
    items = [
        (
            d.id or 0,
            d.label,
            None if d.match_mode == "suffix" else len(d.addresses),
        )
        for d in data.items
        if d.id is not None
    ]
    markup = host_list_keyboard(
        prefix="d",
        items=items,
        page=data.page,
        pages=data.pages,
        back_callback="m:domains",
    )
    return text, markup


def _parse_id_page(data: str) -> tuple[int, int] | None:
    parts = data.split(":")
    if len(parts) < 4:
        return None
    try:
        return int(parts[2]), int(parts[3])
    except ValueError:
        return None


def _host_text(domain) -> str:
    mode = getattr(domain, "match_mode", "exact") or "exact"
    label = format_domain_label(str(domain.name), mode)
    if mode == "suffix":
        return f"🌐 {label}\nmatch: suffix (поддомены через dnstap)"
    ips = ", ".join(str(a.ip) for a in domain.addresses) or "—"
    return f"🌐 {label}\nIP ({len(domain.addresses)}): {ips}"


async def _back_to_domains(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Manual domains (pre-resolve).\nМожно: example.com или *.example.com",
        reply_markup=main_menu_keyboard(),
    )
    await message.answer("Выберите действие:", reply_markup=domains_menu())


@router.callback_query(F.data == "d:list")
@router.callback_query(F.data.startswith("d:list:"))
async def cb_list(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    page = 1
    raw = callback.data or ""
    if raw.startswith("d:list:"):
        try:
            page = int(raw.split(":")[2])
        except (IndexError, ValueError):
            page = 1
    text, markup = await _render_manual_page(container, page)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("d:h:"))
async def cb_host(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parsed = _parse_id_page(callback.data or "")
    if parsed is None:
        await callback.answer("Invalid callback.")
        return
    domain_id, page = parsed

    domain = await container.repository.get_by_id(domain_id)
    if domain is None or domain.source != "manual":
        await callback.answer("Host not found.", show_alert=True)
        return
    if callback.message:
        mode = getattr(domain, "match_mode", "exact") or "exact"
        await callback.message.edit_text(
            _host_text(domain),
            reply_markup=manual_host_menu(domain_id, page, is_mask=mode == "suffix"),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("d:rs:"))
async def cb_refresh(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parsed = _parse_id_page(callback.data or "")
    if parsed is None:
        await callback.answer("Invalid callback.")
        return
    domain_id, page = parsed

    domain = await container.repository.get_by_id(domain_id)
    if domain is None:
        await callback.answer("Host not found.", show_alert=True)
        return
    if (getattr(domain, "match_mode", None) or "exact") == "suffix":
        await callback.answer("Маска: IP только через dnstap", show_alert=True)
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
        mode = getattr(domain, "match_mode", "exact") or "exact"
        await callback.message.edit_text(
            _host_text(domain),
            reply_markup=manual_host_menu(domain_id, page, is_mask=mode == "suffix"),
        )
    await callback.answer(toast, show_alert=True)


@router.callback_query(F.data.startswith("d:rmid:"))
async def cb_remove_prompt(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parsed = _parse_id_page(callback.data or "")
    if parsed is None:
        await callback.answer("Invalid callback.")
        return
    domain_id, page = parsed
    domain = await container.repository.get_by_id(domain_id)
    if domain is None:
        await callback.answer("Host not found.", show_alert=True)
        return
    mode = getattr(domain, "match_mode", "exact") or "exact"
    label = format_domain_label(str(domain.name), mode)
    if callback.message:
        await callback.message.edit_text(
            f"Удалить 🌐 {label}?",
            reply_markup=confirm_remove_host_menu(domain_id, page),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("d:rmok:"))
async def cb_remove_confirm(callback: CallbackQuery, container: AppContainer) -> None:
    if not allowed(container, callback.from_user.id if callback.from_user else None):
        await callback.answer("Access denied.", show_alert=True)
        return
    parsed = _parse_id_page(callback.data or "")
    if parsed is None:
        await callback.answer("Invalid callback.")
        return
    domain_id, page = parsed
    domain = await container.repository.get_by_id(domain_id)
    if domain is None:
        await callback.answer("Host not found.", show_alert=True)
        return
    result = await container.bus.execute(RemoveDomainCommand(name=str(domain.name)))
    if not result.ok:
        await callback.answer(result.error or "Error", show_alert=True)
        return
    text, markup = await _render_manual_page(container, page)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer("Удалено.")


@router.callback_query(F.data == "d:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddDomain.waiting_name)
    if callback.message:
        await callback.message.answer(
            "Домен или маска:\nexample.com / *.example.com",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "d:rm")
async def cb_remove(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RemoveDomain.waiting_name)
    if callback.message:
        await callback.message.answer("Enter domain to remove:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AddDomain.waiting_name, F.text)
async def add_domain_text(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_domains(message, state)
        return
    result = await container.bus.execute(AddDomainCommand(name=(message.text or "").strip()))
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    view = result.data
    label = view.label if view else (message.text or "")
    if view and view.match_mode == "suffix":
        text = f"Added {label}"
    else:
        ips = ", ".join(view.addresses) if view and view.addresses else "-"
        text = f"Added {label}: {ips}"
    await message.answer(
        f"{text}\nЕщё домен или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )


@router.message(RemoveDomain.waiting_name, F.text)
async def remove_domain_text(message: Message, container: AppContainer, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await _back_to_domains(message, state)
        return
    result = await container.bus.execute(RemoveDomainCommand(name=(message.text or "").strip()))
    if not result.ok:
        await message.answer(f"Error: {result.error}", reply_markup=cancel_keyboard())
        return
    await message.answer(
        f"{result.message or 'Removed.'}\nЕщё домен или ◀ Отмена:",
        reply_markup=cancel_keyboard(),
    )
