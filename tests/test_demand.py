import pytest

from vastai_hosting.demand import market_demand

NOW = 1_000_000.0
CURRENT = {
    "success": True,
    "gpus": [
        {"gpu_name": "RTX 4090", "usage": 50.0},
        {
            "gpu_name": "RTX 5090",
            "usage": 90.3,
            "usage_30d": 81.5,
            "rented_verified_gpus": 1227,
            "avail_verified_gpus": 132,
            "price_median": 0.46,
        },
    ],
}
HISTORY = {
    "success": True,
    "gpus": {
        "RTX 5090": {
            "supply_demand": {
                "timestamps": [NOW - 12 * 3600, NOW - 6 * 3600, NOW],
                "rented_verified_gpus": [753, 1037, 1227],
                "avail_verified_gpus": [606, 354, 132],
            },
            "pricing": {"avail_median": [0.35, 0.4, 0.57]},
        }
    },
}


def test_market_demand_keeps_unit_free_signals() -> None:
    assert market_demand(CURRENT, HISTORY, "RTX 5090", NOW) == {
        "usagePercent": 90.3,
        "usagePercent30d": 81.5,
        "rentedGpus": 1227,
        "availableGpus": 132,
        "trend": [
            {"hoursAgo": 12, "rentedGpus": 753, "availableGpus": 606},
            {"hoursAgo": 6, "rentedGpus": 1037, "availableGpus": 354},
            {"hoursAgo": 0, "rentedGpus": 1227, "availableGpus": 132},
        ],
    }


@pytest.mark.parametrize(
    ("current", "history", "gpu_name"),
    [
        (CURRENT, HISTORY, "RTX 3090"),
        ({"gpus": [], "needs_machine": True}, HISTORY, "RTX 5090"),
        (CURRENT, {"gpus": {}}, "RTX 5090"),
        (
            CURRENT,
            {
                "gpus": {
                    "RTX 5090": {
                        "supply_demand": {
                            "timestamps": [NOW],
                            "rented_verified_gpus": [1, 2],
                            "avail_verified_gpus": [1],
                        }
                    }
                }
            },
            "RTX 5090",
        ),
        (
            CURRENT,
            {
                "gpus": {
                    "RTX 5090": {
                        "supply_demand": {
                            "timestamps": [NOW],
                            "rented_verified_gpus": [None],
                            "avail_verified_gpus": [1],
                        }
                    }
                }
            },
            "RTX 5090",
        ),
    ],
)
def test_invalid_market_metrics_are_rejected(current, history, gpu_name) -> None:
    with pytest.raises(ValueError):
        market_demand(current, history, gpu_name, NOW)
