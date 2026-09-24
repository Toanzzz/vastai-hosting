from pathlib import Path

import pytest

from vastai_hosting.subscribers import Subscribers


def test_subscribers_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "subscribers.json"
    store = Subscribers(path)
    assert store.add(5) is True
    assert store.add(5) is False
    assert store.chats() == (5,)
    assert (path.stat().st_mode & 0o777) == 0o600

    restored = Subscribers(path)
    assert restored.contains(5)
    assert restored.remove(5) is True
    assert restored.contains(5) is False
    assert restored.remove(5) is False


def test_corrupt_subscriber_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "subscribers.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid subscriber file"):
        Subscribers(path)

    path.write_text('{"chats": ["5"]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid subscriber file"):
        Subscribers(path)


@pytest.mark.parametrize("chat_id", [0, -3, True])
def test_invalid_chat_id_is_rejected(tmp_path: Path, chat_id: int) -> None:
    store = Subscribers(tmp_path / "subscribers.json")
    with pytest.raises(ValueError, match="chat id"):
        store.add(chat_id)
