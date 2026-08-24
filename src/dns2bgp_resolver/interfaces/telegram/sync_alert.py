from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from dns2bgp_resolver.application.ports.repository import SyncPendingConfirmation
from dns2bgp_resolver.application.ports.sync_alert import SyncAlertNotifier
from dns2bgp_resolver.config import TelegramSettings

logger = logging.getLogger(__name__)


def dangerous_sync_keyboard(list_id: int, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Запустить",
                    callback_data=f"l:syncforce:{list_id}:{token}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"l:synccancel:{list_id}:{token}",
                ),
            ]
        ]
    )


class TelegramSyncAlertNotifier(SyncAlertNotifier):
    def __init__(self, settings: TelegramSettings, bot: Bot | None = None) -> None:
        self._settings = settings
        self._bot = bot
        self._owned_bot = False

    def bind_bot(self, bot: Bot) -> None:
        self._bot = bot
        self._owned_bot = False

    def _ensure_bot(self) -> Bot | None:
        if self._bot is not None:
            return self._bot
        if not self._settings.token:
            return None
        self._bot = Bot(token=self._settings.token)
        self._owned_bot = True
        return self._bot

    async def notify_dangerous_sync(self, pending: SyncPendingConfirmation) -> None:
        bot = self._ensure_bot()
        recipients = self._settings.allowed_user_ids
        if bot is None or not recipients:
            logger.warning(
                "cannot send sync alert for list %s: bot/token or allowed_user_ids missing",
                pending.list_name,
            )
            return

        ratio = (
            (pending.would_remove / pending.current_count * 100.0)
            if pending.current_count
            else 0.0
        )
        text = (
            f"⚠️ Dangerous sync: {pending.list_name}\n"
            f"would remove {pending.would_remove}/{pending.current_count} ({ratio:.0f}%), "
            f"add {pending.would_add}.\n"
            f"Apply?"
        )
        markup = dangerous_sync_keyboard(pending.list_id, pending.token)
        for user_id in recipients:
            try:
                await bot.send_message(user_id, text, reply_markup=markup)
            except Exception:  # noqa: BLE001
                logger.exception("failed to send sync alert to %s", user_id)

    async def close(self) -> None:
        if self._owned_bot and self._bot is not None:
            await self._bot.session.close()
            self._bot = None
