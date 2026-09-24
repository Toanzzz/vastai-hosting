import json
from dataclasses import replace

import pytest

from vastai_hosting.config import Config
from vastai_hosting.gemini import parse_recommendation


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


def answer(*estimates: tuple[float, float], rationale: str = "Comparable GPUs") -> str:
    candidates = [{"price": price, "occupancy": share} for price, share in estimates]
    return json.dumps({"candidates": candidates, "rationale": rationale})


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
