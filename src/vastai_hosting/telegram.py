import threading
import time
from hmac import compare_digest
from typing import Any, Protocol, TypeGuard

from .config import Config
from .status import CycleStatus, keyboard, parse_cents, status_text
from .subscribers import Subscribers
from .telegram_api import TelegramApi, TelegramError

# answerCallbackQuery must return before the client gives up on the button.
_POLL_SECONDS = 50


class _Listing(Protocol):
    def update_listing(self, config: Config, target: float) -> None: ...


def redact(text: str, config: Config) -> str:
    for secret in (
        config.telegram_bot_token,
        config.telegram_subscribe_secret,
        config.vast_api_key,
        config.gemini_api_key,
    ):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


class PriceBot:
    def __init__(
        self,
        config: Config,
        vast: _Listing,
        subscribers: Subscribers,
        api: TelegramApi | None = None,
    ) -> None:
        self._config = config
        self._vast = vast
        self._subscribers = subscribers
        self._api = api or TelegramApi(config.telegram_bot_token)
        self._last: CycleStatus | None = None
        self._status_lock = threading.Lock()

    def start(self) -> None:
        username = self._username()
        label = f"@{username}" if username else "bot"
        print(f"telegram {label} polling")
        threading.Thread(target=self._poll, name="telegram", daemon=True).start()

    def publish(self, status: CycleStatus) -> None:
        with self._status_lock:
            self._last = status
        for chat_id in self._subscribers.chats():
            self._send_status(chat_id, status)

    def handle(self, update: dict[str, Any]) -> None:
        if isinstance(update.get("message"), dict):
            self._on_message(update["message"])
        elif isinstance(update.get("callback_query"), dict):
            self._on_callback(update["callback_query"])

    def _username(self) -> str | None:
        try:
            me = self._api.call("getMe")
        except TelegramError as error:
            if error.code == 401:
                raise RuntimeError("Telegram bot token was rejected") from error
            print(f"telegram getMe failed: {redact(str(error), self._config)}")
            return None
        username = me.get("username") if isinstance(me, dict) else None
        return username if isinstance(username, str) and username else None

    def _poll(self) -> None:
        offset: int | None = None
        while True:
            try:
                updates = self._api.call(
                    "getUpdates",
                    offset=offset,
                    timeout=_POLL_SECONDS,
                    allowed_updates=["message", "callback_query"],
                )
            except TelegramError as error:
                print(f"telegram poll failed: {redact(str(error), self._config)}")
                time.sleep(5)
                continue
            if not isinstance(updates, list):
                time.sleep(5)
                continue
            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if isinstance(update_id, int) and not isinstance(update_id, bool):
                    offset = update_id + 1
                try:
                    self.handle(update)
                except Exception as error:
                    print(f"telegram update failed: {redact(str(error), self._config)}")

    def _on_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("type") != "private":
            return
        chat_id = chat.get("id")
        text = message.get("text")
        if not _is_chat_id(chat_id) or not isinstance(text, str):
            return
        command = _command(text)
        if command is None:
            return
        name, argument = command
        if name == "/start":
            self._subscribe(chat_id, argument)
        elif name == "/stop":
            self._unsubscribe(chat_id)

    def _subscribe(self, chat_id: int, argument: str) -> None:
        if not compare_digest(argument, self._config.telegram_subscribe_secret):
            self._reply(chat_id, "Subscription refused.")
            return
        try:
            added = self._subscribers.add(chat_id)
        except OSError as error:
            detail = redact(str(error), self._config)
            print(f"telegram subscribe failed for chat {chat_id}: {detail}")
            self._reply(chat_id, "Subscription failed.")
            return
        if added:
            print(f"telegram chat {chat_id} subscribed")
            self._reply(chat_id, "Subscribed. Status messages arrive after each pricing cycle.")
            with self._status_lock:
                status = self._last
            if status is not None:
                self._send_status(chat_id, status)
        else:
            self._reply(chat_id, "Already subscribed.")

    def _unsubscribe(self, chat_id: int) -> None:
        if self._subscribers.remove(chat_id):
            print(f"telegram chat {chat_id} unsubscribed")
            self._reply(chat_id, "Unsubscribed.")
        else:
            self._reply(chat_id, "Not subscribed.")

    def _on_callback(self, query: dict[str, Any]) -> None:
        query_id = query.get("id")
        message = query.get("message")
        if not isinstance(query_id, str):
            return
        if not isinstance(message, dict):
            self._answer(query_id, "Not allowed.", alert=True)
            return
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        user = query.get("from")
        user_id = user.get("id") if isinstance(user, dict) else None
        if not isinstance(chat, dict) or chat.get("type") != "private":
            self._answer(query_id, "Not allowed.", alert=True)
            return
        if not _is_chat_id(chat_id):
            self._answer(query_id, "Not allowed.", alert=True)
            return
        if user_id != chat_id or not self._subscribers.contains(chat_id):
            self._answer(query_id, "Not allowed.", alert=True)
            return
        raw = query.get("data")
        cents = parse_cents(raw) if isinstance(raw, str) else None
        if cents is None:
            self._answer(query_id, "Not allowed.", alert=True)
            return
        price = cents / 100
        if not _in_bounds(price, self._config):
            self._answer(query_id, "Price is outside configured bounds.", alert=True)
            return
        self._answer(query_id, "Updating listing…", alert=False)
        try:
            self._vast.update_listing(self._config, price)
        except Exception as error:
            print(f"telegram listing update failed: {redact(str(error), self._config)}")
            self._reply(chat_id, "Listing update failed.")
            return
        self._mark_applied(message, chat_id, price)

    def _mark_applied(self, message: dict[str, Any], chat_id: int, price: float) -> None:
        original = message.get("text")
        message_id = message.get("message_id")
        if (
            not isinstance(original, str)
            or original.endswith("Applied.")
            or not isinstance(message_id, int)
            or isinstance(message_id, bool)
        ):
            return
        try:
            self._api.call(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=f"{original}\nApplied.",
                reply_markup=keyboard(price),
            )
        except TelegramError as error:
            print(f"telegram edit failed for chat {chat_id}: {redact(str(error), self._config)}")

    def _send_status(self, chat_id: int, status: CycleStatus) -> None:
        try:
            self._api.call(
                "sendMessage",
                chat_id=chat_id,
                text=status_text(status),
                reply_markup=keyboard(status.suggested),
            )
        except TelegramError as error:
            if error.code == 403:
                self._subscribers.remove(chat_id)
                print(f"telegram chat {chat_id} blocked the bot; unsubscribed")
                return
            print(f"telegram send failed for chat {chat_id}: {redact(str(error), self._config)}")

    def _reply(self, chat_id: int, text: str) -> None:
        try:
            self._api.call("sendMessage", chat_id=chat_id, text=text)
        except TelegramError as error:
            print(f"telegram send failed for chat {chat_id}: {redact(str(error), self._config)}")

    def _answer(self, query_id: str, text: str, *, alert: bool) -> None:
        try:
            self._api.call(
                "answerCallbackQuery",
                callback_query_id=query_id,
                text=text,
                show_alert=alert,
            )
        except TelegramError as error:
            print(f"telegram callback answer failed: {redact(str(error), self._config)}")


def _is_chat_id(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _command(text: str) -> tuple[str, str] | None:
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    name, _, _bot = head.partition("@")
    return name.lower(), rest.strip()


def _in_bounds(price: float, config: Config) -> bool:
    return config.min_price <= price <= config.max_price and price >= config.min_bid_price
