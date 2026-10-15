from pathlib import Path

from vastai_hosting.utilization import UtilizationHistory, occupied


def test_history_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "state" / "occupancy.json"
    history = UtilizationHistory(3600, path=path)
    history.record(1_000_000.0, 0.55, False)
    history.record(1_003_600.0, 0.55, True)
    restored = UtilizationHistory(3600, path=path)
    assert restored.summary() == history.summary()
    assert restored.summary()["rentalsStarted"] == 1


def test_unreadable_history_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "occupancy.json"
    for text in ("not json", '{"observations": [[1, 0.5, "yes"]]}', "[]"):
        path.write_text(text)
        assert UtilizationHistory(3600, path=path).summary()["currentlyOccupied"] is None


def test_state_changes_report_the_previous_state_duration() -> None:
    changes: list[tuple[bool, float, float]] = []
    history = UtilizationHistory(3600, on_change=lambda *change: changes.append(change))
    history.record(0.0, 0.5, False)
    history.record(3600.0, 0.5, False)
    history.record(7200.0, 0.554, True)
    history.record(10_800.0, 0.55, True)
    history.record(18_000.0, 0.55, False)
    assert changes == [(True, 0.55, 2.0), (False, 0.55, 3.0)]


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
