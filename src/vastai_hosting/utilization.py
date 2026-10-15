import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

Row = dict[str, Any]
OnChange = Callable[[bool, float, float], None]

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


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


class UtilizationHistory:
    """Observed occupancy over a rolling window, saved to `path` when one is given.

    `on_change(occupied, price, hours)` runs when the state flips; `hours` is how long the
    previous state lasted, as far as observations show.
    """

    def __init__(
        self,
        max_gap_seconds: float,
        path: str | Path | None = None,
        on_change: OnChange | None = None,
    ) -> None:
        self._max_gap_seconds = max_gap_seconds
        self._path = None if path is None else Path(path)
        self._on_change = on_change
        self._observations: list[Observation] = []
        self._load()

    def record(self, at: float, price: float, is_occupied: bool) -> None:
        previous = self._observations[-1] if self._observations else None
        hours = (at - self._streak_start()) / 3600 if previous is not None else 0.0
        self._observations.append(Observation(at, round(price, 2), is_occupied))
        cutoff = at - WINDOW_DAYS * 86_400
        self._observations = [item for item in self._observations if item.at >= cutoff]
        self._save()
        if previous is not None and previous.occupied != is_occupied and self._on_change:
            self._on_change(is_occupied, round(price, 2), max(hours, 0.0))

    def _streak_start(self) -> float:
        items = self._observations
        start = items[-1].at
        for item in reversed(items):
            if item.occupied != items[-1].occupied:
                break
            start = item.at
        return start

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

        state_hours = (items[-1].at - self._streak_start()) / 3600 if items else 0.0
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

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            rows = data["observations"]
            items = [
                Observation(float(at), float(price), state)
                for at, price, state in rows
                if _finite(at) and _finite(price) and isinstance(state, bool)
            ]
            if len(items) != len(rows):
                raise ValueError("invalid observation")
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"occupancy history at {self._path} is unreadable; starting empty: {error}")
            return
        self._observations = sorted(items, key=lambda item: item.at)

    def _save(self) -> None:
        if self._path is None:
            return
        rows = [[item.at, item.price, item.occupied] for item in self._observations]
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps({"observations": rows}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except OSError as error:
            print(f"occupancy history save failed: {error}")
