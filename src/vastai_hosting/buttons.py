from dataclasses import dataclass
from typing import Any

from .config import Config
from .pricing import gpu_in_bounds
from .rates import Rates, cents, rates_in_bounds
from .status import HOURS_PER_MONTH, CycleStatus, PriceChoice, money, offered_choices, rates_offered

# A tap applies the prices on its own message, so old market data must not reach the listing.
BUTTON_TTL_SECONDS = 48 * 3600
# Telegram and host clocks can disagree by a little.
CLOCK_SKEW_SECONDS = 300
DISMISS = "d"
RATES_LABEL = "Set disk/bandwidth rates"


@dataclass(frozen=True, slots=True)
class SetGpu:
    cents: int
    # None for buttons sent before callbacks carried the market check time.
    at: int | None

    @property
    def price(self) -> float:
        return self.cents / 100


@dataclass(frozen=True, slots=True)
class SetRates:
    disk: int
    upload: int
    download: int
    at: int

    @property
    def rates(self) -> Rates:
        return Rates(self.disk / 100, self.upload / 100, self.download / 100)


@dataclass(frozen=True, slots=True)
class Dismiss:
    pass


Action = SetGpu | SetRates | Dismiss


def _digits(raw: str, limit: int) -> int | None:
    if (
        not raw.isascii()
        or not raw.isdigit()
        or len(raw) > limit
        or (len(raw) > 1 and raw.startswith("0"))
    ):
        return None
    return int(raw)


def parse_action(data: str) -> Action | None:
    if data == DISMISS:
        return Dismiss()
    kind, _, rest = data.partition(":")
    *fields, stamp = rest.split(":")
    if kind == "p" and not fields:
        legacy = _digits(stamp, 6)
        return None if legacy is None else SetGpu(legacy, None)
    at = _digits(stamp, 11)
    values = [value for value in (_digits(field, 6) for field in fields) if value is not None]
    if at is None or len(values) != len(fields):
        return None
    if kind == "p" and len(values) == 1:
        return SetGpu(values[0], at)
    if kind == "r" and len(values) == 3:
        return SetRates(values[0], values[1], values[2], at)
    return None


def expired(action: SetGpu | SetRates, now: float) -> bool:
    return action.at is None or not -CLOCK_SKEW_SECONDS <= now - action.at <= BUTTON_TTL_SECONDS


def in_bounds(action: SetGpu | SetRates, config: Config) -> bool:
    if isinstance(action, SetGpu):
        return gpu_in_bounds(action.price, config)
    return rates_in_bounds(action.rates, config)


def gpu_data(price: float, at: float) -> str:
    return f"p:{cents(price)}:{int(at)}"


def rates_data(rates: Rates, at: float) -> str:
    return f"r:{cents(rates.disk)}:{cents(rates.upload)}:{cents(rates.download)}:{int(at)}"


def _choice_button(choice: PriceChoice, at: float, *, best: bool) -> dict[str, str]:
    label = f"${cents(choice.price) / 100:.2f}"
    if choice.hourly_profit is None:
        text = f"Set price to {label}"
    else:
        monthly = money(choice.hourly_profit * HOURS_PER_MONTH, 2)
        text = f"{'Best' if best else 'Set'} {label} · {monthly}/mo"
    return {"text": text, "callback_data": gpu_data(choice.price, at)}


def report_keyboard(status: CycleStatus, config: Config) -> dict[str, Any] | None:
    best = status.choices[0] if status.choices else None
    rows = [
        [_choice_button(choice, status.at, best=choice is best)]
        for choice in offered_choices(status, config)
    ]
    if rates_offered(status, config):
        data = rates_data(status.suggested_rates, status.at)
        rows.append([{"text": RATES_LABEL, "callback_data": data}])
    if not rows:
        return None
    rows.append([{"text": "Dismiss", "callback_data": DISMISS}])
    return {"inline_keyboard": rows}


def applied_note(action: SetGpu | SetRates) -> str:
    if isinstance(action, SetGpu):
        return f"Applied {money(action.price)}/GPU-h."
    return "Applied disk/bandwidth rates."


def applied_keyboard(action: SetGpu | SetRates, data: str) -> dict[str, Any]:
    text = f"Set price to {money(action.price)}" if isinstance(action, SetGpu) else RATES_LABEL
    return {"inline_keyboard": [[{"text": text, "callback_data": data}]]}
