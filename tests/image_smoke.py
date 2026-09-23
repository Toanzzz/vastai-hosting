"""Exercise the pinned SDK call paths without contacting external services.

Run locally with `uv run python tests/image_smoke.py`, or pipe this file into
`docker run -i --entrypoint python vastai-hosting -`.
"""

import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from httpx import Client, Request, Response
from vastai.api.client import VastClient

from vastai_hosting.config import Config
from vastai_hosting.gemini import GeminiService
from vastai_hosting.main import run_cycle
from vastai_hosting.vast import VastService

config = Config(
    machine_id=42,
    vast_api_key="test-vast-key",
    gemini_api_key="test-gemini-key",
    gemini_model="gemini-flash-latest",
    gemini_thinking_budget=1024,
    dry_run=False,
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

own = {
    "machine_id": 42,
    "gpu_name": "RTX 5090",
    "num_gpus": 1,
    "listed": True,
    "listed_gpu_cost": 0.6,
    "listed_storage_cost": 0.15,
    "listed_inet_up_cost": 0.01,
    "listed_inet_down_cost": 0.01,
    "min_bid_price": 0.3,
    "credit_discount_max": 0.1,
    "listed_volume_cost": 0.15,
    "listed_min_gpu_count": 1,
    "volume_total_size": 200,
    "end_date": time.time() + 7 * 86400,
}
offers = [
    {
        "machine_id": i,
        "gpu_name": "RTX 5090",
        "num_gpus": 1,
        "rentable": True,
        "verification": "verified",
        "dph_base": 0.5,
    }
    for i in range(1, 4)
]
requests = []
recommended_text = '{"target_price":0.5,"rationale":"Peer prices"}'


def fake_request(self, method, url, headers, json_data=None, timeout=None):
    requests.append((method, url, json_data))
    if url.endswith("/machines?owner=me"):
        result = {"machines": [own]}
    elif url.endswith("/bundles/"):
        assert method == "POST"
        assert isinstance(json_data, dict)
        assert json_data["gpu_name"] == {"eq": "RTX 5090"}
        assert json_data["num_gpus"] == {"eq": "1"}
        assert json_data["order"] == [["dph_total", "asc"]]
        result = {"offers": offers}
    elif url.endswith("/machines/create_asks/"):
        assert method == "PUT"
        assert isinstance(json_data, dict)
        assert json_data["machine"] == 42
        assert json_data["price_gpu"] == 0.5
        assert json_data["price_min_bid"] == 0.3
        assert json_data["duration"] == "7 days"
        assert json_data["vol_size"] == 200
        result = {"success": True}
    else:
        raise AssertionError(f"unexpected SDK request: {method} {url}")
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: result)


def fake_google_send(self, request: Request, **kwargs):
    assert request.url.path.endswith("/models/gemini-flash-latest:generateContent")
    assert request.headers["x-goog-api-key"] == "test-gemini-key"
    body = json.loads(request.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"]["required"] == [
        "target_price",
        "rationale",
    ]
    assert body["generationConfig"]["thinkingConfig"]["thinking_budget"] == 1024
    assert '"medianGpuPrice":0.5' in body["contents"][0]["parts"][0]["text"]
    return Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": recommended_text}], "role": "model"}}]
        },
        request=request,
    )


with (
    patch.object(VastClient, "_request", fake_request),
    patch.object(Client, "send", fake_google_send),
):
    run_cycle(config, VastService(config), GeminiService(config))
    assert [method for method, _, _ in requests] == ["GET", "POST", "PUT"]

    requests.clear()
    dry_run = replace(config, dry_run=True)
    run_cycle(dry_run, VastService(dry_run), GeminiService(dry_run))
    assert [method for method, _, _ in requests] == ["GET", "POST"]

    requests.clear()
    recommended_text = '{"target_price":9,"rationale":"Invalid price"}'
    try:
        run_cycle(config, VastService(config), GeminiService(config))
    except ValueError as error:
        assert "local validation" in str(error)
    else:
        raise AssertionError("invalid recommendation was accepted")
    assert [method for method, _, _ in requests] == ["GET", "POST"]

    requests.clear()
    offers.clear()
    try:
        run_cycle(config, VastService(config), GeminiService(config))
    except ValueError as error:
        assert "comparable offers" in str(error)
    else:
        raise AssertionError("empty market was accepted")
    assert [method for method, _, _ in requests] == ["GET", "POST"]

print("SDK cycle smoke passed")
