from dataclasses import replace

import pytest

from vastai_hosting.config import Config
from vastai_hosting.pricing import peers_from_search
from vastai_hosting.rates import (
    PeerRates,
    Rates,
    listed_rates,
    median_rates,
    parse_rates,
    peer_rates,
    rates_in_bounds,
    same_rates,
)


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


def offer(machine_id: int, storage: float | None, up: float, down: float) -> dict:
    return {
        "machine_id": machine_id,
        "gpu_name": "RTX 5090",
        "num_gpus": 1,
        "rentable": True,
        "verification": "verified",
        "dph_base": 0.6,
        "storage_cost": storage,
        "inet_up_cost": up,
        "inet_down_cost": down,
    }


def test_peer_rates_are_host_earnings_per_tb() -> None:
    own = {"gpu_name": "RTX 5090", "num_gpus": 1}
    peers = peers_from_search(
        [
            [
                offer(1, 0.2, 4 / 1024 / 0.75, 0.0),
                offer(2, None, 8 / 1024 / 0.75, 2 / 1024 / 0.75),
                offer(3, 0.4, 2 / 1024 / 0.75, 4 / 1024 / 0.75),
            ]
        ],
        own,
        config(),
        0.75,
    )
    assert peers[0]["diskPrice"] == pytest.approx(0.15)
    assert peers[0]["uploadPricePerTb"] == pytest.approx(4.0)
    assert peers[1]["diskPrice"] is None
    medians = peer_rates(peers)
    assert medians.disk == pytest.approx(0.225)
    assert medians.upload == pytest.approx(4.0)
    assert medians.download == pytest.approx(2.0)


def test_listed_rates_convert_bandwidth_to_tb() -> None:
    own = {
        "listed_storage_cost": 0.15000000000000002,
        "listed_inet_up_cost": 0.001953125,
        "listed_inet_down_cost": 0.00390625,
    }
    assert listed_rates(own) == Rates(0.15000000000000002, 2.0, 4.0)
    with pytest.raises(ValueError, match="listed_inet_up_cost"):
        listed_rates({**own, "listed_inet_up_cost": None})


def test_median_fallback_rounds_and_keeps_unreported_rates() -> None:
    listed = Rates(0.15, 2.0, 2.0)
    proposal = median_rates(PeerRates(0.225, 3.905, None), listed)
    assert proposal == Rates(0.23, 3.91, 2.0)
    assert same_rates(Rates(0.15000000000000002, 2.0, 2.0), listed)
    assert not same_rates(proposal, listed)


def test_rate_bounds() -> None:
    cfg = config()
    assert rates_in_bounds(Rates(0.15, 2.0, 2.0), cfg)
    assert not rates_in_bounds(Rates(0.5, 2.0, 2.0), cfg)
    assert not rates_in_bounds(Rates(0.15, 0.5, 2.0), cfg)
    assert not rates_in_bounds(Rates(0.15, 2.0, float("nan")), cfg)
    assert rates_in_bounds(Rates(0.15, 0.0, 0.0), replace(cfg, min_bandwidth_price=0.0))


def test_gemini_rates_are_rounded_and_bounded() -> None:
    cfg = config()
    answer = {
        "diskPrice": 0.154,
        "uploadPricePerTb": 3.996,
        "downloadPricePerTb": 4,
        "rationale": " Peers  charge $4/TB ",
    }
    assert parse_rates(answer, cfg) == (Rates(0.15, 4.0, 4.0), "Peers charge $4/TB")
    for bad in (
        None,
        {**answer, "diskPrice": 0.9},
        {**answer, "uploadPricePerTb": -1},
        {**answer, "downloadPricePerTb": "4"},
        {**answer, "rationale": ""},
    ):
        with pytest.raises(ValueError):
            parse_rates(bad, cfg)
