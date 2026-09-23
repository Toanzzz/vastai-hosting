from vastai_hosting.utilization import UtilizationHistory, occupied


def test_occupancy_reads_on_demand_rentals() -> None:
    assert occupied({"current_rentals_running_on_demand": 1}) is True
    assert occupied({"current_rentals_running_on_demand": 0}) is False
    assert occupied({"current_rentals_running_on_demand": True}) is None
    assert occupied({"current_rentals_on_demand": 2}) is None


def test_history_attributes_intervals_to_the_listed_price() -> None:
    history = UtilizationHistory(max_gap_seconds=3600)
    start = 1_000_000.0
    assert history.summary()["currentlyOccupied"] is None

    history.record(start, 0.6, False)
    history.record(start + 1800, 0.6, False)
    history.record(start + 3600, 0.554, True)
    history.record(start + 5400, 0.55, True)
    history.record(start + 5400 + 3 * 3600, 0.55, True)

    assert history.summary() == {
        "observedHours": 2.5,
        "occupiedFraction": 0.6,
        "currentlyOccupied": True,
        "hoursInCurrentState": 3.5,
        "rentalsStarted": 1,
        "byPrice": [
            {"gpuPrice": 0.55, "idleHours": 0.5, "occupiedHours": 1.5, "rentalsStarted": 1},
            {"gpuPrice": 0.6, "idleHours": 0.5, "occupiedHours": 0.0, "rentalsStarted": 0},
        ],
    }


def test_history_keeps_a_rolling_window() -> None:
    history = UtilizationHistory(max_gap_seconds=3600)
    history.record(0.0, 0.5, True)
    history.record(8 * 86_400, 0.5, False)
    summary = history.summary()
    assert summary["observedHours"] == 0
    assert summary["occupiedFraction"] is None
    assert summary["currentlyOccupied"] is False
    assert summary["byPrice"] == []
