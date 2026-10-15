from time import time as now
from typing import Any, Protocol

from .config import Config, redact
from .demand import market_demand
from .gemini import Recommendation
from .pricing import (
    host_share,
    listing_changed,
    median_peer_price,
    peers_from_search,
    price_changed,
    price_under_median,
    required_number,
)
from .rates import listed_rates, median_rates, peer_rates
from .status import CycleStatus, suggestion_choices
from .utilization import UtilizationHistory, occupied

Row = dict[str, Any]


class HostData(Protocol):
    def own_machine(self, config: Config) -> Row: ...

    def own_offers(self, config: Config) -> Any: ...

    def search_peers(self, own: Row, config: Config) -> list[Any]: ...

    def market_metrics(self, own: Row) -> tuple[Any, Any]: ...


class Pricer(Protocol):
    def recommend_price(
        self, own: Row, peers: list[Row], share: float, demand: Row, history: Row
    ) -> Recommendation: ...


def observe(own: Row, history: UtilizationHistory, at: float) -> bool | None:
    """Record whether the host is rented at its listed price."""
    state = occupied(own)
    if state is not None:
        history.record(at, required_number(own, "listed_gpu_cost"), state)
    return state


def run_cycle(
    config: Config, vast: HostData, gemini: Pricer, history: UtilizationHistory
) -> CycleStatus:
    at = now()
    own = vast.own_machine(config)
    current = required_number(own, "listed_gpu_cost")
    rates = listed_rates(own)
    state = observe(own, history, at)
    usage = history.summary()
    share = host_share(own, vast.own_offers(config))
    peers = peers_from_search(vast.search_peers(own, config), own, config, share)
    demand = market_demand(*vast.market_metrics(own), own.get("gpu_name"), at)
    median = median_peer_price(peers)
    medians = peer_rates(peers)
    try:
        recommendation = gemini.recommend_price(own, peers, share, demand, usage)
    except (RuntimeError, ValueError) as error:
        cause = f" ({redact(str(error.__cause__), config)[:300]})" if error.__cause__ else ""
        print(f"gemini unavailable: {redact(str(error), config)}{cause}")
        recommendation = None

    if recommendation is None:
        suggested = price_under_median(median)
        occupancy = hourly_profit = rationale = None
        estimates: tuple[tuple[float, float], ...] = ()
    else:
        suggested = recommendation.price
        occupancy = recommendation.occupancy
        hourly_profit = recommendation.hourly_profit
        estimates = recommendation.estimates
        rationale = recommendation.rationale
    if recommendation is not None and recommendation.rates is not None:
        suggested_rates = recommendation.rates
        rates_rationale = recommendation.rates_rationale
    else:
        suggested_rates = median_rates(medians, rates)
        rates_rationale = None

    status = CycleStatus(
        machine_id=config.machine_id,
        at=at,
        current=current,
        suggested=suggested,
        occupancy=occupancy,
        hourly_profit=hourly_profit,
        estimates=estimates,
        choices=suggestion_choices(
            estimates,
            running_cost=config.running_cost,
            current=current,
            suggested=suggested,
            occupancy=occupancy,
            hourly_profit=hourly_profit,
        ),
        occupied=state,
        market_usage=_number(demand, "usagePercent"),
        usage_30d=_number(demand, "usagePercent30d"),
        rented_gpus=_number(demand, "rentedGpus"),
        available_gpus=_number(demand, "availableGpus"),
        peers=len(peers),
        peer_low=min(float(peer["gpuPrice"]) for peer in peers),
        peer_high=max(float(peer["gpuPrice"]) for peer in peers),
        host_share=share,
        median=median,
        rationale=rationale,
        listing_settings_differ=listing_changed(own, config, at),
        listed_rates=rates,
        suggested_rates=suggested_rates,
        peer_rates=medians,
        rates_rationale=rates_rationale,
    )
    _log(config, status, demand, usage)
    return status


def _log(config: Config, status: CycleStatus, demand: Row, usage: Row) -> None:
    rented = "n/a" if status.occupancy is None else f"{status.occupancy:.2f}"
    profit = "n/a" if status.hourly_profit is None else f"${status.hourly_profit:.4f}/h"
    estimates = " ".join(f"{price:.2f}:{share:.2f}" for price, share in status.estimates)
    listed, proposed = status.listed_rates, status.suggested_rates
    print(
        f"machine={config.machine_id} peers={status.peers} host_share={status.host_share:.3f} "
        f"median=${status.median:.4f} market_usage={demand['usagePercent']}% "
        f"occupied={status.occupied} observed_hours={usage['observedHours']} "
        f"occupied_fraction={usage['occupiedFraction']} current=${status.current:.4f} "
        f"best=${status.suggested:.4f} expected_occupancy={rented} expected_profit={profit} "
        f"estimates=[{estimates}] "
        f"price_changed={price_changed(status.current, status.suggested, config)} "
        f"rates=disk:{listed.disk:.2f}->{proposed.disk:.2f} "
        f"up:{listed.upload:.2f}->{proposed.upload:.2f} "
        f"down:{listed.download:.2f}->{proposed.download:.2f} "
        f"listing_changed={status.listing_settings_differ} "
        f"gemini={status.rationale is not None} gemini_rates={status.rates_rationale is not None} "
        f"reason={status.rationale!r} rates_reason={status.rates_rationale!r}"
    )


def _number(row: Row, key: str) -> float:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {key}")
    return float(value)
