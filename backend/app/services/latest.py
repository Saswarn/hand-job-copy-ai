import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class Latest(Generic[T]):
    """Newest value plus a sequence number, shared between threads. Old values are dropped, never queued."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._seq = 0
        self._value: T | None = None

    def put(self, value: T) -> None:
        with self._cond:
            self._seq += 1
            self._value = value
            self._cond.notify_all()

    def sequence(self) -> int:
        with self._cond:
            return self._seq

    def wait_newer(self, seq: int, timeout: float) -> tuple[int, T | None]:
        """Block until a value newer than `seq` arrives. Returns (seq, None) on timeout."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq > seq, timeout):
                return seq, None
            return self._seq, self._value
