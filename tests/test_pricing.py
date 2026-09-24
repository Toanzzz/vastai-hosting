import pytest

from vastai_hosting.config import Config
from vastai_hosting.pricing import (
    host_share,
    listing_changed,
    median_peer_price,
    peers_from_search,
    price_changed,
)


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


def test_peer_filtering_and_median() -> None:
    own = {"gpu_name": "RTX 5090", "num_gpus": 1}

    def offer(machine_id: int, price: float) -> dict:
        return {
            "machine_id": machine_id,
            "gpu_name": "RTX 5090",
            "num_gpus": 1,
            "rentable": True,
            "verification": "verified",
            "dph_base": price,
            "dph_total": 9,
        }

    peers = peers_from_search(
        [
            [offer(42, 0.01), offer(1, 0.42), offer(1, 0.01), offer(2, 0.5)],
            [offer(2, 0.5), offer(3, 0.75)],
        ],
        own,
        config(),
        1.0,
    )
    assert [peer["gpuPrice"] for peer in peers] == [0.42, 0.5, 0.75]
    assert median_peer_price(peers) == 0.5

    earnings = peers_from_search(
        [[offer(1, 0.4), offer(2, 0.8), offer(3, 1.2)]], own, config(), 0.75
    )
    assert [peer["gpuPrice"] for peer in earnings] == pytest.approx([0.3, 0.6, 0.9])

    with pytest.raises(ValueError, match="OFFER_LIMIT"):
        peers_from_search([[offer(i, 0.5) for i in range(1, 101)]], own, config(), 1.0)


def test_host_share_comes_from_the_public_offer() -> None:
    own = {"machine_id": 42, "listed_gpu_cost": 0.46}

    def offer(machine_id: int, base: float, gpus: int = 1) -> dict:
        return {"machine_id": machine_id, "dph_base": base, "num_gpus": gpus}

    assert host_share(own, [offer(42, 0.46 / 0.75)]) == pytest.approx(0.75)
    assert host_share({**own, "listed_gpu_cost": 0.5}, [offer(42, 4 / 3 * 2, 4)]) == pytest.approx(
        0.75
    )
    assert host_share(own, [offer(42, 0.1), offer(42, 0.6)]) == pytest.approx(0.46 / 0.6)
    for offers in ([], [offer(7, 0.6)], [offer(42, 0.2)], [offer(42, 2.0)], {"offers": None}):
        with pytest.raises(ValueError):
            host_share(own, offers)


def test_listing_settings_and_price_threshold() -> None:
    cfg = config()
    now = 2_000_000_000 - 7 * 86_400
    own = {
        "listed_storage_cost": 0.15,
        "listed_inet_up_cost": 0.01,
        "listed_inet_down_cost": 0.01,
        "min_bid_price": 0.3,
        "credit_discount_max": 0.1,
        "listed_volume_cost": 0.15,
        "listed_min_gpu_count": 1,
        "volume_total_size": 200,
        "end_date": 2_000_000_000,
    }
    assert not listing_changed(own, cfg, now)
    assert listing_changed({**own, "listed_volume_cost": 0.2}, cfg, now)
    assert listing_changed(own, cfg, now + 86_401)
    assert listing_changed({**own, "end_date": float("nan")}, cfg, now)
    assert not price_changed(0.5, 0.505, cfg)
    assert price_changed(0.5, 0.52, cfg)
    assert price_changed(0.34, 0.35, cfg)


def test_nonfinite_offer_prices_are_not_used() -> None:
    cfg = config()
    offers = [
        {
            "machine_id": i,
            "gpu_name": "RTX 5090",
            "num_gpus": 1,
            "rentable": True,
            "verification": "verified",
            "dph_base": price,
        }
        for i, price in enumerate([float("nan"), float("inf"), 0.6], 1)
    ]
    with pytest.raises(ValueError, match="only 1 comparable offer"):
        peers_from_search([offers], {"gpu_name": "RTX 5090", "num_gpus": 1}, cfg, 1.0)
