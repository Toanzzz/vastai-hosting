import threading


class CheckGate:
    """One queued manual pricing check, and the cycle epoch it belongs to."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[int] = set()
        self._epoch = 0

    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def request(self, chat_id: int) -> bool:
        """Record a chat waiting on /check. True when this call starts the check."""
        with self._lock:
            first = not self._pending
            if first:
                self._epoch += 1
            self._pending.add(chat_id)
            return first

    def take(self, epoch: int | None) -> tuple[int, ...]:
        """Clear waiters for this cycle. A newer /check keeps its place."""
        with self._lock:
            if epoch is not None and self._epoch != epoch:
                return ()
            chats = tuple(self._pending)
            self._pending.clear()
            return chats
