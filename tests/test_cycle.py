from typing import Any

import pytest

from vastai_hosting.config import Config
from vastai_hosting.cycle import run_cycle
from vastai_hosting.gemini import Recommendation
from vastai_hosting.main import sample
from vastai_hosting.pricing import price_under_median
from vastai_hosting.rates import PeerRates, Rates
from vastai_hosting.utilization import UtilizationHistory

OWN: dict[str, Any] = {
    "machine_id": 42,
    "gpu_name": "RTX 5090",
    "num_gpus": 1,
    "listed_gpu_cost": 0.6,
    "listed_storage_cost": 0.15,
    "listed_inet_up_cost": 2 / 1024,
    "listed_inet_down_cost": 2 / 1024,
    "current_rentals_running_on_demand": 0,
}
OFFERS: list[dict[str, Any]] = [
    {
        "machine_id": machine_id,
        "gpu_name": "RTX 5090",
        "num_gpus": 1,
        "rentable": True,
        "verification": "verified",
        "dph_base": price,
        "storage_cost": storage,
        "inet_up_cost": 4 / 1024 / 0.75,
    }
    for machine_id, price, storage in ((1, 0.4, 0.2), (2, 0.5, 0.3), (3, 0.6, 0.2))
]
CURRENT: dict[str, Any] = {
    "gpus": [
        {
            "gpu_name": "RTX 5090",
            "usage": 90.3,
            "usage_30d": 81.5,
            "rented_verified_gpus": 1227,
            "avail_verified_gpus": 132,
        }
    ]
}
HISTORY: dict[str, Any] = {
    "gpus": {
        "RTX 5090": {
            "supply_demand": {
                "timestamps": [1_000_000.0],
                "rented_verified_gpus": [1227],
                "avail_verified_gpus": [132],
            }
        }
    }
}


def config() -> Config:
    return Config(
        machine_id=42,
        vast_api_key="test",
        gemini_api_key="test",
        gemini_model="gemini-flash-latest",
        gemini_thinking_budget=1024,
        poll_seconds=1800,
        sample_seconds=600,
        min_peers=3,
        offer_limit=100,
        min_price=0.35,
        max_price=0.8,
        min_change=0.01,
        min_disk_price=0.05,
        max_disk_price=0.4,
        min_bandwidth_price=1.0,
        max_bandwidth_price=10.0,
        min_bid_price=0.3,
        discount_rate=0.1,
        min_chunk=1,
        volume_size_gb=200,
        volume_price=0.15,
        duration_days=7,
        running_cost=0.0,
        telegram_bot_token="123456:ABC_def",
        telegram_subscribe_secret="subscribe",
        telegram_state_path="data/subscribers.json",
        history_state_path="data/occupancy.json",
    )


class Vast:
    def __init__(self, offers: list[dict[str, Any]] | None = None) -> None:
        self.offers = OFFERS if offers is None else offers
        self.own = OWN

    def own_machine(self, config: Config) -> dict[str, Any]:
        return self.own

    def own_offers(self, config: Config) -> Any:
        return [{"machine_id": 42, "num_gpus": 1, "dph_base": 0.8}]

    def search_peers(self, own: dict[str, Any], config: Config) -> list[Any]:
        return [self.offers]

    def market_metrics(self, own: dict[str, Any]) -> tuple[Any, Any]:
        return CURRENT, HISTORY


class Gemini:
    def __init__(self, error: Exception | None = None, rates: Rates | None = None) -> None:
        self.error = error
        self.rates = rates
        self.calls = 0

    def recommend_price(
        self,
        own: dict[str, Any],
        peers: list[dict[str, Any]],
        share: float,
        demand: dict[str, Any],
        history: dict[str, Any],
    ) -> Recommendation:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return Recommendation(
            price=0.5,
            occupancy=0.9,
            hourly_profit=0.45,
            estimates=((0.5, 0.9), (0.6, 0.6), (0.7, 0.3)),
            rationale="Peer prices",
            rates=self.rates,
            rates_rationale=None if self.rates is None else "Peer rates",
        )


def test_rates_come_from_gemini_or_the_peer_median() -> None:
    result = run_cycle(config(), Vast(), Gemini(), UtilizationHistory(3600))
    assert result.listed_rates == Rates(0.15, 2.0, 2.0)
    assert result.peer_rates.disk == pytest.approx(0.15)
    assert result.peer_rates.upload == pytest.approx(4.0)
    assert result.peer_rates.download is None
    assert result.suggested_rates == Rates(0.15, 4.0, 2.0)
    assert result.rates_rationale is None

    proposed = Rates(0.2, 3.0, 3.0)
    kept = run_cycle(config(), Vast(), Gemini(rates=proposed), UtilizationHistory(3600))
    assert kept.suggested_rates == proposed
    assert kept.rates_rationale == "Peer rates"
    assert isinstance(kept.peer_rates, PeerRates)


def test_sampling_records_occupancy_and_reports_changes() -> None:
    changes: list[tuple[bool, float, float]] = []
    history = UtilizationHistory(3600, on_change=lambda *change: changes.append(change))
    vast = Vast()
    sample(config(), vast, history)
    vast.own = {**OWN, "current_rentals_running_on_demand": 1}
    sample(config(), vast, history)
    assert history.summary()["currentlyOccupied"] is True
    assert [(started, price) for started, price, _hours in changes] == [(True, 0.6)]

    class Broken(Vast):
        def own_machine(self, config: Config) -> dict[str, Any]:
            raise RuntimeError("Vast API request failed")

    sample(config(), Broken(), history)
    assert len(changes) == 1


def test_gemini_failure_suggests_two_cents_under_the_median() -> None:
    gemini = Gemini(RuntimeError("Gemini request failed"))
    result = run_cycle(config(), Vast(), gemini, UtilizationHistory(3600))
    assert gemini.calls == 1
    assert result.rationale is None
    assert result.occupancy is None
    assert result.hourly_profit is None
    assert result.estimates == ()
    assert result.median == pytest.approx(0.375)
    assert result.suggested == price_under_median(result.median) == 0.36
    assert result.peer_low == pytest.approx(0.3)
    assert result.peer_high == pytest.approx(0.45)
    assert result.market_usage == 90.3
    assert result.current == 0.6
    assert len(result.choices) == 1
    assert result.choices[0].price == 0.36
    assert result.choices[0].hourly_profit is None


def test_invalid_gemini_answer_uses_the_same_fallback() -> None:
    gemini = Gemini(ValueError("Gemini candidate failed local validation"))
    result = run_cycle(config(), Vast(), gemini, UtilizationHistory(3600))
    assert result.suggested == 0.36
    assert result.rationale is None


def test_gemini_recommendation_is_kept() -> None:
    result = run_cycle(config(), Vast(), Gemini(), UtilizationHistory(3600))
    assert result.suggested == 0.5
    assert result.rationale == "Peer prices"
    assert result.occupancy == 0.9
    assert [choice.price for choice in result.choices] == [0.5, 0.6, 0.7]
    assert result.choices[0].hourly_profit == pytest.approx(0.45)
    assert result.choices[1].hourly_profit == pytest.approx(0.36)
    assert result.choices[2].hourly_profit == pytest.approx(0.21)


def test_market_failure_still_skips_the_suggestion() -> None:
    gemini = Gemini()

    class Empty(Vast):
        def search_peers(self, own: dict[str, Any], config: Config) -> list[Any]:
            return [[]]

    with pytest.raises(ValueError, match="comparable offers"):
        run_cycle(config(), Empty(), gemini, UtilizationHistory(3600))
    assert gemini.calls == 0
