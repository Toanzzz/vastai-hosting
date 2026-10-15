from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from statistics import median
from typing import Any

from .config import Config
from .pricing import GB_PER_TB, required_number

Row = dict[str, Any]

RATIONALE_LIMIT = 300


@dataclass(frozen=True, slots=True)
class Rates:
    """Host earnings: disk in USD per GB-month, upload and download in USD per TB."""

    disk: float
    upload: float
    download: float


@dataclass(frozen=True, slots=True)
class PeerRates:
    """Peer medians in host earnings, or None when no peer reports that rate."""

    disk: float | None
    upload: float | None
    download: float | None


def cents(value: float) -> int:
    return int(round(value * 100))


def round_cents(value: float) -> float:
    return float(Decimal(str(round(value, 4))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def listed_rates(own: Row) -> Rates:
    return Rates(
        disk=required_number(own, "listed_storage_cost"),
        upload=required_number(own, "listed_inet_up_cost") * GB_PER_TB,
        download=required_number(own, "listed_inet_down_cost") * GB_PER_TB,
    )


def _median(peers: list[Row], key: str) -> float | None:
    values = [float(peer[key]) for peer in peers if peer.get(key) is not None]
    return median(values) if values else None


def peer_rates(peers: list[Row]) -> PeerRates:
    return PeerRates(
        disk=_median(peers, "diskPrice"),
        upload=_median(peers, "uploadPricePerTb"),
        download=_median(peers, "downloadPricePerTb"),
    )


def median_rates(medians: PeerRates, listed: Rates) -> Rates:
    """Peer median per rate, rounded half-up to the cent; the listed rate when peers have none."""

    def pick(value: float | None, current: float) -> float:
        return current if value is None else round_cents(value)

    return Rates(
        disk=pick(medians.disk, listed.disk),
        upload=pick(medians.upload, listed.upload),
        download=pick(medians.download, listed.download),
    )


def rates_in_bounds(rates: Rates, config: Config) -> bool:
    def within(value: float, low: float, high: float) -> bool:
        return isfinite(value) and low <= value <= high

    return (
        within(rates.disk, config.min_disk_price, config.max_disk_price)
        and within(rates.upload, config.min_bandwidth_price, config.max_bandwidth_price)
        and within(rates.download, config.min_bandwidth_price, config.max_bandwidth_price)
    )


def same_rates(left: Rates, right: Rates) -> bool:
    return (
        cents(left.disk) == cents(right.disk)
        and cents(left.upload) == cents(right.upload)
        and cents(left.download) == cents(right.download)
    )


def parse_rates(value: Any, config: Config) -> tuple[Rates, str]:
    """Gemini's rate proposal in whole cents, validated against the configured bounds."""
    if not isinstance(value, dict):
        raise ValueError("Gemini returned no rates")
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 500:
        raise ValueError("Gemini rate rationale failed local validation")
    rates = Rates(
        disk=round(required_number(value, "diskPrice"), 2),
        upload=round(required_number(value, "uploadPricePerTb"), 2),
        download=round(required_number(value, "downloadPricePerTb"), 2),
    )
    if not rates_in_bounds(rates, config):
        raise ValueError("Gemini rates failed local validation")
    return rates, " ".join(rationale.split())[:RATIONALE_LIMIT]
