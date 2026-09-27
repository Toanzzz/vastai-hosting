from dataclasses import dataclass
from typing import Any

from .config import Config

# 30-day month. Monthly profit is expected hourly profit times this.
HOURS_PER_MONTH = 24 * 30
EXTRA_SUGGESTIONS = 2


@dataclass(frozen=True, slots=True)
class PriceChoice:
    price: float
    occupancy: float | None
    hourly_profit: float | None


@dataclass(frozen=True, slots=True)
class CycleStatus:
    machine_id: int
    current: float
    suggested: float
    occupancy: float | None
    hourly_profit: float | None
    estimates: tuple[tuple[float, float], ...]
    choices: tuple[PriceChoice, ...]
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
        lines = [
            f"💰 <b>Listed</b> {listed}/GPU-h",
            "✅ <i>Suggested price matches the listed price.</i>",
        ]
    else:
        delta = status.suggested - status.current
        sign = "+" if delta > 0 else "-"
        change = f"{sign}{_money(abs(delta))}"
        lines = [
            f"💰 <b>Listed</b> {listed}/GPU-h",
            f"✨ <b>Suggested</b> {_money(status.suggested)}/GPU-h <i>({escape_html(change)})</i>",
        ]
    if status.hourly_profit is not None and status.occupancy is not None:
        monthly = status.hourly_profit * HOURS_PER_MONTH
        lines.append(
            f"💵 Occupancy <b>{status.occupancy:.2f}</b> · "
            f"<b>{_money(status.hourly_profit, 4)}</b>/h · <b>{_money(monthly, 2)}</b>/mo"
        )
    return "\n".join(lines)


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


def _suggestions_block(status: CycleStatus) -> str:
    chosen = price_cents(status.suggested)
    rows: list[tuple[str, str, str, str, str]] = [("Price", "Occ", "$/h", "$/mo", "")]
    for choice in status.choices:
        mark = "  ←" if price_cents(choice.price) == chosen else ""
        if choice.occupancy is None or choice.hourly_profit is None:
            occupancy, hourly, monthly = "n/a", "n/a", "n/a"
        else:
            occupancy = f"{choice.occupancy:.2f}"
            hourly = _money(choice.hourly_profit, 4)
            monthly = _money(choice.hourly_profit * HOURS_PER_MONTH, 2)
        rows.append((f"${choice.price:.2f}", occupancy, hourly, monthly, mark))
    widths = [max(len(row[index]) for row in rows) for index in range(4)]
    lines: list[str] = []
    for row in rows:
        cells = (
            row[0].ljust(widths[0]),
            row[1].rjust(widths[1]),
            row[2].rjust(widths[2]),
            row[3].rjust(widths[3]),
        )
        lines.append("  ".join(cells) + row[4])
    table = f"<pre>{escape_html('\n'.join(lines))}</pre>"
    return f"💡 <b>Suggestions</b>\n{table}\n<i>$/mo is 30 days at that occupancy.</i>"


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
        if len(status.choices) > 1:
            parts.append(_suggestions_block(status))
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


def _in_bounds(price: float, config: Config) -> bool:
    return config.min_price <= price <= config.max_price and price >= config.min_bid_price


def _choice_button(choice: PriceChoice, *, best: bool) -> dict[str, str]:
    cents = price_cents(choice.price)
    label = f"${cents / 100:.2f}"
    if choice.hourly_profit is None:
        text = f"Set price to {label}"
    else:
        monthly = _money(choice.hourly_profit * HOURS_PER_MONTH, 2)
        prefix = "Best" if best else "Set"
        text = f"{prefix} {label} · {monthly}/mo"
    return {"text": text, "callback_data": f"p:{cents}"}


def apply_keyboard(status: CycleStatus, config: Config) -> dict[str, Any] | None:
    rows = [
        [_choice_button(choice, best=index == 0)]
        for index, choice in enumerate(status.choices)
        if price_cents(choice.price) != price_cents(status.current)
        and _in_bounds(choice.price, config)
    ]
    if not rows:
        return None
    return {"inline_keyboard": rows}


def suggestion_choices(
    estimates: tuple[tuple[float, float], ...],
    *,
    running_cost: float,
    current: float,
    suggested: float,
    occupancy: float | None,
    hourly_profit: float | None,
) -> tuple[PriceChoice, ...]:
    """Best price, then up to two more by expected hourly profit."""
    if hourly_profit is None or not estimates:
        return (PriceChoice(suggested, occupancy, hourly_profit),)

    def earned(price: float, rented: float) -> float:
        return rented * (price - running_cost)

    def sort_key(item: tuple[float, float]) -> tuple[float, float]:
        price, rented = item
        return (round(earned(price, rented), 6), -abs(price - current))

    ranked = sorted(estimates, key=sort_key, reverse=True)
    suggested_cents = price_cents(suggested)
    primary = next((item for item in ranked if price_cents(item[0]) == suggested_cents), None)
    picked: list[tuple[float, float]] = []
    if primary is None:
        picked.append((suggested, 0.0 if occupancy is None else occupancy))
    else:
        picked.append(primary)
    for item in ranked:
        if len(picked) >= 1 + EXTRA_SUGGESTIONS:
            break
        if any(price_cents(item[0]) == price_cents(price) for price, _rented in picked):
            continue
        picked.append(item)
    choices: list[PriceChoice] = []
    for price, rented in picked:
        if price_cents(price) == suggested_cents:
            choices.append(PriceChoice(price, rented, hourly_profit))
        else:
            choices.append(PriceChoice(price, rented, earned(price, rented)))
    return tuple(choices)
