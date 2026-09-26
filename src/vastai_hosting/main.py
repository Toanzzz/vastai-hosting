import threading
from time import time as now
from typing import Any, Protocol

from .config import Config, load_config
from .demand import market_demand
from .gemini import GeminiService, Recommendation
from .pricing import (
    host_share,
    listing_changed,
    median_peer_price,
    peers_from_search,
    price_changed,
    price_under_median,
    required_number,
)
from .status import CycleStatus
from .subscribers import Subscribers
from .telegram import PriceBot, redact
from .utilization import UtilizationHistory, occupied
from .vast import VastService

# A failed cycle is retried sooner than POLL_SECONDS so one outage does not skip a whole day.
RETRY_SECONDS = 3600
Row = dict[str, Any]


class _HostData(Protocol):
    def own_machine(self, config: Config) -> Row: ...

    def own_offers(self, config: Config) -> Any: ...

    def search_peers(self, own: Row, config: Config) -> list[Any]: ...

    def market_metrics(self, own: Row) -> tuple[Any, Any]: ...


class _Pricer(Protocol):
    def recommend_price(
        self, own: Row, peers: list[Row], share: float, demand: Row, history: Row
    ) -> Recommendation: ...


def run_cycle(
    config: Config, vast: _HostData, gemini: _Pricer, history: UtilizationHistory
) -> CycleStatus:
    at = now()
    own = vast.own_machine(config)
    current = required_number(own, "listed_gpu_cost")
    state = occupied(own)
    if state is not None:
        history.record(at, current, state)
    usage = history.summary()
    share = host_share(own, vast.own_offers(config))
    peers = peers_from_search(vast.search_peers(own, config), own, config, share)
    demand = market_demand(*vast.market_metrics(own), own.get("gpu_name"), at)
    median = median_peer_price(peers)
    try:
        recommendation = gemini.recommend_price(own, peers, share, demand, usage)
    except (RuntimeError, ValueError) as error:
        cause = f" ({redact(str(error.__cause__), config)[:300]})" if error.__cause__ else ""
        print(f"gemini unavailable: {redact(str(error), config)}{cause}")
        recommendation = None
    return _status(
        config, at, own, current, state, share, peers, demand, median, usage, recommendation
    )


def _status(
    config: Config,
    at: float,
    own: Row,
    current: float,
    state: bool | None,
    share: float,
    peers: list[Row],
    demand: Row,
    median: float,
    usage: Row,
    recommendation: Recommendation | None,
) -> CycleStatus:
    prices = [float(peer["gpuPrice"]) for peer in peers]
    if recommendation is None:
        suggested = price_under_median(median)
        occupancy = None
        hourly_profit = None
        estimates: tuple[tuple[float, float], ...] = ()
        rationale = None
        estimate_text = ""
    else:
        suggested = recommendation.price
        occupancy = recommendation.occupancy
        hourly_profit = recommendation.hourly_profit
        estimates = recommendation.estimates
        rationale = recommendation.rationale
        estimate_text = " ".join(f"{price:.2f}:{rented:.2f}" for price, rented in estimates)
    settings_changed = listing_changed(own, config, at)
    rented = "n/a" if occupancy is None else f"{occupancy:.2f}"
    profit = "n/a" if hourly_profit is None else f"${hourly_profit:.4f}/h"
    print(
        f"machine={config.machine_id} peers={len(peers)} host_share={share:.3f} "
        f"median=${median:.4f} market_usage={demand['usagePercent']}% occupied={state} "
        f"observed_hours={usage['observedHours']} occupied_fraction={usage['occupiedFraction']} "
        f"current=${current:.4f} best=${suggested:.4f} expected_occupancy={rented} "
        f"expected_profit={profit} estimates=[{estimate_text}] "
        f"price_changed={price_changed(current, suggested, config)} "
        f"listing_changed={settings_changed} gemini={rationale is not None} "
        f"reason={rationale!r}"
    )
    return CycleStatus(
        machine_id=config.machine_id,
        current=current,
        suggested=suggested,
        occupancy=occupancy,
        hourly_profit=hourly_profit,
        estimates=estimates,
        occupied=state,
        market_usage=_number(demand, "usagePercent"),
        usage_30d=_number(demand, "usagePercent30d"),
        rented_gpus=_number(demand, "rentedGpus"),
        available_gpus=_number(demand, "availableGpus"),
        peers=len(peers),
        peer_low=min(prices),
        peer_high=max(prices),
        host_share=share,
        median=median,
        rationale=rationale,
        listing_settings_differ=settings_changed,
    )


def _number(row: Row, key: str) -> float:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {key}")
    return float(value)


def main() -> None:
    config = load_config()
    vast = VastService(config)
    gemini = GeminiService(config)
    history = UtilizationHistory(max_gap_seconds=2 * config.poll_seconds)
    trigger = threading.Event()
    bot = PriceBot(config, vast, Subscribers(config.telegram_state_path), on_check=trigger.set)
    bot.start()
    print(f"pricing service started for machine={config.machine_id}")
    while True:
        delay = config.poll_seconds
        epoch = bot.pending_epoch()
        try:
            bot.publish(run_cycle(config, vast, gemini, history), epoch=epoch)
        except Exception as error:
            cause = f" ({redact(str(error.__cause__), config)[:300]})" if error.__cause__ else ""
            print(f"pricing cycle failed: {redact(str(error), config)}{cause}")
            bot.notify_check_failed(epoch)
            delay = min(config.poll_seconds, RETRY_SECONDS)
        if trigger.wait(delay):
            trigger.clear()


if __name__ == "__main__":
    main()
