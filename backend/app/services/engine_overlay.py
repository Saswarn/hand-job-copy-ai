import threading
import time

from app.utils.image import Overlay


class EngineOverlay:
    """What the display stream draws over the camera picture for the engine scan. Display only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._overlay: Overlay | None = None
        self._expires: float | None = None

    def set(self, overlay: Overlay | None, keep_s: float | None = None) -> None:
        """Replace the overlay; `keep_s` = drop it after that many seconds, None = until replaced."""
        with self._lock:
            self._overlay = overlay
            self._expires = None if keep_s is None else time.monotonic() + keep_s

    def get(self) -> Overlay | None:
        with self._lock:
            if self._expires is not None and time.monotonic() >= self._expires:
                self._overlay = self._expires = None
            return self._overlay
