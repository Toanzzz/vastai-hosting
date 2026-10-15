import json
from dataclasses import replace

import pytest

from vastai_hosting.config import Config
from vastai_hosting.gemini import parse_recommendation
from vastai_hosting.rates import Rates


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


RATES = {
    "diskPrice": 0.15,
    "uploadPricePerTb": 4,
    "downloadPricePerTb": 3.5,
    "rationale": "Peer median",
}


def answer(
    *estimates: tuple[float, float], rationale: str = "Comparable GPUs", rates: object = RATES
) -> str:
    candidates = [{"price": price, "occupancy": share} for price, share in estimates]
    return json.dumps({"candidates": candidates, "rates": rates, "rationale": rationale})


def test_rates_are_kept_or_dropped_without_affecting_the_gpu_price() -> None:
    best = parse_recommendation(answer((0.5, 0.8), (0.6, 0.5), (0.7, 0.3)), config(), 0.5)
    assert best.rates == Rates(0.15, 4.0, 3.5)
    assert best.rates_rationale == "Peer median"
    for rates in (None, {**RATES, "diskPrice": 2.0}):
        text = answer((0.5, 0.8), (0.6, 0.5), (0.7, 0.3), rates=rates)
        dropped = parse_recommendation(text, config(), 0.5)
        assert dropped.price == 0.5
        assert dropped.rates is None and dropped.rates_rationale is None


def test_recommendation_maximises_expected_profit() -> None:
    cfg = config()
    text = answer((0.6, 0.5), (0.45, 0.9), (0.496, 0.8), rationale=" Cheapest  comparable ")
    best = parse_recommendation(text, cfg, 0.5)
    assert (best.price, best.occupancy, best.rationale) == (0.45, 0.9, "Cheapest comparable")
    assert best.hourly_profit == pytest.approx(0.405)
    assert best.estimates == ((0.45, 0.9), (0.5, 0.8), (0.6, 0.5))

    costly = parse_recommendation(text, replace(cfg, running_cost=0.1), 0.5)
    assert costly.price == 0.5
    assert costly.hourly_profit == pytest.approx(0.32)


def test_equal_profit_keeps_the_price_closest_to_current() -> None:
    text = answer((0.4, 1.0), (0.5, 0.8), (0.8, 0.5))
    assert parse_recommendation(text, config(), 0.5).price == 0.5
    assert parse_recommendation(text, config(), 0.2).price == 0.4


@pytest.mark.parametrize(
    "text",
    [
        answer((0.4, 0.9), (0.5, 0.95), (0.6, 0.5)),
        answer((0.4, 0.9), (10, 0.1), (0.5, 0.5)),
        answer((0.4, 1.5), (0.5, 0.8), (0.6, 0.5)),
        answer((0.4, 0.9), (0.6, 0.5), (0.7, 0.4)),
        answer((0.5, 0.8), (0.501, 0.8), (0.6, 0.5)),
        answer((0.5, 0.8), (0.6, 0.5)),
        answer((0.5, 0.8), (0.6, 0.5), (0.7, 0.3), rationale=""),
        answer((0.5, 0.8), (0.6, 0.5), (0.7, 0.3)).replace("0.6", '"0.6"'),
        answer((0.5, 0.8), (0.6, 0.5), (0.7, 0.3)).replace("0.6", "NaN"),
        '{"target_price":0.5,"rationale":"old shape"}',
        "not JSON",
    ],
)
def test_invalid_recommendations_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_recommendation(text, config(), 0.5)


def test_current_price_is_only_required_when_listable() -> None:
    text = answer((0.4, 0.9), (0.6, 0.5), (0.7, 0.4))
    assert parse_recommendation(text, config(), 0.2).price == 0.4
