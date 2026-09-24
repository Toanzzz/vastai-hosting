import pytest

from vastai_hosting.config import DEFAULTS, _integer, _number, _optional_number, load_config


def _only_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "VAST_API_KEY",
        "GEMINI_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_SUBSCRIBE_SECRET",
        *DEFAULTS,
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VAST_API_KEY", "vast-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC_def")
    monkeypatch.setenv("TELEGRAM_SUBSCRIBE_SECRET", "join-code")


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
    assert config.telegram_bot_token == "123456:ABC_def"
    assert config.telegram_subscribe_secret == "join-code"
    assert config.telegram_state_path == "data/subscribers.json"
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
    monkeypatch.setenv("TELEGRAM_STATE_PATH", "")
    config = load_config()
    assert config.machine_id == 51673
    assert config.telegram_state_path == "data/subscribers.json"


def test_explicit_setting_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _only_secrets(monkeypatch)
    monkeypatch.setenv("MACHINE_ID", "99")
    monkeypatch.setenv("TELEGRAM_STATE_PATH", "state/subscribers.json")
    config = load_config()
    assert config.machine_id == 99
    assert config.telegram_state_path == "state/subscribers.json"


@pytest.mark.parametrize(
    "key",
    ["VAST_API_KEY", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_SUBSCRIBE_SECRET"],
)
def test_missing_secret_is_rejected(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    _only_secrets(monkeypatch)
    monkeypatch.delenv(key)
    with pytest.raises(ValueError, match=f"missing {key}"):
        load_config()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("TELEGRAM_BOT_TOKEN", "not-a-token"),
        ("TELEGRAM_BOT_TOKEN", "123:bad token"),
        ("TELEGRAM_SUBSCRIBE_SECRET", "has space"),
        ("TELEGRAM_SUBSCRIBE_SECRET", "a" * 65),
        ("TELEGRAM_SUBSCRIBE_SECRET", "bad/char"),
        ("TELEGRAM_STATE_PATH", "data/\nbad"),
    ],
)
def test_invalid_telegram_setting_is_rejected(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    _only_secrets(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        load_config()


def test_optional_running_cost_defaults_to_zero(monkeypatch) -> None:
    monkeypatch.delenv("GPU_RUNNING_COST", raising=False)
    assert _optional_number("GPU_RUNNING_COST", 0.0) == 0.0
    monkeypatch.setenv("GPU_RUNNING_COST", "0.08")
    assert _optional_number("GPU_RUNNING_COST", 0.0) == 0.08
    monkeypatch.setenv("GPU_RUNNING_COST", "nan")
    with pytest.raises(ValueError, match="finite nonnegative"):
        _optional_number("GPU_RUNNING_COST", 0.0)
