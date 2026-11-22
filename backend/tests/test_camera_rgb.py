import sys
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import Capture, CsiRgbConfig, load_config
from app.services.camera import open_camera
from app.services.camera_rgb import csi_rgb_frames
from app.utils.image import Image


class FakeRequest:
    def __init__(self, image: Image, timestamp: object) -> None:
        self.image = image
        self.timestamp = timestamp
        self.released = False

    def make_array(self, name: str) -> Image:
        assert name == "main"
        return self.image

    def get_metadata(self) -> dict[str, object]:
        return {"SensorTimestamp": self.timestamp}

    def release(self) -> None:
        self.released = True


class FakePicamera:
    def __init__(self, image: Image, timestamp: object) -> None:
        self.request = FakeRequest(image, timestamp)
        self.config: dict[str, object] = {}
        self.closed = False

    def create_video_configuration(
        self, *, main: dict[str, object], controls: dict[str, object], queue: bool
    ) -> dict[str, object]:
        return {"main": main, "controls": controls, "queue": queue}

    def configure(self, config: dict[str, object]) -> None:
        self.config = config

    def start(self) -> None:
        pass

    def capture_request(self) -> FakeRequest:
        return self.request

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("source,engine", [("csi-rgb", "real"), ("csi", "real"), ("csi", "mock")])
def test_csi_rgb_camera_keeps_original_and_provides_preview(
    monkeypatch: pytest.MonkeyPatch, source: str, engine: str
) -> None:
    image = np.empty((400, 800, 3), dtype=np.uint8)
    image[:] = (230, 90, 10)
    fake = FakePicamera(image, 12_345_678_900)
    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=lambda: fake))
    monkeypatch.setenv("CAMERA_SOURCE", source)
    monkeypatch.setenv("HANDCLEAN_ENGINE", engine)
    cfg = load_config().camera.model_copy(update={
        "capture": Capture(width=800, height=400, fps=25),
        "csi_rgb": CsiRgbConfig(exposure_us=10_000, analogue_gain=2.0, colour_gains=(1.8, 1.2)),
    })
    camera = open_camera(cfg)
    camera.start()
    try:
        _, frame = camera.latest_color.wait_newer(0, timeout=1)
        _, jpeg = camera.latest.wait_newer(0, timeout=1)
        assert frame is not None and jpeg is not None
        assert frame.timestamp_ms == 12_345
        assert frame.image is image
        preview = frame.preview()
        assert preview.image.shape == (320, 640, 3)
        assert preview.image[0, 0].tolist() == [230, 90, 10]
        assert (preview.frame_id, preview.timestamp_ms) == (frame.frame_id, frame.timestamp_ms)
        assert jpeg.startswith(b"\xff\xd8")
    finally:
        camera.stop()
    assert fake.request.released and fake.closed
    assert fake.config == {
        "main": {"size": (800, 400), "format": "BGR888"},
        "controls": {
            "AeEnable": False, "AwbEnable": False, "ExposureTime": 10_000,
            "AnalogueGain": 2.0, "ColourGains": (1.8, 1.2), "FrameDurationLimits": (40_000, 40_000),
        },
        "queue": False,
    }


@pytest.mark.parametrize("timestamp,shape,error", [
    (None, (8, 8, 3), "SensorTimestamp"),
    (1, (8, 8, 4), "configured RGB frame"),
])
def test_csi_rgb_releases_failed_capture(
    monkeypatch: pytest.MonkeyPatch, timestamp: object, shape: tuple[int, int, int], error: str
) -> None:
    fake = FakePicamera(np.zeros(shape, dtype=np.uint8), timestamp)
    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=lambda: fake))
    cfg = load_config().camera.model_copy(update={
        "capture": Capture(width=8, height=8, fps=25),
        "csi_rgb": CsiRgbConfig(exposure_us=10_000, analogue_gain=1.0, colour_gains=(1.0, 1.0)),
    })
    with pytest.raises(RuntimeError, match=error):
        next(csi_rgb_frames(cfg, Event()))
    assert fake.request.released and fake.closed


def test_csi_rgb_requires_manual_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMERA_SOURCE", "csi-rgb")
    cfg = load_config().camera.model_copy(update={"csi_rgb": None})
    with pytest.raises(ValueError, match="requires camera.csi_rgb"):
        open_camera(cfg)
