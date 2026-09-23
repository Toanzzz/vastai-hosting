from math import isfinite
from typing import Any

from .config import Config

Row = dict[str, Any]


def required_number(row: Row, field: str) -> float:
    value = row.get(field)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"missing or invalid {field}")
    return float(value)


def _optional_number(row: Row, key: str) -> float | None:
    value = row.get(key)
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value >= 0
    ):
        return float(value)
    return None


def host_details(own: Row) -> Row:
    return {
        "gpuName": own.get("gpu_name"),
        "gpuCount": own.get("num_gpus"),
        "currentGpuPrice": required_number(own, "listed_gpu_cost"),
        "reliability": _optional_number(own, "reliability2"),
        "gpuRamMb": _optional_number(own, "gpu_ram"),
        "cpuRamMb": _optional_number(own, "cpu_ram"),
        "inetUpMbps": _optional_number(own, "inet_up"),
        "inetDownMbps": _optional_number(own, "inet_down"),
        "diskBwMbps": _optional_number(own, "disk_bw"),
        "geolocation": own.get("geolocation") if isinstance(own.get("geolocation"), str) else None,
        "currentRentals": _optional_number(own, "current_rentals_on_demand"),
    }


def peers_from_search(data: Any, own: Row, config: Config) -> list[Row]:
    offers = (
        data if isinstance(data, list) else data.get("offers") if isinstance(data, dict) else None
    )
    if not isinstance(offers, list):
        raise ValueError("expected offers array")
    if len(offers) >= config.offer_limit:
        raise ValueError(
            "market results reached OFFER_LIMIT; increase it to avoid a truncated median"
        )

    name = own.get("gpu_name")
    count = required_number(own, "num_gpus")
    if not isinstance(name, str) or count == 0 or not count.is_integer():
        raise ValueError("invalid machine GPU details")

    seen: set[int] = set()
    peers: list[Row] = []
    for item in offers:
        if not isinstance(item, dict):
            continue
        machine_id = item.get("machine_id")
        base = item.get("dph_base")
        if (
            not isinstance(machine_id, int)
            or isinstance(machine_id, bool)
            or machine_id == config.machine_id
            or item.get("gpu_name") != name
            or item.get("num_gpus") != count
            or item.get("rentable") is not True
            or item.get("verification") != "verified"
            or not isinstance(base, (int, float))
            or isinstance(base, bool)
            or not isfinite(base)
            or base <= 0
            or machine_id in seen
        ):
            continue
        seen.add(machine_id)
        peers.append(
            {
                "gpuPrice": float(base) / count,
                "reliability": _optional_number(item, "reliability2"),
                "gpuRamMb": _optional_number(item, "gpu_ram"),
                "cpuRamMb": _optional_number(item, "cpu_ram"),
                "inetUpMbps": _optional_number(item, "inet_up"),
                "inetDownMbps": _optional_number(item, "inet_down"),
                "diskBwMbps": _optional_number(item, "disk_bw"),
                "geolocation": (
                    item.get("geolocation") if isinstance(item.get("geolocation"), str) else None
                ),
            }
        )
    if len(peers) < config.min_peers:
        raise ValueError(f"only {len(peers)} comparable offers (need {config.min_peers})")
    return peers


def median_peer_price(peers: list[Row]) -> float:
    prices = sorted(float(peer["gpuPrice"]) for peer in peers)
    if not prices:
        raise ValueError("no comparable prices")
    middle = len(prices) // 2
    if len(prices) % 2:
        return prices[middle]
    return (prices[middle - 1] + prices[middle]) / 2


def _differs(row: Row, key: str, value: float) -> bool:
    actual = row.get(key)
    return (
        not isinstance(actual, (int, float))
        or isinstance(actual, bool)
        or not isfinite(actual)
        or abs(float(actual) - value) > 0.000001
    )


def listing_changed(own: Row, config: Config, now_seconds: float) -> bool:
    return (
        _differs(own, "listed_storage_cost", config.disk_price)
        or _differs(own, "listed_inet_up_cost", config.upload_price)
        or _differs(own, "listed_inet_down_cost", config.download_price)
        or _differs(own, "min_bid_price", config.min_bid_price)
        or _differs(own, "credit_discount_max", config.discount_rate)
        or _differs(own, "listed_volume_cost", config.volume_price)
        or own.get("listed_min_gpu_count") != config.min_chunk
        or own.get("volume_total_size") != config.volume_size_gb
        or not isinstance(own.get("end_date"), (int, float))
        or not isfinite(own["end_date"])
        or float(own["end_date"]) < now_seconds + (config.duration_days - 1) * 86_400
    )


def price_changed(current: float, target: float, config: Config) -> bool:
    return (
        abs(target - current) + 0.000001 >= config.min_change
        or not config.min_price <= current <= config.max_price
        or current < config.min_bid_price
    )
