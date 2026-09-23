import pytest

from vastai_hosting.config import DEFAULTS, _integer, _number, _optional_number, load_config


def _only_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("VAST_API_KEY", "GEMINI_API_KEY", *DEFAULTS):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VAST_API_KEY", "vast-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "invalid"])
def test_invalid_numeric_configuration_is_rejected(monkeypatch, value: str) -> None:
    monkeypatch.setenv("MIN_PRICE", value)
    with pytest.raises(ValueError, match="finite nonnegative"):
        _number("MIN_PRICE")


@pytest.mark.parametrize("value", ["0.5", "9007199254740993"])
def test_invalid_integer_configuration_is_rejected(monkeypatch, value: str) -> None:
    monkeypatch.setenv("MACHINE_ID", value)
    with pytest.raises(ValueError, match="safe integer"):
        _integer("MACHINE_ID")


def test_load_config_needs_only_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _only_secrets(monkeypatch)
    config = load_config()
    assert config.vast_api_key == "vast-key"
    assert config.gemini_api_key == "gemini-key"
    assert config.machine_id == 51673
    assert config.gemini_model == "gemini-flash-latest"
    assert config.gemini_thinking_budget == 1024
    assert config.dry_run is True
    assert config.poll_seconds == 86400
    assert config.min_peers == 5
    assert config.offer_limit == 200
    assert config.min_price == 0.40
    assert config.max_price == 1.00
    assert config.min_change == 0.01
    assert config.running_cost == 0.0
    assert config.disk_price == 0.15
    assert config.upload_price == 0.001953125
    assert config.download_price == 0.001953125
    assert config.min_bid_price == 0.40
    assert config.discount_rate == 0.10
    assert config.min_chunk == 1
    assert config.volume_size_gb == 209
    assert config.volume_price == 0.15
    assert config.duration_days == 7


def test_blank_setting_uses_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _only_secrets(monkeypatch)
    monkeypatch.setenv("MACHINE_ID", "  ")
    monkeypatch.setenv("DRY_RUN", "")
    config = load_config()
    assert config.machine_id == 51673
    assert config.dry_run is True


def test_explicit_setting_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _only_secrets(monkeypatch)
    monkeypatch.setenv("MACHINE_ID", "99")
    monkeypatch.setenv("DRY_RUN", "false")
    config = load_config()
    assert config.machine_id == 99
    assert config.dry_run is False


def test_missing_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _only_secrets(monkeypatch)
    monkeypatch.delenv("VAST_API_KEY")
    with pytest.raises(ValueError, match="missing VAST_API_KEY"):
        load_config()


def test_invalid_dry_run_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _only_secrets(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "yes")
    with pytest.raises(ValueError, match="DRY_RUN"):
        load_config()


def test_optional_running_cost_defaults_to_zero(monkeypatch) -> None:
    monkeypatch.delenv("GPU_RUNNING_COST", raising=False)
    assert _optional_number("GPU_RUNNING_COST", 0.0) == 0.0
    monkeypatch.setenv("GPU_RUNNING_COST", "0.08")
    assert _optional_number("GPU_RUNNING_COST", 0.0) == 0.08
    monkeypatch.setenv("GPU_RUNNING_COST", "nan")
    with pytest.raises(ValueError, match="finite nonnegative"):
        _optional_number("GPU_RUNNING_COST", 0.0)
