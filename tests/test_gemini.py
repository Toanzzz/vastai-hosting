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
        dry_run=True,
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
    )


def test_recommendation_is_locally_bounded_and_rounded() -> None:
    cfg = config()
    assert parse_recommendation('{"target_price":0.496,"rationale":"Comparable GPUs"}', cfg) == (
        0.5,
        "Comparable GPUs",
    )
    for text in [
        '{"target_price":10,"rationale":"raise it"}',
        '{"target_price":"0.50","rationale":"fine"}',
        '{"target_price":0.5,"rationale":""}',
        '{"target_price":NaN,"rationale":"invalid"}',
        "not JSON",
    ]:
        with pytest.raises(ValueError):
            parse_recommendation(text, cfg)
