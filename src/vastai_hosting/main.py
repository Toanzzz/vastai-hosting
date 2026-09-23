import time
from time import time as now

from .config import Config, load_config
from .gemini import GeminiService
from .pricing import listing_changed, peers_from_search, price_changed, required_number
from .vast import VastService


def run_cycle(config: Config, vast: VastService, gemini: GeminiService) -> None:
    own = vast.own_machine(config)
    raw_peers = vast.search_peers(own, config)
    peers = peers_from_search(raw_peers, own, config)
    target, rationale = gemini.recommend_price(own, peers)
    current = required_number(own, "listed_gpu_cost")
    should_change_price = price_changed(current, target, config)
    settings_changed = listing_changed(own, config, now())
    proposed = target if should_change_price else current
    print(
        f"machine={config.machine_id} peers={len(peers)} current=${current:.4f} "
        f"target=${proposed:.4f} listing_changed={settings_changed} "
        f"dry_run={config.dry_run} reason={rationale!r}"
    )
    if (not should_change_price and not settings_changed) or config.dry_run:
        return
    vast.update_listing(config, proposed)
    print(f"machine={config.machine_id} listing updated")


def main() -> None:
    config = load_config()
    vast = VastService(config)
    gemini = GeminiService(config)
    print(f"pricing service started for machine={config.machine_id} dry_run={config.dry_run}")
    while True:
        try:
            run_cycle(config, vast, gemini)
        except Exception as error:
            print(f"pricing cycle failed: {error}")
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    main()
