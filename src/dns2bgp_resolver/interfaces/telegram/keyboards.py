from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

BTN_HOME = "🏠 Меню"


def home_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_HOME)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Домены", callback_data="m:domains"),
                InlineKeyboardButton(text="🤖 Auto", callback_data="m:auto"),
            ],
            [
                InlineKeyboardButton(text="🛣 Prefixes", callback_data="m:prefixes"),
                InlineKeyboardButton(text="📂 Списки", callback_data="m:lists"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="m:settings"),
                InlineKeyboardButton(text="📊 Статус", callback_data="m:status"),
            ],
            [InlineKeyboardButton(text="🔄 Resolve manual", callback_data="m:resolve")],
        ]
    )


def cancel_inline(back_callback: str = "m:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀ Отмена", callback_data=back_callback)]]
    )


def back_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀ Назад", callback_data="m:main")]]
    )


def status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="m:status:r"),
                InlineKeyboardButton(text="◀ Назад", callback_data="m:main"),
            ]
        ]
    )


def domains_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 List", callback_data="d:list:1"),
                InlineKeyboardButton(text="➕ Add", callback_data="d:add"),
                InlineKeyboardButton(text="🗑 Remove", callback_data="d:rm"),
            ],
            [InlineKeyboardButton(text="📥 Export", callback_data="d:export")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="m:main")],
        ]
    )


def auto_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 List", callback_data="a:list:1"),
                InlineKeyboardButton(text="🔍 Search", callback_data="a:search"),
                InlineKeyboardButton(text="🏷 Filters", callback_data="a:filters"),
            ],
            [InlineKeyboardButton(text="◀ Назад", callback_data="m:main")],
        ]
    )


def prefixes_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 List", callback_data="p:list:1"),
                InlineKeyboardButton(text="➕ Add", callback_data="p:add"),
                InlineKeyboardButton(text="🗑 Remove", callback_data="p:rm"),
            ],
            [InlineKeyboardButton(text="📥 Export", callback_data="p:export")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="m:main")],
        ]
    )


def _page_nav_row(*, prev_data: str, next_data: str, page: int, pages: int) -> list[InlineKeyboardButton]:
    """Always one horizontal row: ◀  N/M  ▶ (noop stubs at edges)."""
    prev = (
        InlineKeyboardButton(text="◀", callback_data=prev_data)
        if page > 1
        else InlineKeyboardButton(text="·", callback_data="noop")
    )
    nxt = (
        InlineKeyboardButton(text="▶", callback_data=next_data)
        if page < pages
        else InlineKeyboardButton(text="·", callback_data="noop")
    )
    mid = InlineKeyboardButton(text=f"{page}/{pages}", callback_data="noop")
    return [prev, mid, nxt]


def prefixes_list_keyboard(
    items: list[tuple[str, str | None]],
    *,
    page: int,
    pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cidr, name in items:
        label = f"{cidr}" if not name else f"{cidr} ({name})"
        if len(label) > 64:
            label = label[:61] + "…"
        rows.append(
            [InlineKeyboardButton(text=f"🗑 {label}", callback_data=f"p:rmok:{page}:{cidr}")]
        )
    if pages > 1:
        rows.append(
            _page_nav_row(
                prev_data=f"p:list:{page - 1}",
                next_data=f"p:list:{page + 1}",
                page=page,
                pages=pages,
            )
        )
    rows.append(
        [
            InlineKeyboardButton(text="➕ Add", callback_data="p:add"),
            InlineKeyboardButton(text="◀ Назад", callback_data="m:prefixes"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def host_list_keyboard(
    *,
    prefix: str,
    items: list[tuple[int, str, int | None]],
    page: int,
    pages: int,
    back_callback: str,
    query_key: str | None = None,
    show_addr_count: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for domain_id, name, addr_count in items:
        if show_addr_count and addr_count is not None:
            label = f"🌐 {name} ({addr_count})"
        else:
            label = f"🌐 {name}"
        if len(label) > 64:
            if show_addr_count and addr_count is not None:
                label = f"🌐 {name[:50]}… ({addr_count})"
            else:
                label = f"🌐 {name[:58]}…"
        if query_key is not None:
            cb = f"{prefix}:h:{domain_id}:{page}:{query_key}"
        else:
            cb = f"{prefix}:h:{domain_id}:{page}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    if pages > 1:
        if query_key is not None:
            prev_data = f"s:{page - 1}:{query_key}"
            next_data = f"s:{page + 1}:{query_key}"
        else:
            prev_data = f"{prefix}:list:{page - 1}"
            next_data = f"{prefix}:list:{page + 1}"
        rows.append(
            _page_nav_row(
                prev_data=prev_data,
                next_data=next_data,
                page=page,
                pages=pages,
            )
        )
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_host_menu(
    domain_id: int,
    page: int,
    *,
    is_mask: bool = False,
    suppress_ipv6: str = "default",
) -> InlineKeyboardMarkup:
    actions: list[InlineKeyboardButton] = []
    if not is_mask:
        actions.append(
            InlineKeyboardButton(
                text="🔄 Обновить IP", callback_data=f"d:rs:{domain_id}:{page}"
            )
        )
    actions.append(
        InlineKeyboardButton(
            text="🗑 Удалить", callback_data=f"d:rmid:{domain_id}:{page}"
        )
    )
    if suppress_ipv6 == "on":
        v6_label = "🚫 AAAA выкл"
    elif suppress_ipv6 == "off":
        v6_label = "✅ AAAA вкл"
    else:
        v6_label = "⚙️ AAAA дефолт"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            actions,
            [
                InlineKeyboardButton(
                    text=v6_label, callback_data=f"d:v6:{domain_id}:{page}"
                )
            ],
            [InlineKeyboardButton(text="◀ К списку", callback_data=f"d:list:{page}")],
        ]
    )


def auto_host_menu(
    domain_id: int, page: int, *, query_key: str | None = None
) -> InlineKeyboardMarkup:
    if query_key is not None:
        back = f"s:{page}:{query_key}"
    else:
        back = f"a:list:{page}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀ К списку", callback_data=back)],
        ]
    )


def confirm_remove_host_menu(domain_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да", callback_data=f"d:rmok:{domain_id}:{page}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"d:h:{domain_id}:{page}"
                ),
            ]
        ]
    )


def confirm_import_menu(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Импортировать", callback_data=f"mi:ok:{token}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"mi:no:{token}"),
            ]
        ]
    )


def lists_menu(list_buttons: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(list_buttons), 2):
        rows.append(list_buttons[i : i + 2])
    rows.append(
        [
            InlineKeyboardButton(text="➕ Add URL", callback_data="l:addurl"),
            InlineKeyboardButton(text="📤 Upload file", callback_data="l:upload"),
        ]
    )
    rows.append([InlineKeyboardButton(text="🔄 Sync all", callback_data="l:syncall")])
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="m:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def list_detail_menu(list_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle = "⏸ Disable" if enabled else "▶️ Enable"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=toggle, callback_data=f"l:en:{list_id}"),
                InlineKeyboardButton(text="🔄 Sync", callback_data=f"l:sync:{list_id}"),
            ],
            [
                InlineKeyboardButton(text="🧹 Clear", callback_data=f"l:clr:{list_id}"),
                InlineKeyboardButton(text="🗑 Delete", callback_data=f"l:del:{list_id}"),
            ],
            [
                InlineKeyboardButton(text="⏱ Interval", callback_data=f"l:int:{list_id}"),
                InlineKeyboardButton(text="◀ Lists", callback_data="m:lists"),
            ],
        ]
    )


def confirm_menu(action: str, list_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"l:cf:{action}:{list_id}:yes"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"l:view:{list_id}"),
            ]
        ]
    )


def settings_menu(
    *,
    suppress_manual: bool = True,
    suppress_auto: bool = True,
) -> InlineKeyboardMarkup:
    manual_label = (
        "🚫 Manual AAAA: выкл" if suppress_manual else "✅ Manual AAAA: вкл"
    )
    auto_label = "🚫 Auto AAAA: выкл" if suppress_auto else "✅ Auto AAAA: вкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ Default interval", callback_data="st:interval")],
            [
                InlineKeyboardButton(text=manual_label, callback_data="st:v6:manual"),
                InlineKeyboardButton(text=auto_label, callback_data="st:v6:auto"),
            ],
            [InlineKeyboardButton(text="🏷 Exclude keywords", callback_data="st:filters")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="m:main")],
        ]
    )


def filters_menu(keywords: list[str], *, back_callback: str = "m:auto") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for kw in keywords[:20]:
        rows.append([InlineKeyboardButton(text=f"🗑 {kw}", callback_data=f"f:rm:{kw}")])
    rows.append(
        [
            InlineKeyboardButton(text="➕ Add", callback_data="f:add"),
            InlineKeyboardButton(text="◀ Назад", callback_data=back_callback),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_keyboard(query_key: str, page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pages > 1:
        rows.append(
            _page_nav_row(
                prev_data=f"s:{page - 1}:{query_key}",
                next_data=f"s:{page + 1}:{query_key}",
                page=page,
                pages=pages,
            )
        )
    rows.append([InlineKeyboardButton(text="◀ Auto", callback_data="m:auto")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
