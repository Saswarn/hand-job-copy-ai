import logging
import threading

from app.core.config import Crop, StreamConfig
from app.services.camera import Camera
from app.services.detector import Detector
from app.services.engine_overlay import EngineOverlay
from app.services.latest import Latest
from app.utils.image import render_frame

log = logging.getLogger(__name__)


class Stream:
    """Display frames in their own thread: always renders the newest camera frame, skips the rest."""

    def __init__(
        self, camera: Camera, detector: Detector, crop: Crop, cfg: StreamConfig, overlay: EngineOverlay
    ) -> None:
        self.latest: Latest[bytes] = Latest()
        self._camera = camera
        self._detector = detector
        self._crop = crop
        self._cfg = cfg
        self._overlay = overlay
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="stream", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        seq = 0
        while not self._stop.is_set():
            seq, jpeg = self._camera.latest.wait_newer(seq, timeout=0.5)
            if jpeg is None:
                continue
            try:
                frame = render_frame(
                    jpeg, self._crop, self._cfg.min_width, self._detector.latest(), self._cfg.quality,
                    self._overlay.get(),
                )
            except Exception:
                log.exception("render failed, frame skipped")  # e.g. a corrupt JPEG from the camera
                continue
            self.latest.put(frame)
