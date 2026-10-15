import threading
from time import time as now

from .config import Config, load_config, redact
from .cycle import HostData, observe, run_cycle
from .gemini import GeminiService
from .subscribers import Subscribers
from .telegram import PriceBot
from .utilization import UtilizationHistory
from .vast import VastService

# A failed cycle is retried sooner than POLL_SECONDS so one outage does not skip a whole day.
RETRY_SECONDS = 3600


def _failure(error: Exception, config: Config) -> str:
    cause = f" ({redact(str(error.__cause__), config)[:300]})" if error.__cause__ else ""
    return f"{redact(str(error), config)}{cause}"


def sample(config: Config, vast: HostData, history: UtilizationHistory) -> None:
    """Occupancy between pricing cycles: one machine read, no market or Gemini calls."""
    try:
        observe(vast.own_machine(config), history, now())
    except Exception as error:
        print(f"occupancy sample failed: {_failure(error, config)}")


def main() -> None:
    config = load_config()
    vast = VastService(config)
    gemini = GeminiService(config)
    trigger = threading.Event()
    bot = PriceBot(config, vast, Subscribers(config.telegram_state_path), on_check=trigger.set)
    history = UtilizationHistory(
        max_gap_seconds=2 * config.sample_seconds,
        path=config.history_state_path,
        on_change=bot.notify_rental,
    )
    bot.start()
    print(f"pricing service started for machine={config.machine_id}")
    next_cycle = 0.0
    while True:
        if trigger.is_set() or now() >= next_cycle:
            trigger.clear()
            epoch = bot.pending_epoch()
            try:
                bot.publish(run_cycle(config, vast, gemini, history), epoch=epoch)
                next_cycle = now() + config.poll_seconds
            except Exception as error:
                print(f"pricing cycle failed: {_failure(error, config)}")
                bot.notify_check_failed(epoch)
                next_cycle = now() + min(config.poll_seconds, RETRY_SECONDS)
        else:
            sample(config, vast, history)
        trigger.wait(max(0.0, min(next_cycle, now() + config.sample_seconds) - now()))


if __name__ == "__main__":
    main()
