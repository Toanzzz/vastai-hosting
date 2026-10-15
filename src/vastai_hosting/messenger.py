import re
from typing import Any

from .buttons import report_keyboard
from .config import Config, redact
from .status import CycleStatus, escape_html, status_text
from .subscribers import Subscribers
from .telegram_api import TelegramApi, TelegramError

# Telegram returns message text without the HTML tags the bot sent.
_TAG = re.compile(r"</?[a-z]+>")


class Messenger:
    """Outgoing Telegram messages. Remembers recent status HTML so edits keep formatting."""

    def __init__(self, config: Config, api: TelegramApi, subscribers: Subscribers) -> None:
        self._config = config
        self._api = api
        self._subscribers = subscribers
        self._sent: dict[tuple[int, int], str] = {}

    def send_status(self, chat_id: int, status: CycleStatus) -> None:
        text = status_text(status, self._config)
        params: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        keyboard = report_keyboard(status, self._config)
        if keyboard is not None:
            params["reply_markup"] = keyboard
        try:
            sent = self._api.call("sendMessage", **params)
        except TelegramError as error:
            if error.code == 403:
                self._subscribers.remove(chat_id)
                print(f"telegram chat {chat_id} blocked the bot; unsubscribed")
                return
            self._log(f"telegram send failed for chat {chat_id}", error)
            return
        message_id = sent.get("message_id") if isinstance(sent, dict) else None
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            self._remember(chat_id, message_id, text)

    def edit(
        self, message: dict[str, Any], chat_id: int, note: str, keyboard: dict[str, Any] | None
    ) -> None:
        """Append a note to a status message and replace its buttons; None removes them."""
        original = message.get("text")
        message_id = message.get("message_id")
        if (
            not isinstance(original, str)
            or original.endswith(_TAG.sub("", note))
            or not isinstance(message_id, int)
            or isinstance(message_id, bool)
        ):
            return
        stored = self._sent.get((chat_id, message_id))
        text = f"{stored if stored is not None else escape_html(original)}\n{note}"
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if keyboard is not None:
            params["reply_markup"] = keyboard
        try:
            self._api.call("editMessageText", **params)
        except TelegramError as error:
            self._log(f"telegram edit failed for chat {chat_id}", error)
            return
        self._remember(chat_id, message_id, text)

    def reply(self, chat_id: int, text: str) -> None:
        try:
            self._api.call("sendMessage", chat_id=chat_id, text=text)
        except TelegramError as error:
            self._log(f"telegram send failed for chat {chat_id}", error)

    def answer(self, query_id: str, text: str, *, alert: bool) -> None:
        try:
            self._api.call(
                "answerCallbackQuery", callback_query_id=query_id, text=text, show_alert=alert
            )
        except TelegramError as error:
            self._log("telegram callback answer failed", error)

    def _remember(self, chat_id: int, message_id: int, text: str) -> None:
        self._sent[(chat_id, message_id)] = text
        if len(self._sent) > 40:
            del self._sent[next(iter(self._sent))]

    def _log(self, context: str, error: TelegramError) -> None:
        print(f"{context}: {redact(str(error), self._config)}")
