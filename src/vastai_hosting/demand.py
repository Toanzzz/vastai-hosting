from math import isfinite
from typing import Any

from .pricing import required_number

Row = dict[str, Any]

DEMAND_HISTORY_DAYS = 7
DEMAND_STEP_HOURS = 6


def _count(value: Any) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError("invalid market metrics history")
    return value


def market_demand(current: Any, history: Any, gpu_name: Any, now: float) -> Row:
    """Unit-free demand for the host's GPU model from Vast's verified host market metrics.

    Metrics prices are left out: they match neither host earnings nor renter prices.
    """
    rows = current.get("gpus") if isinstance(current, dict) else None
    row = next(
        (
            item
            for item in (rows if isinstance(rows, list) else [])
            if isinstance(item, dict) and item.get("gpu_name") == gpu_name
        ),
        None,
    )
    if row is None:
        raise ValueError("market metrics missing the host GPU")

    gpus = history.get("gpus") if isinstance(history, dict) else None
    data = gpus.get(gpu_name) if isinstance(gpus, dict) else None
    series = data.get("supply_demand") if isinstance(data, dict) else None
    if not isinstance(series, dict):
        raise ValueError("invalid market metrics history")
    times = series.get("timestamps")
    rented = series.get("rented_verified_gpus")
    available = series.get("avail_verified_gpus")
    if (
        not isinstance(times, list)
        or not isinstance(rented, list)
        or not isinstance(available, list)
        or not times
        or not len(times) == len(rented) == len(available)
    ):
        raise ValueError("invalid market metrics history")

    return {
        "usagePercent": required_number(row, "usage"),
        "usagePercent30d": required_number(row, "usage_30d"),
        "rentedGpus": required_number(row, "rented_verified_gpus"),
        "availableGpus": required_number(row, "avail_verified_gpus"),
        "trend": [
            {
                "hoursAgo": round((now - _count(at)) / 3600),
                "rentedGpus": _count(rented_gpus),
                "availableGpus": _count(available_gpus),
            }
            for at, rented_gpus, available_gpus in zip(times, rented, available, strict=True)
        ],
    }
