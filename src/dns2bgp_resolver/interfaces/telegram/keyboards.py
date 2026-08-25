from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

BTN_DOMAINS = "📋 Домены"
BTN_AUTO = "🤖 Auto"
BTN_PREFIXES = "🛣 Prefixes"
BTN_LISTS = "📂 Списки"
BTN_SETTINGS = "⚙️ Настройки"
BTN_STATUS = "📊 Статус"
BTN_RESOLVE = "🔄 Resolve manual"
BTN_CANCEL = "◀ Отмена"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DOMAINS), KeyboardButton(text=BTN_AUTO)],
            [KeyboardButton(text=BTN_PREFIXES), KeyboardButton(text=BTN_LISTS)],
            [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_STATUS)],
            [KeyboardButton(text=BTN_RESOLVE)],
        ],
        resize_keyboard=True,
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def back_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀ Назад", callback_data="m:main")]]
    )


def domains_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 List", callback_data="d:list:1"),
                InlineKeyboardButton(text="➕ Add", callback_data="d:add"),
                InlineKeyboardButton(text="🗑 Remove", callback_data="d:rm"),
            ],
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
                InlineKeyboardButton(text="📋 List", callback_data="p:list"),
                InlineKeyboardButton(text="➕ Add", callback_data="p:add"),
                InlineKeyboardButton(text="🗑 Remove", callback_data="p:rm"),
            ],
            [InlineKeyboardButton(text="◀ Назад", callback_data="m:main")],
        ]
    )


def prefixes_list_keyboard(items: list[tuple[str, str | None]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cidr, name in items[:30]:
        label = f"{cidr}" if not name else f"{cidr} ({name})"
        if len(label) > 64:
            label = label[:61] + "…"
        rows.append(
            [InlineKeyboardButton(text=f"🗑 {label}", callback_data=f"p:rmok:{cidr}")]
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

    nav: list[InlineKeyboardButton] = []
    if query_key is not None:
        prev_data = f"s:{page - 1}:{query_key}"
        next_data = f"s:{page + 1}:{query_key}"
    else:
        prev_data = f"{prefix}:list:{page - 1}"
        next_data = f"{prefix}:list:{page + 1}"
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀", callback_data=prev_data))
    if page < pages:
        nav.append(InlineKeyboardButton(text="▶", callback_data=next_data))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_host_menu(
    domain_id: int, page: int, *, is_mask: bool = False
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            actions,
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


def settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ Default interval", callback_data="st:interval")],
            [InlineKeyboardButton(text="🏷 Exclude keywords", callback_data="st:filters")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="m:main")],
        ]
    )


def filters_menu(keywords: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for kw in keywords[:20]:
        rows.append([InlineKeyboardButton(text=f"🗑 {kw}", callback_data=f"f:rm:{kw}")])
    rows.append(
        [
            InlineKeyboardButton(text="➕ Add", callback_data="f:add"),
            InlineKeyboardButton(text="◀ Назад", callback_data="m:auto"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_keyboard(query_key: str, page: int, pages: int) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if page > 1:
        buttons.append(
            InlineKeyboardButton(text="◀", callback_data=f"s:{page - 1}:{query_key}")
        )
    if page < pages:
        buttons.append(
            InlineKeyboardButton(text="▶", callback_data=f"s:{page + 1}:{query_key}")
        )
    nav = [buttons] if buttons else []
    nav.append([InlineKeyboardButton(text="◀ Auto", callback_data="m:auto")])
    return InlineKeyboardMarkup(inline_keyboard=nav)
