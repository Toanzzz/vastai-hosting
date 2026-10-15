import io
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from vastai_hosting.buttons import BUTTON_TTL_SECONDS, Dismiss, SetGpu, SetRates, parse_action
from vastai_hosting.config import Config
from vastai_hosting.rates import PeerRates, Rates
from vastai_hosting.status import (
    CycleStatus,
    PriceChoice,
    status_text,
    suggestion_choices,
)
from vastai_hosting.subscribers import Subscribers
from vastai_hosting.telegram import PriceBot
from vastai_hosting.telegram_api import TelegramApi, TelegramError

TOKEN = "123456:ABC_def"
SECRET = "join-code"
NOW = int(time.time())
LISTED = Rates(0.15, 2.0, 2.0)
OWN: dict[str, Any] = {
    "listed_gpu_cost": 0.55,
    "listed_storage_cost": 0.15,
    "listed_inet_up_cost": 2 / 1024,
    "listed_inet_down_cost": 2 / 1024,
}


def config(tmp_path: Path) -> Config:
    return Config(
        machine_id=42,
        vast_api_key="vast-key",
        gemini_api_key="gemini-key",
        gemini_model="gemini-flash-latest",
        gemini_thinking_budget=1024,
        poll_seconds=1800,
        sample_seconds=600,
        min_peers=3,
        offer_limit=100,
        min_price=0.35,
        max_price=0.8,
        min_change=0.01,
        min_disk_price=0.05,
        max_disk_price=0.4,
        min_bandwidth_price=1.0,
        max_bandwidth_price=10.0,
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
        history_state_path=str(tmp_path / "occupancy.json"),
    )


def status() -> CycleStatus:
    estimates = ((0.55, 0.9), (0.72, 0.8), (0.8, 0.4))
    return CycleStatus(
        machine_id=42,
        at=NOW,
        current=0.55,
        suggested=0.72,
        occupancy=0.8,
        hourly_profit=0.576,
        estimates=estimates,
        choices=suggestion_choices(
            estimates,
            running_cost=0.0,
            current=0.55,
            suggested=0.72,
            occupancy=0.8,
            hourly_profit=0.576,
        ),
        occupied=True,
        market_usage=61,
        usage_30d=80,
        rented_gpus=1227,
        available_gpus=132,
        peers=42,
        peer_low=0.4,
        peer_high=0.9,
        host_share=0.75,
        median=0.5,
        rationale="Peers are cheaper",
        listing_settings_differ=True,
        listed_rates=LISTED,
        suggested_rates=Rates(0.15, 4.0, 4.0),
        peer_rates=PeerRates(0.15, 3.9, None),
        rates_rationale="Peers charge more for bandwidth",
    )


class Listing:
    def __init__(self, error: Exception | None = None) -> None:
        self.writes: list[tuple[float, Rates]] = []
        self.error = error

    def own_machine(self, config: Config) -> dict[str, Any]:
        return OWN

    def update_listing(self, config: Config, gpu: float, rates: Rates) -> None:
        if self.error is not None:
            raise self.error
        self.writes.append((gpu, rates))


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
    tmp_path: Path,
    listing: Listing | None = None,
    transport: Transport | None = None,
    on_check: Callable[[], None] | None = None,
) -> tuple[PriceBot, Listing, Transport, Subscribers]:
    listing = listing or Listing()
    transport = transport or Transport()
    subscribers = Subscribers(tmp_path / "subscribers.json")
    bot = PriceBot(
        config(tmp_path), listing, subscribers, TelegramApi(TOKEN, transport), on_check=on_check
    )
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


def calls(transport: Transport, name: str) -> list[dict[str, Any]]:
    return [params for method, params in transport.calls if method == name]


def test_suggestions_keep_the_best_price_and_two_alternatives() -> None:
    choices = suggestion_choices(
        ((0.45, 0.9), (0.5, 0.8), (0.6, 0.5), (0.7, 0.2)),
        running_cost=0.0,
        current=0.5,
        suggested=0.45,
        occupancy=0.9,
        hourly_profit=0.405,
    )
    assert [choice.price for choice in choices] == [0.45, 0.5, 0.6]
    assert choices[0].hourly_profit == pytest.approx(0.405)
    assert choices[1].hourly_profit == pytest.approx(0.4)
    tied = suggestion_choices(
        ((0.4, 1.0), (0.5, 0.8), (0.8, 0.5)),
        running_cost=0.0,
        current=0.5,
        suggested=0.5,
        occupancy=0.8,
        hourly_profit=0.4,
    )
    assert [choice.price for choice in tied] == [0.5, 0.4, 0.8]


def test_status_message_shows_prices_market_and_rates(tmp_path: Path) -> None:
    text = status_text(status(), config(tmp_path))
    assert "🖥 <b>Machine 42</b>" in text
    assert "<b>Listed</b> $0.55/GPU-h" in text
    assert "<b>Suggested</b> $0.72/GPU-h <i>(+$0.17)</i>" in text
    assert "Usage now" in text and "61%" in text
    assert "Usage 30d" in text and "80%" in text
    assert "1,227" in text and "132" in text
    assert "Median" in text and "$0.5000" in text
    assert "Host share" in text and "0.750" in text
    assert "Occupied" in text and "yes" in text
    assert "<pre>" in text and "$/mo" in text
    assert "$0.55" in text and "0.90" in text and "$356.40" in text
    assert "$414.72" in text and "$230.40" in text
    assert "←" in text
    assert "Occupancy <b>0.80</b>" in text
    assert "<b>$0.5760</b>/h · <b>$414.72</b>/mo" in text
    assert "Listing settings differ from config. Applying also refreshes them." in text
    assert "<i>Peers are cheaper</i>" in text
    assert "Storage &amp; bandwidth" in text
    assert "Upload/TB     $2.00    $4.00  $3.90" in text
    assert "Download/TB   $2.00    $4.00    n/a" in text
    assert "<i>Peers charge more for bandwidth</i>" in text


def test_callback_data_round_trips() -> None:
    assert parse_action(f"p:72:{NOW}") == SetGpu(72, NOW)
    assert parse_action("p:72") == SetGpu(72, None)
    assert parse_action(f"r:15:400:350:{NOW}") == SetRates(15, 400, 350, NOW)
    assert parse_action("d") == Dismiss()
    for bad in ("p:05:1", "p:7²:1", "p:72:", "r:1:2:1", "x:1:2", "p:1:2:3", "p:1234567"):
        assert parse_action(bad) is None


def test_wrong_start_does_not_subscribe(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    bot.handle(message("/start wrong"))
    assert subscribers.chats() == ()
    assert texts(transport) == ["Subscription refused."]


def test_start_subscribes_a_private_chat(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    bot.handle(message("/start@HostBot join-code"))
    assert subscribers.chats() == (7,)
    assert texts(transport) == [
        "Subscribed. Status arrives after each pricing cycle. "
        "Send /check to run one now, or /status for the latest."
    ]
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
    sent = calls(transport, "sendMessage")
    assert "$0.72/GPU-h" in str(sent[1]["text"])
    assert sent[1]["parse_mode"] == "HTML"
    rows = sent[1]["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in rows] == [
        f"p:72:{NOW}",
        f"p:80:{NOW}",
        f"r:15:400:400:{NOW}",
        "d",
    ]
    assert rows[0][0]["text"] == "Best $0.72 · $414.72/mo"
    assert rows[1][0]["text"] == "Set $0.80 · $230.40/mo"
    assert rows[2][0]["text"] == "Set disk/bandwidth rates"
    assert rows[3][0]["text"] == "Dismiss"


def test_status_command_resends_the_latest_report(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    bot.handle(message("/status"))
    assert texts(transport) == ["Not subscribed."]
    assert subscribers.add(7)
    bot.handle(message("/status"))
    assert texts(transport)[-1] == "No status yet. Send /check to run a pricing check."
    bot.publish(status())
    bot.handle(message("/status@HostBot"))
    sent = calls(transport, "sendMessage")
    assert len(sent) == 4
    assert sent[-1]["text"] == sent[-2]["text"]
    assert "reply_markup" in sent[-1]


@pytest.mark.parametrize(
    ("update", "reply", "subscribe"),
    [
        (callback(f"p:72:{NOW}"), "Not allowed.", False),
        (
            callback(f"p:72:{NOW}", chat_type="group", chat_id=-5, user_id=-5),
            "Not allowed.",
            False,
        ),
        (callback("p:05"), "Not allowed.", True),
        (callback(f"p:900:{NOW}"), "Price is outside configured bounds.", True),
        (callback(f"r:90:400:400:{NOW}"), "Price is outside configured bounds.", True),
        (callback(f"p:72:{NOW}", user_id=8), "Not allowed.", True),
        (
            callback(f"p:72:{NOW - BUTTON_TTL_SECONDS - 60}"),
            "This report expired. Send /check for a fresh one.",
            True,
        ),
        (callback("p:72"), "This report expired. Send /check for a fresh one.", True),
    ],
)
def test_rejected_callbacks_do_not_update_the_listing(
    tmp_path: Path, update: dict[str, Any], reply: str, subscribe: bool
) -> None:
    bot, listing, transport, subscribers = build(tmp_path)
    if subscribe:
        assert subscribers.add(7)
    bot.handle(update)
    assert listing.writes == []
    answers = calls(transport, "answerCallbackQuery")
    assert answers[-1]["text"] == reply
    assert answers[-1]["show_alert"] is True


def test_expired_button_is_removed(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(7)
    bot.handle(callback("p:72"))
    edited = calls(transport, "editMessageText")[-1]
    assert edited["text"] == "Machine 42\n⌛ <i>Expired.</i>"
    assert "reply_markup" not in edited


def test_gpu_tap_keeps_the_listed_rates(tmp_path: Path) -> None:
    order: list[str] = []

    class RecordingListing(Listing):
        def update_listing(self, config: Config, gpu: float, rates: Rates) -> None:
            order.append(f"update:{gpu}")
            super().update_listing(config, gpu, rates)

    bot, listing, transport, subscribers = build(tmp_path, RecordingListing(), Transport(order))
    assert subscribers.add(7)
    bot.handle(callback(f"p:72:{NOW}"))
    assert listing.writes == [(0.72, LISTED)]
    assert order == ["answerCallbackQuery", "update:0.72", "editMessageText"]
    edited = transport.calls[-1][1]
    assert edited["text"] == "Machine 42\n✅ <b>Applied $0.72/GPU-h.</b>"
    assert edited["parse_mode"] == "HTML"
    assert edited["reply_markup"]["inline_keyboard"][0][0]["text"] == "Set price to $0.72"


def test_rates_tap_keeps_the_listed_gpu_price(tmp_path: Path) -> None:
    bot, listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(7)
    update = callback(f"r:15:400:350:{NOW}")
    keyboard = {"inline_keyboard": [[{"text": "Dismiss", "callback_data": "d"}]]}
    update["callback_query"]["message"]["reply_markup"] = keyboard
    bot.handle(update)
    assert listing.writes == [(0.55, Rates(0.15, 4.0, 3.5))]
    edited = calls(transport, "editMessageText")[-1]
    assert edited["text"] == "Machine 42\n✅ <b>Applied disk/bandwidth rates.</b>"
    assert edited["reply_markup"] == keyboard


def test_dismiss_removes_the_buttons(tmp_path: Path) -> None:
    bot, listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(7)
    bot.publish(status())
    bot.handle(callback("d", text="Machine 42"))
    assert listing.writes == []
    assert calls(transport, "answerCallbackQuery")[-1]["text"] == "Dismissed."
    edited = calls(transport, "editMessageText")[-1]
    assert edited["text"] == "Machine 42\n✖️ <i>Dismissed.</i>"
    assert "reply_markup" not in edited
    bot.handle(callback("d", user_id=8))
    assert len(calls(transport, "editMessageText")) == 1


def test_second_tap_updates_again(tmp_path: Path) -> None:
    bot, listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(7)
    bot.handle(callback(f"p:72:{NOW}", text="Machine 42\n✅ Applied $0.72/GPU-h."))
    assert listing.writes == [(0.72, LISTED)]
    assert calls(transport, "editMessageText") == []


def test_failed_listing_update_is_reported(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path, Listing(RuntimeError("vast down")))
    assert subscribers.add(7)
    bot.handle(callback(f"p:72:{NOW}"))
    assert texts(transport) == ["Listing update failed."]
    assert calls(transport, "editMessageText") == []


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
    assert [params["chat_id"] for params in calls(transport, "sendMessage")] == [3, 9]


def test_rental_changes_are_announced(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(3)
    bot.notify_rental(True, 0.55, 30.0)
    bot.notify_rental(False, 0.55, 0.5)
    assert texts(transport) == [
        "🟢 Rental started on machine 42 at $0.55/GPU-h, after about 30 h idle.",
        "⚪ Rental ended on machine 42 after under an hour. Listed at $0.55/GPU-h.",
    ]


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


def test_unchanged_price_omits_only_that_button(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(7)
    bot.publish(replace(status(), current=0.72, suggested=0.72, suggested_rates=LISTED))
    sent = calls(transport, "sendMessage")
    rows = sent[0]["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in rows] == [f"p:55:{NOW}", f"p:80:{NOW}", "d"]
    assert [row[0]["text"].split()[0] for row in rows] == ["Set", "Set", "Dismiss"]
    assert "matches the listed price" in str(sent[0]["text"])
    assert "Suggested rates match the listed rates." in str(sent[0]["text"])


def test_out_of_bounds_suggestions_have_no_buttons(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path)
    assert subscribers.add(7)
    bot.publish(
        replace(
            status(),
            suggested=0.1,
            choices=(PriceChoice(0.1, None, None),),
            suggested_rates=Rates(0.9, 4.0, 4.0),
        )
    )
    sent = calls(transport, "sendMessage")
    assert "reply_markup" not in sent[0]
    assert "cannot be applied" in str(sent[0]["text"])
    assert "Suggested rates are outside the configured bounds." in str(sent[0]["text"])


def test_fallback_and_escaped_rationale_text(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    fallback = replace(
        status(),
        rationale=None,
        occupancy=None,
        hourly_profit=None,
        estimates=(),
        choices=(PriceChoice(0.48, None, None),),
        current=0.55,
        suggested=0.48,
        median=0.5,
        rates_rationale=None,
    )
    text = status_text(fallback, cfg)
    assert "Gemini unavailable" in text
    assert "$0.02 under the $0.5000 peer median." in text
    assert "Peer median rates, rounded to the cent." in text
    assert "Estimates" not in text
    rounded = status_text(replace(fallback, suggested=0.36, median=0.375, current=0.6), cfg)
    assert "Rounded to the nearest cent." in rounded
    escaped = status_text(replace(status(), rationale="a < b & c"), cfg)
    assert "<i>a &lt; b &amp; c</i>" in escaped


def test_applied_edit_keeps_the_formatted_status(tmp_path: Path) -> None:
    class Ids(Transport):
        def __call__(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            self.order.append(method)
            self.calls.append((method, params))
            if method == "sendMessage":
                return {"message_id": 4}
            return {"message_id": 1}

    bot, _listing, transport, subscribers = build(tmp_path, transport=Ids())
    assert subscribers.add(7)
    bot.publish(status())
    bot.handle(callback(f"p:72:{NOW}", text="plain from telegram"))
    edited = transport.calls[-1][1]
    assert "plain from telegram" not in str(edited["text"])
    assert "<b>Machine 42</b>" in str(edited["text"])
    assert str(edited["text"]).endswith("\n✅ <b>Applied $0.72/GPU-h.</b>")


def test_check_runs_the_cycle_for_a_subscriber(tmp_path: Path) -> None:
    checks: list[str] = []
    bot, _listing, transport, subscribers = build(tmp_path, on_check=lambda: checks.append("go"))
    assert subscribers.add(7)
    bot.handle(message("/check@HostBot"))
    assert checks == ["go"]
    assert texts(transport) == ["Checking the market…"]
    bot.handle(message("/check"))
    assert checks == ["go"]
    assert texts(transport)[-1] == "A pricing check is already running."
    bot.publish(status())
    bot.handle(message("/check"))
    assert checks == ["go", "go"]


def test_check_requires_a_subscription(tmp_path: Path) -> None:
    checks: list[str] = []
    bot, _listing, transport, _subscribers = build(tmp_path, on_check=lambda: checks.append("go"))
    bot.handle(message("/check"))
    assert checks == []
    assert texts(transport) == ["Not subscribed."]
    bot.handle(message("/check", chat_id=-3, chat_type="group"))
    assert transport.calls == [("sendMessage", transport.calls[0][1])]


def test_failed_check_is_reported_only_for_its_cycle(tmp_path: Path) -> None:
    bot, _listing, transport, subscribers = build(tmp_path, on_check=lambda: None)
    assert subscribers.add(7)
    started = bot.pending_epoch()
    bot.handle(message("/check"))
    bot.notify_check_failed(started)
    assert "Pricing check failed." not in texts(transport)
    bot.notify_check_failed(bot.pending_epoch())
    assert texts(transport)[-1] == "Pricing check failed."
    bot.notify_check_failed(bot.pending_epoch())
    assert texts(transport).count("Pricing check failed.") == 1
