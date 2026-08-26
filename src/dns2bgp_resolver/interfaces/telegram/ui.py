from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

from dns2bgp_resolver.interfaces.telegram.keyboards import home_keyboard

logger = logging.getLogger(__name__)

ReplyMarkup = InlineKeyboardMarkup | ReplyKeyboardMarkup


class BotUi:
    """One active bot screen per chat: new screens replace the previous message."""

    def __init__(self) -> None:
        self._screens: dict[int, int] = {}
        self._generation: dict[int, int] = {}
        self._home_ready: set[int] = set()

    def generation(self, chat_id: int) -> int:
        return self._generation.get(chat_id, 0)

    def screen_id(self, chat_id: int) -> int | None:
        return self._screens.get(chat_id)

    def remember(self, chat_id: int, message_id: int) -> None:
        self._screens[chat_id] = message_id

    def bump(self, chat_id: int) -> int:
        gen = self._generation.get(chat_id, 0) + 1
        self._generation[chat_id] = gen
        return gen

    async def _delete_screen(self, bot: Bot, chat_id: int) -> None:
        old_id = self._screens.pop(chat_id, None)
        if old_id is None:
            return
        try:
            await bot.delete_message(chat_id, old_id)
        except TelegramBadRequest:
            pass
        except Exception:  # noqa: BLE001
            logger.debug("failed to delete screen %s/%s", chat_id, old_id, exc_info=True)

    async def _delete_message(self, bot: Bot, chat_id: int, message_id: int) -> None:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            pass
        except Exception:  # noqa: BLE001
            logger.debug("failed to delete message %s/%s", chat_id, message_id, exc_info=True)

    async def ensure_home_keyboard(self, bot: Bot, chat_id: int) -> None:
        if chat_id in self._home_ready:
            return
        try:
            tmp = await bot.send_message(chat_id, "\u2060", reply_markup=home_keyboard())
            await self._delete_message(bot, chat_id, tmp.message_id)
            self._home_ready.add(chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("failed to set home keyboard for %s", chat_id, exc_info=True)

    async def show(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        *,
        reply_markup: ReplyMarkup | None = None,
        delete_message_id: int | None = None,
        **kwargs: Any,
    ) -> Message:
        await self.ensure_home_keyboard(bot, chat_id)
        await self._delete_screen(bot, chat_id)
        if delete_message_id is not None:
            await self._delete_message(bot, chat_id, delete_message_id)
        msg = await bot.send_message(chat_id, text, reply_markup=reply_markup, **kwargs)
        self._screens[chat_id] = msg.message_id
        self.bump(chat_id)
        return msg

    async def reply(
        self,
        message: Message,
        text: str,
        *,
        reply_markup: ReplyMarkup | None = None,
        delete_user: bool = True,
        **kwargs: Any,
    ) -> Message:
        if message.bot is None:
            raise RuntimeError("message.bot is None")
        delete_id = message.message_id if delete_user else None
        return await self.show(
            message.bot,
            message.chat.id,
            text,
            reply_markup=reply_markup,
            delete_message_id=delete_id,
            **kwargs,
        )

    async def edit(
        self,
        message: Message,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        **kwargs: Any,
    ) -> Message:
        try:
            edited = await message.edit_text(text, reply_markup=reply_markup, **kwargs)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                self.remember(message.chat.id, message.message_id)
                self.bump(message.chat.id)
                return message
            if message.bot is None:
                raise
            return await self.show(
                message.bot,
                message.chat.id,
                text,
                reply_markup=reply_markup,
                **kwargs,
            )
        msg = edited if isinstance(edited, Message) else message
        self.remember(msg.chat.id, msg.message_id)
        self.bump(msg.chat.id)
        return msg

    async def edit_by_id(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        if_generation: int | None = None,
        **kwargs: Any,
    ) -> bool:
        if if_generation is not None and self.generation(chat_id) != if_generation:
            return False
        if self.screen_id(chat_id) not in (None, message_id):
            return False
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
                **kwargs,
            )
            if if_generation is not None and self.generation(chat_id) != if_generation:
                return False
            self.remember(chat_id, message_id)
            return True
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return True
            return False
        except Exception:  # noqa: BLE001
            logger.debug("edit_by_id failed %s/%s", chat_id, message_id, exc_info=True)
            return False
