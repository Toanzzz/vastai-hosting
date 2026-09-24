import json
import os
import threading
from pathlib import Path


class Subscribers:
    """Private Telegram chats that receive status and may apply a price."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._chats: set[int] = set()
        self._lock = threading.Lock()
        self._load()

    def add(self, chat_id: int) -> bool:
        self._check(chat_id)
        with self._lock:
            if chat_id in self._chats:
                return False
            self._chats.add(chat_id)
            try:
                self._save()
            except Exception:
                self._chats.remove(chat_id)
                raise
            return True

    def remove(self, chat_id: int) -> bool:
        with self._lock:
            if chat_id not in self._chats:
                return False
            self._chats.remove(chat_id)
            try:
                self._save()
            except Exception:
                self._chats.add(chat_id)
                raise
            return True

    def contains(self, chat_id: int) -> bool:
        with self._lock:
            return chat_id in self._chats

    def chats(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(sorted(self._chats))

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid subscriber file {self._path}") from error
        chats = data.get("chats") if isinstance(data, dict) else None
        if not isinstance(chats, list) or any(not self._valid(chat_id) for chat_id in chats):
            raise ValueError(f"invalid subscriber file {self._path}")
        self._chats = set(chats)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        payload = json.dumps({"chats": sorted(self._chats)}) + "\n"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)

    @staticmethod
    def _valid(chat_id: object) -> bool:
        return isinstance(chat_id, int) and not isinstance(chat_id, bool) and chat_id > 0

    def _check(self, chat_id: int) -> None:
        if not self._valid(chat_id):
            raise ValueError("invalid telegram chat id")
