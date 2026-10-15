import threading
import time
from collections.abc import Callable
from hmac import compare_digest
from typing import Any, Protocol, TypeGuard

from .buttons import (
    Dismiss,
    SetGpu,
    SetRates,
    applied_keyboard,
    applied_note,
    expired,
    in_bounds,
    parse_action,
)
from .checks import CheckGate
from .config import Config, redact
from .messenger import Messenger
from .pricing import required_number
from .rates import Rates, listed_rates
from .status import CycleStatus, rental_text
from .subscribers import Subscribers
from .telegram_api import TelegramApi, TelegramError

# answerCallbackQuery must return before the client gives up on the button.
_POLL_SECONDS = 50
_COMMANDS = [
    {"command": "check", "description": "Run a pricing check now"},
    {"command": "status", "description": "Resend the latest report"},
    {"command": "stop", "description": "Unsubscribe"},
]

Row = dict[str, Any]


class _Listing(Protocol):
    def own_machine(self, config: Config) -> Row: ...

    def update_listing(self, config: Config, gpu: float, rates: Rates) -> None: ...


class PriceBot:
    def __init__(
        self,
        config: Config,
        vast: _Listing,
        subscribers: Subscribers,
        api: TelegramApi | None = None,
        on_check: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._vast = vast
        self._subscribers = subscribers
        self._api = api or TelegramApi(config.telegram_bot_token)
        self._out = Messenger(config, self._api, subscribers)
        self._on_check = on_check
        self._last: CycleStatus | None = None
        self._status_lock = threading.Lock()
        self._checks = CheckGate()

    def start(self) -> None:
        username = self._username()
        label = f"@{username}" if username else "bot"
        try:
            self._api.call("setMyCommands", commands=_COMMANDS)
        except TelegramError as error:
            print(f"telegram setMyCommands failed: {redact(str(error), self._config)}")
        print(f"telegram {label} polling")
        threading.Thread(target=self._poll, name="telegram", daemon=True).start()

    def pending_epoch(self) -> int:
        return self._checks.epoch()

    def publish(self, status: CycleStatus, *, epoch: int | None = None) -> None:
        with self._status_lock:
            self._last = status
        self._checks.take(epoch)
        for chat_id in self._subscribers.chats():
            self._out.send_status(chat_id, status)

    def notify_check_failed(self, epoch: int) -> None:
        for chat_id in self._checks.take(epoch):
            self._out.reply(chat_id, "Pricing check failed.")

    def notify_rental(self, started: bool, price: float, hours: float) -> None:
        text = rental_text(self._config.machine_id, started, price, hours)
        for chat_id in self._subscribers.chats():
            self._out.reply(chat_id, text)

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
        elif name == "/check":
            self._request_check(chat_id)
        elif name == "/status":
            self._resend(chat_id)

    def _subscribe(self, chat_id: int, argument: str) -> None:
        if not compare_digest(argument, self._config.telegram_subscribe_secret):
            self._out.reply(chat_id, "Subscription refused.")
            return
        try:
            added = self._subscribers.add(chat_id)
        except OSError as error:
            detail = redact(str(error), self._config)
            print(f"telegram subscribe failed for chat {chat_id}: {detail}")
            self._out.reply(chat_id, "Subscription failed.")
            return
        if not added:
            self._out.reply(chat_id, "Already subscribed.")
            return
        print(f"telegram chat {chat_id} subscribed")
        self._out.reply(
            chat_id,
            "Subscribed. Status arrives after each pricing cycle. "
            "Send /check to run one now, or /status for the latest.",
        )
        with self._status_lock:
            status = self._last
        if status is not None:
            self._out.send_status(chat_id, status)

    def _unsubscribe(self, chat_id: int) -> None:
        if self._subscribers.remove(chat_id):
            print(f"telegram chat {chat_id} unsubscribed")
            self._out.reply(chat_id, "Unsubscribed.")
        else:
            self._out.reply(chat_id, "Not subscribed.")

    def _request_check(self, chat_id: int) -> None:
        if not self._subscribers.contains(chat_id):
            self._out.reply(chat_id, "Not subscribed.")
            return
        if self._on_check is None:
            self._out.reply(chat_id, "Manual check is unavailable.")
            return
        if not self._checks.request(chat_id):
            self._out.reply(chat_id, "A pricing check is already running.")
            return
        self._out.reply(chat_id, "Checking the market…")
        self._on_check()

    def _resend(self, chat_id: int) -> None:
        if not self._subscribers.contains(chat_id):
            self._out.reply(chat_id, "Not subscribed.")
            return
        with self._status_lock:
            status = self._last
        if status is None:
            self._out.reply(chat_id, "No status yet. Send /check to run a pricing check.")
            return
        self._out.send_status(chat_id, status)

    def _on_callback(self, query: dict[str, Any]) -> None:
        query_id = query.get("id")
        message = query.get("message")
        if not isinstance(query_id, str):
            return
        if not isinstance(message, dict):
            self._out.answer(query_id, "Not allowed.", alert=True)
            return
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        user = query.get("from")
        user_id = user.get("id") if isinstance(user, dict) else None
        raw = query.get("data")
        action = parse_action(raw) if isinstance(raw, str) else None
        if (
            not isinstance(chat, dict)
            or chat.get("type") != "private"
            or not _is_chat_id(chat_id)
            or user_id != chat_id
            or not self._subscribers.contains(chat_id)
            or action is None
            or not isinstance(raw, str)
        ):
            self._out.answer(query_id, "Not allowed.", alert=True)
            return
        if isinstance(action, Dismiss):
            self._out.answer(query_id, "Dismissed.", alert=False)
            self._out.edit(message, chat_id, "✖️ <i>Dismissed.</i>", None)
            return
        if expired(action, time.time()):
            reply = "This report expired. Send /check for a fresh one."
            self._out.answer(query_id, reply, alert=True)
            self._out.edit(message, chat_id, "⌛ <i>Expired.</i>", None)
            return
        if not in_bounds(action, self._config):
            self._out.answer(query_id, "Price is outside configured bounds.", alert=True)
            return
        self._out.answer(query_id, "Updating listing…", alert=False)
        try:
            self._apply(action)
        except Exception as error:
            print(f"telegram listing update failed: {redact(str(error), self._config)}")
            self._out.reply(chat_id, "Listing update failed.")
            return
        markup = message.get("reply_markup")
        keyboard = markup if isinstance(markup, dict) else applied_keyboard(action, raw)
        self._out.edit(message, chat_id, f"✅ <b>{applied_note(action)}</b>", keyboard)

    def _apply(self, action: SetGpu | SetRates) -> None:
        """Write the tapped value and keep the rest of the live listing."""
        own = self._vast.own_machine(self._config)
        gpu = required_number(own, "listed_gpu_cost")
        rates = listed_rates(own)
        if isinstance(action, SetGpu):
            gpu = action.price
        else:
            rates = action.rates
        self._vast.update_listing(self._config, gpu, rates)


def _is_chat_id(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _command(text: str) -> tuple[str, str] | None:
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    name, _, _bot = head.partition("@")
    return name.lower(), rest.strip()
