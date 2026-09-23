import pytest

from vastai_hosting.config import _integer, _number


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
