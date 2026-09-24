import io
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from vastai_hosting.config import Config
from vastai_hosting.status import CycleStatus, parse_cents, status_text
from vastai_hosting.subscribers import Subscribers
from vastai_hosting.telegram import PriceBot
from vastai_hosting.telegram_api import TelegramApi, TelegramError

TOKEN = "123456:ABC_def"
SECRET = "join-code"


def config(tmp_path: Path) -> Config:
    return Config(
        machine_id=42,
        vast_api_key="vast-key",
        gemini_api_key="gemini-key",
        gemini_model="gemini-flash-latest",
        gemini_thinking_budget=1024,
        poll_seconds=1800,
        min_peers=3,
        offer_limit=100,
        min_price=0.35,
        max_price=0.8,
        min_change=0.01,
        disk_price=0.15,
        upload_price=0.01,
        download_price=0.01,
        min_bid_price=0.3,
        discount_rate=0.1,
        min_chunk=1,
        volume_size_gb=200,
        volume_price=0.15,
        duration_days=7,
        running_cost=0.0,
        telegram_bot_token=TOKEN,
        telegram_subscribe_secret=SECRET,
        telegram_state_path=str(tmp_path / "subscribers.json"),
    )


def status() -> CycleStatus:
    return CycleStatus(
        machine_id=42,
        current=0.55,
        suggested=0.72,
        occupancy=0.8,
        hourly_profit=0.576,
        estimates=((0.55, 0.9), (0.72, 0.8)),
        occupied=True,
        market_usage=61,
        peers=42,
        host_share=0.75,
        rationale="Peers are cheaper",
        listing_settings_differ=True,
    )


class Listing:
    def __init__(self, error: Exception | None = None) -> None:
        self.prices: list[float] = []
        self.error = error

    def update_listing(self, config: Config, target: float) -> None:
        if self.error is not None:
            raise self.error
        self.prices.append(target)


class Transport:
    def __init__(self, order: list[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.order = order if order is not None else []
        self.error: TelegramError | None = None

    def __call__(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.order.append(method)
        self.calls.append((method, params))
        if self.error is not None and method == "sendMessage":
            raise self.error
        if method == "getMe":
            return {"username": "hostprice"}
        return {"message_id": 1}


def build(
    tmp_path: Path, listing: Listing | None = None, transport: Transport | None = None
) -> tuple[PriceBot, Listing, Transport, Subscribers]:
    listing = listing or Listing()
    transport = transport or Transport()
    subscribers = Subscribers(tmp_path / "subscribers.json")
    bot = PriceBot(config(tmp_path), listing, subscribers, TelegramApi(TOKEN, transport))
    return bot, listing, transport, subscribers


def message(text: str, chat_id: int = 7, chat_type: str = "private") -> dict[str, Any]:
    return {"message": {"text": text, "chat": {"id": chat_id, "type": chat_type}}}


def callback(
    data: str,
    chat_id: int = 7,
    chat_type: str = "private",
    user_id: int = 7,
    text: str = "Machine 42",
) -> dict[str, Any]:
    return {
        "callback_query": {
            "id": "q1",
            "from": {"id": user_id},
            "data": data,
            "message": {
                "message_id": 4,
                "text": text,
                "chat": {"id": chat_id, "type": chat_type},
            },
        }
    }


def texts(transport: Transport) -> list[str]:
    return [
        str(params.get("text")) for method, params in transport.calls if method == "sendMessage"
    ]


def test_status_message_includes_the_price_button() -> None:
    text = status_text(status())
    assert "Machine 42" in text
    assert "Listed $0.55/GPU-h, suggested $0.72/GPU-h" in text
    assert "Occupied: yes" in text
    assert "market usage 61%" in text
    assert "peers 42" in text
    assert "host share 0.750" in text
    assert "Expected occupancy 0.80" in text
    assert "profit $0.5760/h" in text
    assert "Estimates: 0.55:0.90 0.72:0.80" in text
    assert "Listing settings differ from config" in text
    assert text.endswith("Peers are cheaper")
    assert parse_cents("p:72") == 72
    assert parse_cents("p:05") is None


def test_wrong_start_does_not_subscribe(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    bot.handle(message("/start wrong"))
    assert subscribers.chats() == ()
    assert texts(transport) == ["Subscription refused."]


def test_start_subscribes_a_private_chat(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    bot.handle(message("/start@HostBot join-code"))
    assert subscribers.chats() == (7,)
    assert texts(transport) == ["Subscribed. Status messages arrive after each pricing cycle."]
    bot.handle(message("/start join-code"))
    assert texts(transport)[-1] == "Already subscribed."


def test_group_start_is_ignored(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    bot.handle(message("/start join-code", chat_id=-10, chat_type="group"))
    assert subscribers.chats() == ()
    assert transport.calls == []


def test_stop_unsubscribes(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(7)
    bot.handle(message("/stop"))
    assert subscribers.chats() == ()
    assert texts(transport) == ["Unsubscribed."]
    bot.handle(message("/stop"))
    assert texts(transport)[-1] == "Not subscribed."


def test_new_subscriber_receives_the_latest_status(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    bot.publish(status())
    assert subscribers.chats() == ()
    bot.handle(message("/start join-code"))
    sent = [params for method, params in transport.calls if method == "sendMessage"]
    assert sent[0]["text"] == "Subscribed. Status messages arrive after each pricing cycle."
    assert "suggested $0.72/GPU-h" in str(sent[1]["text"])
    markup = sent[1]["reply_markup"]
    assert isinstance(markup, dict)
    assert markup["inline_keyboard"][0][0]["callback_data"] == "p:72"


@pytest.mark.parametrize(
    ("update", "reply", "subscribe"),
    [
        (callback("p:72"), "Not allowed.", False),
        (callback("p:72", chat_type="group", chat_id=-5, user_id=-5), "Not allowed.", False),
        (callback("p:05"), "Not allowed.", True),
        (callback("p:900"), "Price is outside configured bounds.", True),
        (callback("p:72", user_id=8), "Not allowed.", True),
    ],
)
def test_rejected_callbacks_do_not_update_the_listing(
    tmp_path: Path, update: dict[str, Any], reply: str, subscribe: bool
) -> None:
    bot, listing, transport, subscribers = build(tmp_path)
    if subscribe:
        assert subscribers.add(7)
    bot.handle(update)
    assert listing.prices == []
    answers = [params for method, params in transport.calls if method == "answerCallbackQuery"]
    assert answers[-1]["text"] == reply
    assert answers[-1]["show_alert"] is True


def test_allowed_callback_updates_the_listing(tmp_path: Path) -> None:
    order: list[str] = []

    class RecordingListing(Listing):
        def update_listing(self, config: Config, target: float) -> None:
            order.append(f"update:{target}")
            self.prices.append(target)

    bot, listing, transport, subscribers = build(tmp_path, RecordingListing(), Transport(order))
    assert subscribers.add(7)
    bot.handle(callback("p:72"))
    assert listing.prices == [0.72]
    assert order == ["answerCallbackQuery", "update:0.72", "editMessageText"]
    edited = transport.calls[-1][1]
    assert edited["text"] == "Machine 42\nApplied."
    assert edited["reply_markup"]["inline_keyboard"][0][0]["text"] == "Set price to $0.72"


def test_second_tap_updates_again(tmp_path: Path) -> None:
    bot, listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(7)
    bot.handle(callback("p:72", text="Machine 42\nApplied."))
    assert listing.prices == [0.72]
    assert [method for method, _params in transport.calls if method == "editMessageText"] == []


def test_failed_listing_update_is_reported(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path, Listing(RuntimeError("vast down")))
    assert subscribers.add(7)
    bot.handle(callback("p:72"))
    assert texts(transport) == ["Listing update failed."]
    assert [method for method, _params in transport.calls if method == "editMessageText"] == []


def test_blocked_chat_is_removed(tmp_path: Path) -> None:
    transport = Transport()
    transport.error = TelegramError("Forbidden", 403)
    bot, _listing, _transport, subscribers = build(tmp_path, transport=transport)
    assert subscribers.add(9)
    bot.publish(status())
    assert subscribers.chats() == ()


def test_publish_messages_every_subscriber(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(3)
    assert subscribers.add(9)
    bot.publish(status())
    chats = [params["chat_id"] for method, params in transport.calls if method == "sendMessage"]
    assert chats == [3, 9]


def test_http_error_hides_the_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError(
            url=f"https://api.telegram.org/bot{TOKEN}/getMe",
            code=401,
            msg="Unauthorized",
            hdrs=Message(),
            fp=io.BytesIO(b'{"ok":false,"error_code":401,"description":"Unauthorized"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    with pytest.raises(TelegramError, match="Unauthorized") as caught:
        TelegramApi(TOKEN).call("getMe")
    assert TOKEN not in str(caught.value)
    assert caught.value.code == 401


def test_network_error_hides_the_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError(f"failed calling bot{TOKEN}")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    with pytest.raises(TelegramError, match="failed calling") as caught:
        TelegramApi(TOKEN).call("sendMessage", chat_id=1, text="hi")
    message = str(caught.value)
    assert TOKEN not in message
    assert "[redacted]" in message
