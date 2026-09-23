from math import isfinite
from time import time
from typing import Any

from vastai import VastAI
from vastai.api import metrics

from .config import Config
from .demand import DEMAND_HISTORY_DAYS, DEMAND_STEP_HOURS

Row = dict[str, Any]

# Search returns a different sample of about 55 offers per call; repeat until no new machines.
MAX_SEARCH_CALLS = 12


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

    def search_peers(self, own: Row, config: Config) -> list[Any]:
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
        batches: list[Any] = []
        seen: set[Any] = set()
        for _ in range(MAX_SEARCH_CALLS):
            try:
                batch = self._client.search_offers(
                    query=query,
                    order="dph",
                    limit=config.offer_limit,
                    storage=5.0,
                )
            except Exception as error:
                raise RuntimeError("Vast market request failed") from error
            batches.append(batch)
            if not isinstance(batch, list):
                break
            found = {item.get("machine_id") for item in batch if isinstance(item, dict)}
            if found <= seen:
                break
            seen |= found
        return batches

    def own_offers(self, config: Config) -> list[Row]:
        try:
            return self._client.search_offers(
                query=f"machine_id={config.machine_id} rentable=any rented=any verified=any",
                limit=10,
                storage=5.0,
            )
        except Exception as error:
            raise RuntimeError("Vast own offer request failed") from error

    def market_metrics(self, own: Row) -> tuple[Any, Any]:
        gpu_name = own.get("gpu_name")
        count = own.get("num_gpus")
        if not isinstance(gpu_name, str):
            raise ValueError("invalid machine GPU details")
        # Vast buckets machines by 1, 2, 4 or 8 GPUs.
        bucket = str(count) if count in (1, 2, 4, 8) and not isinstance(count, bool) else "all"
        end = int(time())
        try:
            current = metrics.gpu_current(self._client.client, verified="yes", num_gpus=bucket)
            history = metrics.gpu_history(
                self._client.client,
                gpu_name=gpu_name,
                verified="yes",
                start=end - DEMAND_HISTORY_DAYS * 86_400,
                end=end,
                step=DEMAND_STEP_HOURS * 3600,
                num_gpus=bucket,
            )
        except Exception as error:
            raise RuntimeError("Vast market metrics request failed") from error
        return current, history

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
