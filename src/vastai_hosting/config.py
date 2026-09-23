import os
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class Config:
    machine_id: int
    vast_api_key: str
    gemini_api_key: str
    gemini_model: str
    gemini_thinking_budget: int
    dry_run: bool
    poll_seconds: int
    min_peers: int
    offer_limit: int
    min_price: float
    max_price: float
    min_change: float
    disk_price: float
    upload_price: float
    download_price: float
    min_bid_price: float
    discount_rate: float
    min_chunk: int
    volume_size_gb: int
    volume_price: float
    duration_days: int
    running_cost: float


# Defaults for the one listed host (machine 51673, one RTX 5090).
# An unset or blank variable uses the default; set it only to override.
DEFAULTS: dict[str, str] = {
    "GEMINI_MODEL": "gemini-flash-latest",
    "GEMINI_THINKING_BUDGET": "1024",
    "MACHINE_ID": "51673",
    "DRY_RUN": "true",
    "POLL_SECONDS": "86400",
    "MIN_PEERS": "5",
    "OFFER_LIMIT": "200",
    "MIN_PRICE": "0.40",
    "MAX_PRICE": "1.00",
    "MIN_CHANGE": "0.01",
    "GPU_RUNNING_COST": "0",
    "DISK_PRICE": "0.15",
    "UPLOAD_PRICE": "0.001953125",
    "DOWNLOAD_PRICE": "0.001953125",
    "MIN_BID_PRICE": "0.40",
    "DISCOUNT_RATE": "0.10",
    "MIN_CHUNK": "1",
    "VOLUME_SIZE_GB": "209",
    "VOLUME_PRICE": "0.15",
    "DURATION_DAYS": "7",
}


def _text(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if value:
        return value
    if key in DEFAULTS:
        return DEFAULTS[key]
    raise ValueError(f"missing {key}")


def _number(key: str) -> float:
    text = _text(key)
    try:
        value = float(text)
    except ValueError as error:
        raise ValueError(f"{key} must be a finite nonnegative number") from error
    if not isfinite(value) or value < 0:
        raise ValueError(f"{key} must be a finite nonnegative number")
    return value


def _optional_number(key: str, default: float) -> float:
    return _number(key) if os.environ.get(key, "").strip() else default


def _integer(key: str) -> int:
    value = _number(key)
    if not value.is_integer() or value > 2**53 - 1:
        raise ValueError(f"{key} must be a nonnegative safe integer")
    return int(value)


def load_config() -> Config:
    dry_run = _text("DRY_RUN")
    if dry_run not in {"true", "false"}:
        raise ValueError("DRY_RUN must be true or false")

    config = Config(
        machine_id=_integer("MACHINE_ID"),
        vast_api_key=_text("VAST_API_KEY"),
        gemini_api_key=_text("GEMINI_API_KEY"),
        gemini_model=_text("GEMINI_MODEL"),
        gemini_thinking_budget=_integer("GEMINI_THINKING_BUDGET"),
        dry_run=dry_run == "true",
        poll_seconds=_integer("POLL_SECONDS"),
        min_peers=_integer("MIN_PEERS"),
        offer_limit=_integer("OFFER_LIMIT"),
        min_price=_number("MIN_PRICE"),
        max_price=_number("MAX_PRICE"),
        min_change=_number("MIN_CHANGE"),
        disk_price=_number("DISK_PRICE"),
        upload_price=_number("UPLOAD_PRICE"),
        download_price=_number("DOWNLOAD_PRICE"),
        min_bid_price=_number("MIN_BID_PRICE"),
        discount_rate=_number("DISCOUNT_RATE"),
        min_chunk=_integer("MIN_CHUNK"),
        volume_size_gb=_integer("VOLUME_SIZE_GB"),
        volume_price=_number("VOLUME_PRICE"),
        duration_days=_integer("DURATION_DAYS"),
        running_cost=_number("GPU_RUNNING_COST"),
    )
    if (
        config.machine_id == 0
        or config.gemini_thinking_budget < 0
        or config.poll_seconds < 60
        or config.min_peers < 3
        or config.offer_limit < config.min_peers
        or config.offer_limit > 1000
        or config.min_price <= 0
        or config.max_price < config.min_price
        or config.min_change <= 0
        or config.min_chunk == 0
        or config.discount_rate > 1
        or config.min_bid_price > config.min_price
        or config.volume_size_gb == 0
        or not 2 <= config.duration_days <= 365
        or config.running_cost >= config.max_price
    ):
        raise ValueError("invalid pricing, interval, peer count, bid floor or listing settings")
    return config
