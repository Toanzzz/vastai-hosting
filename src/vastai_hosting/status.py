from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CycleStatus:
    machine_id: int
    current: float
    suggested: float
    occupancy: float
    hourly_profit: float
    estimates: tuple[tuple[float, float], ...]
    occupied: bool | None
    market_usage: float
    peers: int
    host_share: float
    rationale: str
    listing_settings_differ: bool


def price_cents(price: float) -> int:
    return int(round(price * 100))


def parse_cents(data: str) -> int | None:
    raw = data.removeprefix("p:")
    if raw == data or not raw.isdigit() or len(raw) > 6 or (len(raw) > 1 and raw.startswith("0")):
        return None
    return int(raw)


def status_text(status: CycleStatus) -> str:
    if status.occupied is True:
        occupied = "yes"
    elif status.occupied is False:
        occupied = "no"
    else:
        occupied = "unknown"
    estimates = " ".join(f"{price:.2f}:{share:.2f}" for price, share in status.estimates)
    lines = [
        f"Machine {status.machine_id}",
        f"Listed ${status.current:.2f}/GPU-h, suggested ${status.suggested:.2f}/GPU-h",
        (
            f"Occupied: {occupied}, market usage {status.market_usage:g}%, "
            f"peers {status.peers}, host share {status.host_share:.3f}"
        ),
        f"Expected occupancy {status.occupancy:.2f}, profit ${status.hourly_profit:.4f}/h",
        f"Estimates: {estimates}",
    ]
    if status.listing_settings_differ:
        lines.append("Listing settings differ from config. Applying also refreshes them.")
    lines.append(status.rationale)
    return "\n".join(lines)


def keyboard(price: float) -> dict[str, Any]:
    cents = price_cents(price)
    button = {"text": f"Set price to ${cents / 100:.2f}", "callback_data": f"p:{cents}"}
    return {"inline_keyboard": [[button]]}
