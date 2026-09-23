from math import isfinite
from typing import Any

from vastai import VastAI

from .config import Config

Row = dict[str, Any]


class VastService:
    def __init__(self, config: Config) -> None:
        self._client = VastAI(api_key=config.vast_api_key, retry=1, raw=True)

    def own_machine(self, config: Config) -> Row:
        try:
            machines = self._client.show_machines()
        except Exception as error:
            raise RuntimeError("Vast API request failed") from error
        machine = next(
            (item for item in machines if item.get("machine_id") == config.machine_id), None
        )
        if machine is None:
            raise ValueError(f"machine {config.machine_id} not found in your account")
        if machine.get("listed") is not True:
            raise ValueError("machine is not listed; list it manually first")
        return machine

    def search_peers(self, own: Row, config: Config) -> list[Row]:
        gpu_name = own.get("gpu_name")
        gpu_count = own.get("num_gpus")
        if (
            not isinstance(gpu_name, str)
            or not isinstance(gpu_count, int)
            or isinstance(gpu_count, bool)
            or gpu_count <= 0
            or config.min_chunk > gpu_count
        ):
            raise ValueError("invalid machine GPU details")
        query = (
            f"gpu_name={gpu_name.replace(' ', '_')} num_gpus={gpu_count} "
            "verified=true rentable=true"
        )
        try:
            return self._client.search_offers(
                query=query,
                order="dph",
                limit=config.offer_limit,
                storage=5.0,
            )
        except Exception as error:
            raise RuntimeError("Vast market request failed") from error

    def update_listing(self, config: Config, target: float) -> None:
        if not isfinite(target) or not config.min_price <= target <= config.max_price:
            raise ValueError("listing price outside configured bounds")
        if target < config.min_bid_price:
            raise ValueError("listing price below minimum bid")
        try:
            result = self._client.list_machine(
                id=config.machine_id,
                price_gpu=target,
                price_disk=config.disk_price,
                price_inetu=config.upload_price,
                price_inetd=config.download_price,
                price_min_bid=config.min_bid_price,
                discount_rate=config.discount_rate,
                min_chunk=config.min_chunk,
                duration=f"{config.duration_days} days",
                vol_size=config.volume_size_gb,
                vol_price=config.volume_price,
            )
        except Exception as error:
            raise RuntimeError("Vast listing update failed") from error
        if not isinstance(result, dict) or result.get("success") is not True:
            raise RuntimeError("Vast listing update did not report success")
