import time
from time import time as now

from .config import Config, load_config
from .demand import market_demand
from .gemini import GeminiService
from .pricing import host_share, listing_changed, peers_from_search, price_changed, required_number
from .status import CycleStatus
from .subscribers import Subscribers
from .telegram import PriceBot, redact
from .utilization import UtilizationHistory, occupied
from .vast import VastService

# A failed cycle is retried sooner than POLL_SECONDS so one outage does not skip a whole day.
RETRY_SECONDS = 3600


def run_cycle(
    config: Config, vast: VastService, gemini: GeminiService, history: UtilizationHistory
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
    best = gemini.recommend_price(own, peers, share, demand, usage)
    should_change_price = price_changed(current, best.price, config)
    settings_changed = listing_changed(own, config, at)
    estimates = " ".join(f"{price:.2f}:{rented:.2f}" for price, rented in best.estimates)
    print(
        f"machine={config.machine_id} peers={len(peers)} host_share={share:.3f} "
        f"market_usage={demand['usagePercent']}% occupied={state} "
        f"observed_hours={usage['observedHours']} occupied_fraction={usage['occupiedFraction']} "
        f"current=${current:.4f} best=${best.price:.4f} expected_occupancy={best.occupancy:.2f} "
        f"expected_profit=${best.hourly_profit:.4f}/h estimates=[{estimates}] "
        f"price_changed={should_change_price} listing_changed={settings_changed} "
        f"reason={best.rationale!r}"
    )
    return CycleStatus(
        machine_id=config.machine_id,
        current=current,
        suggested=best.price,
        occupancy=best.occupancy,
        hourly_profit=best.hourly_profit,
        estimates=best.estimates,
        occupied=state,
        market_usage=float(demand["usagePercent"]),
        peers=len(peers),
        host_share=share,
        rationale=best.rationale,
        listing_settings_differ=settings_changed,
    )


def main() -> None:
    config = load_config()
    vast = VastService(config)
    gemini = GeminiService(config)
    history = UtilizationHistory(max_gap_seconds=2 * config.poll_seconds)
    bot = PriceBot(config, vast, Subscribers(config.telegram_state_path))
    bot.start()
    print(f"pricing service started for machine={config.machine_id}")
    while True:
        delay = config.poll_seconds
        try:
            bot.publish(run_cycle(config, vast, gemini, history))
        except Exception as error:
            cause = f" ({redact(str(error.__cause__), config)[:300]})" if error.__cause__ else ""
            print(f"pricing cycle failed: {redact(str(error), config)}{cause}")
            delay = min(config.poll_seconds, RETRY_SECONDS)
        time.sleep(delay)


if __name__ == "__main__":
    main()
