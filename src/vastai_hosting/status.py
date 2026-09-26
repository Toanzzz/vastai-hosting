from dataclasses import dataclass
from typing import Any

from .config import Config


@dataclass(frozen=True, slots=True)
class CycleStatus:
    machine_id: int
    current: float
    suggested: float
    occupancy: float | None
    hourly_profit: float | None
    estimates: tuple[tuple[float, float], ...]
    occupied: bool | None
    market_usage: float
    usage_30d: float
    rented_gpus: float
    available_gpus: float
    peers: int
    peer_low: float
    peer_high: float
    host_share: float
    median: float
    rationale: str | None
    listing_settings_differ: bool


def price_cents(price: float) -> int:
    return int(round(price * 100))


def parse_cents(data: str) -> int | None:
    raw = data.removeprefix("p:")
    if raw == data or not raw.isdigit() or len(raw) > 6 or (len(raw) > 1 and raw.startswith("0")):
        return None
    return int(raw)


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _money(value: float, places: int = 2) -> str:
    text = f"{abs(value):.{places}f}"
    return f"-${text}" if value < 0 else f"${text}"


def _count(value: float) -> str:
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:g}"


def _occupied(state: bool | None) -> str:
    if state is True:
        return "yes"
    if state is False:
        return "no"
    return "unknown"


def _table(rows: list[tuple[str, str]]) -> str:
    width = max(len(label) for label, _value in rows)
    lines = [f"{label:<{width}}  {value}" for label, value in rows]
    return f"<pre>{escape_html('\n'.join(lines))}</pre>"


def _price_block(status: CycleStatus) -> str:
    listed = _money(status.current)
    if price_cents(status.current) == price_cents(status.suggested):
        return (
            f"💰 <b>Listed</b> {listed}/GPU-h\n✅ <i>Suggested price matches the listed price.</i>"
        )
    delta = status.suggested - status.current
    sign = "+" if delta > 0 else "-"
    change = f"{sign}{_money(abs(delta))}"
    return (
        f"💰 <b>Listed</b> {listed}/GPU-h\n"
        f"✨ <b>Suggested</b> {_money(status.suggested)}/GPU-h <i>({escape_html(change)})</i>"
    )


def _market_block(status: CycleStatus) -> str:
    table = _table(
        [
            ("Usage now", f"{_count(status.market_usage)}%"),
            ("Usage 30d", f"{_count(status.usage_30d)}%"),
            ("Rented", _count(status.rented_gpus)),
            ("Available", _count(status.available_gpus)),
            ("Peers", str(status.peers)),
            ("Lowest", _money(status.peer_low, 4)),
            ("Median", _money(status.median, 4)),
            ("Highest", _money(status.peer_high, 4)),
            ("Host share", f"{status.host_share:.3f}"),
            ("Occupied", _occupied(status.occupied)),
        ]
    )
    return f"📊 <b>Market</b>\n{table}"


def _estimates_block(status: CycleStatus) -> str:
    chosen = price_cents(status.suggested)
    lines = [f"{'Price':<8}{'Occupancy':>9}"]
    for price, occupancy in status.estimates:
        mark = "  ←" if price_cents(price) == chosen else ""
        lines.append(f"${price:<7.2f}{occupancy:>9.2f}{mark}")
    return f"📈 <b>Estimates</b>\n<pre>{escape_html('\n'.join(lines))}</pre>"


def _fallback_block(status: CycleStatus) -> str:
    gap = abs((status.median - 0.02) - status.suggested)
    rounded = " Rounded to the nearest cent." if gap >= 0.001 else ""
    return (
        "⚠️ <b>Gemini unavailable.</b> "
        f"<i>No rationale. Suggested {_money(status.suggested)}, "
        f"$0.02 under the {_money(status.median, 4)} peer median.{rounded}</i>"
    )


def status_text(status: CycleStatus, *, offer_button: bool) -> str:
    parts = [
        f"🖥 <b>Machine {status.machine_id}</b>",
        _price_block(status),
        _market_block(status),
    ]
    if status.rationale is None:
        parts.append(_fallback_block(status))
    else:
        if status.estimates:
            parts.append(_estimates_block(status))
        if status.occupancy is not None and status.hourly_profit is not None:
            parts.append(
                f"💵 Expected occupancy <b>{status.occupancy:.2f}</b> · "
                f"profit <b>{_money(status.hourly_profit, 4)}</b>/h"
            )
        parts.append(f"💬 <i>{escape_html(status.rationale)}</i>")
    if status.listing_settings_differ:
        refresh = " Applying also refreshes them." if offer_button else ""
        parts.append(f"⚙️ <i>Listing settings differ from config.{refresh}</i>")
    if not offer_button and price_cents(status.suggested) != price_cents(status.current):
        parts.append("🚫 <i>Outside the configured price bounds, so it cannot be applied.</i>")
    return "\n\n".join(parts)


def keyboard(price: float) -> dict[str, Any]:
    cents = price_cents(price)
    button = {"text": f"Set price to ${cents / 100:.2f}", "callback_data": f"p:{cents}"}
    return {"inline_keyboard": [[button]]}


def apply_keyboard(status: CycleStatus, config: Config) -> dict[str, Any] | None:
    price = status.suggested
    if price_cents(price) == price_cents(status.current):
        return None
    if not config.min_price <= price <= config.max_price or price < config.min_bid_price:
        return None
    return keyboard(price)
