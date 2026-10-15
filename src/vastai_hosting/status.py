from dataclasses import dataclass

from .config import Config
from .pricing import gpu_in_bounds
from .rates import PeerRates, Rates, rates_in_bounds, same_rates

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
    at: float
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
    listed_rates: Rates
    suggested_rates: Rates
    peer_rates: PeerRates
    rates_rationale: str | None


def price_cents(price: float) -> int:
    return int(round(price * 100))


def offered_choices(status: CycleStatus, config: Config) -> list[PriceChoice]:
    return [
        choice
        for choice in status.choices
        if price_cents(choice.price) != price_cents(status.current)
        and gpu_in_bounds(choice.price, config)
    ]


def rates_offered(status: CycleStatus, config: Config) -> bool:
    return not same_rates(status.suggested_rates, status.listed_rates) and rates_in_bounds(
        status.suggested_rates, config
    )


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def money(value: float, places: int = 2) -> str:
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


def _pre(rows: list[tuple[str, ...]], left: int = 1) -> str:
    """Monospace table; the first `left` columns are left-aligned, the rest right-aligned."""
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines = [
        "  ".join(
            cell.ljust(widths[index]) if index < left else cell.rjust(widths[index])
            for index, cell in enumerate(row)
        ).rstrip()
        for row in rows
    ]
    return f"<pre>{escape_html('\n'.join(lines))}</pre>"


def _price_block(status: CycleStatus) -> str:
    listed = money(status.current)
    if price_cents(status.current) == price_cents(status.suggested):
        lines = [
            f"💰 <b>Listed</b> {listed}/GPU-h",
            "✅ <i>Suggested price matches the listed price.</i>",
        ]
    else:
        delta = status.suggested - status.current
        sign = "+" if delta > 0 else "-"
        change = f"{sign}{money(abs(delta))}"
        lines = [
            f"💰 <b>Listed</b> {listed}/GPU-h",
            f"✨ <b>Suggested</b> {money(status.suggested)}/GPU-h <i>({escape_html(change)})</i>",
        ]
    if status.hourly_profit is not None and status.occupancy is not None:
        monthly = status.hourly_profit * HOURS_PER_MONTH
        lines.append(
            f"💵 Occupancy <b>{status.occupancy:.2f}</b> · "
            f"<b>{money(status.hourly_profit, 4)}</b>/h · <b>{money(monthly, 2)}</b>/mo"
        )
    return "\n".join(lines)


def _market_block(status: CycleStatus) -> str:
    table = _pre(
        [
            ("Usage now", f"{_count(status.market_usage)}%"),
            ("Usage 30d", f"{_count(status.usage_30d)}%"),
            ("Rented", _count(status.rented_gpus)),
            ("Available", _count(status.available_gpus)),
            ("Peers", str(status.peers)),
            ("Lowest", money(status.peer_low, 4)),
            ("Median", money(status.median, 4)),
            ("Highest", money(status.peer_high, 4)),
            ("Host share", f"{status.host_share:.3f}"),
            ("Occupied", _occupied(status.occupied)),
        ],
        left=2,
    )
    return f"📊 <b>Market</b>\n{table}"


def _suggestions_block(status: CycleStatus) -> str:
    chosen = price_cents(status.suggested)
    rows: list[tuple[str, ...]] = [("Price", "Occ", "$/h", "$/mo", "")]
    for choice in status.choices:
        mark = "←" if price_cents(choice.price) == chosen else ""
        if choice.occupancy is None or choice.hourly_profit is None:
            occupancy, hourly, monthly = "n/a", "n/a", "n/a"
        else:
            occupancy = f"{choice.occupancy:.2f}"
            hourly = money(choice.hourly_profit, 4)
            monthly = money(choice.hourly_profit * HOURS_PER_MONTH, 2)
        rows.append((f"${choice.price:.2f}", occupancy, hourly, monthly, mark))
    return f"💡 <b>Suggestions</b>\n{_pre(rows)}\n<i>$/mo is 30 days at that occupancy.</i>"


def _fallback_block(status: CycleStatus) -> str:
    gap = abs((status.median - 0.02) - status.suggested)
    rounded = " Rounded to the nearest cent." if gap >= 0.001 else ""
    return (
        "⚠️ <b>Gemini unavailable.</b> "
        f"<i>No rationale. Suggested {money(status.suggested)}, "
        f"$0.02 under the {money(status.median, 4)} peer median.{rounded}</i>"
    )


def _rates_block(status: CycleStatus, offered: bool) -> str:
    listed, suggested, peers = status.listed_rates, status.suggested_rates, status.peer_rates

    def row(label: str, current: float, proposal: float, median: float | None) -> tuple[str, ...]:
        return (label, money(current), money(proposal), "n/a" if median is None else money(median))

    table = _pre(
        [
            ("", "Listed", "Suggest", "Peers"),
            row("Disk/GB·mo", listed.disk, suggested.disk, peers.disk),
            row("Upload/TB", listed.upload, suggested.upload, peers.upload),
            row("Download/TB", listed.download, suggested.download, peers.download),
        ]
    )
    if status.rates_rationale is None:
        note = "<i>Peer median rates, rounded to the cent. A rate no peer reports stays.</i>"
    else:
        note = f"💬 <i>{escape_html(status.rates_rationale)}</i>"
    lines = [f"📦 <b>Storage &amp; bandwidth</b>\n{table}", note]
    if same_rates(suggested, listed):
        lines.append("✅ <i>Suggested rates match the listed rates.</i>")
    elif not offered:
        lines.append("🚫 <i>Suggested rates are outside the configured bounds.</i>")
    return "\n".join(lines)


def status_text(status: CycleStatus, config: Config) -> str:
    offer_button = bool(offered_choices(status, config))
    rates_button = rates_offered(status, config)
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
    if not offer_button and price_cents(status.suggested) != price_cents(status.current):
        parts.append("🚫 <i>Outside the configured price bounds, so it cannot be applied.</i>")
    parts.append(_rates_block(status, rates_button))
    if status.listing_settings_differ:
        refresh = " Applying also refreshes them." if offer_button or rates_button else ""
        parts.append(f"⚙️ <i>Listing settings differ from config.{refresh}</i>")
    return "\n\n".join(parts)


def _duration(hours: float) -> str:
    if hours < 1:
        return "under an hour"
    if hours < 48:
        return f"about {hours:.0f} h"
    return f"about {hours / 24:.1f} days"


def rental_text(machine_id: int, started: bool, price: float, hours: float) -> str:
    """Plain-text alert. Hours is how long the previous state lasted, as far as was observed."""
    listed = f"{money(price)}/GPU-h"
    span = _duration(hours)
    if started:
        return f"🟢 Rental started on machine {machine_id} at {listed}, after {span} idle."
    return f"⚪ Rental ended on machine {machine_id} after {span}. Listed at {listed}."


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
