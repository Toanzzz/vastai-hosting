from typing import Any

import pytest

from vastai_hosting.config import Config
from vastai_hosting.gemini import Recommendation
from vastai_hosting.main import run_cycle
from vastai_hosting.pricing import price_under_median
from vastai_hosting.utilization import UtilizationHistory

OWN: dict[str, Any] = {
    "machine_id": 42,
    "gpu_name": "RTX 5090",
    "num_gpus": 1,
    "listed_gpu_cost": 0.6,
}
OFFERS: list[dict[str, Any]] = [
    {
        "machine_id": machine_id,
        "gpu_name": "RTX 5090",
        "num_gpus": 1,
        "rentable": True,
        "verification": "verified",
        "dph_base": price,
    }
    for machine_id, price in enumerate((0.4, 0.5, 0.6), start=1)
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
        min_peers=3,
        offer_limit=100,
        min_price=0.35,
        max_price=0.8,
        min_change=0.01,
        disk_price=0.15,
        upload_price=0.01,
        download_price=0.01,
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
    )


class Vast:
    def __init__(self, offers: list[dict[str, Any]] | None = None) -> None:
        self.offers = OFFERS if offers is None else offers

    def own_machine(self, config: Config) -> dict[str, Any]:
        return OWN

    def own_offers(self, config: Config) -> Any:
        return [{"machine_id": 42, "num_gpus": 1, "dph_base": 0.8}]

    def search_peers(self, own: dict[str, Any], config: Config) -> list[Any]:
        return [self.offers]

    def market_metrics(self, own: dict[str, Any]) -> tuple[Any, Any]:
        return CURRENT, HISTORY


class Gemini:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
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
        )


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
