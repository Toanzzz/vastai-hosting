from dataclasses import dataclass
from math import isfinite
from typing import Any

Row = dict[str, Any]

WINDOW_DAYS = 7


@dataclass(frozen=True, slots=True)
class Observation:
    at: float
    price: float
    occupied: bool


def occupied(own: Row) -> bool | None:
    """Whether an on-demand rental is running on the host, or None when unknown.

    `current_rentals_on_demand` also counts stopped rentals that keep their contract, so a
    one-GPU host can report two; only running rentals occupy a GPU.
    """
    rentals = own.get("current_rentals_running_on_demand")
    if (
        not isinstance(rentals, (int, float))
        or isinstance(rentals, bool)
        or not isfinite(rentals)
        or rentals < 0
    ):
        return None
    return rentals > 0


class UtilizationHistory:
    """Occupancy observed once per cycle, kept in memory for a rolling window."""

    def __init__(self, max_gap_seconds: float) -> None:
        self._max_gap_seconds = max_gap_seconds
        self._observations: list[Observation] = []

    def record(self, at: float, price: float, is_occupied: bool) -> None:
        self._observations.append(Observation(at, round(price, 2), is_occupied))
        cutoff = at - WINDOW_DAYS * 86_400
        self._observations = [item for item in self._observations if item.at >= cutoff]

    def summary(self) -> Row:
        items = self._observations
        by_price: dict[float, Row] = {}
        observed = occupied_hours = 0.0
        rentals = 0
        for previous, current in zip(items, items[1:], strict=False):
            hours = min(max(current.at - previous.at, 0.0), self._max_gap_seconds) / 3600
            # Prices change only right after an observation, so the price read at the end of
            # an interval is the one renters saw throughout it.
            stats = by_price.setdefault(
                current.price, {"idleHours": 0.0, "occupiedHours": 0.0, "rentalsStarted": 0}
            )
            stats["occupiedHours" if previous.occupied else "idleHours"] += hours
            observed += hours
            occupied_hours += hours if previous.occupied else 0.0
            if not previous.occupied and current.occupied:
                stats["rentalsStarted"] += 1
                rentals += 1

        state_hours = 0.0
        if items:
            start = items[-1].at
            for item in reversed(items):
                if item.occupied != items[-1].occupied:
                    break
                start = item.at
            state_hours = (items[-1].at - start) / 3600

        return {
            "observedHours": round(observed, 2),
            "occupiedFraction": round(occupied_hours / observed, 3) if observed else None,
            "currentlyOccupied": items[-1].occupied if items else None,
            "hoursInCurrentState": round(state_hours, 2),
            "rentalsStarted": rentals,
            "byPrice": [
                {
                    "gpuPrice": price,
                    "idleHours": round(stats["idleHours"], 2),
                    "occupiedHours": round(stats["occupiedHours"], 2),
                    "rentalsStarted": stats["rentalsStarted"],
                }
                for price, stats in sorted(by_price.items())
            ],
        }
