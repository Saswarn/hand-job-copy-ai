import logging
from collections.abc import Generator
from dataclasses import dataclass
from threading import Event
from typing import ClassVar, Literal

import numpy as np

from app.core.config import CameraConfig
from app.utils.image import Image

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColorFrame:
    pixel_format: ClassVar[Literal["RGB"]] = "RGB"
    image: Image  # RGB uint8 in the engine's expected channel order
    frame_id: int
    timestamp_ms: int

    def preview(self) -> "ColorFrame":
        """Fit the engine preview within 640 px without changing the still image."""
        height, width = self.image.shape[:2]
        long_side = max(height, width)
        if long_side <= 640:
            return self
        rows = np.linspace(0, height - 1, max(1, round(height * 640 / long_side)), dtype=np.intp)
        cols = np.linspace(0, width - 1, max(1, round(width * 640 / long_side)), dtype=np.intp)
        return ColorFrame(self.image[rows[:, None], cols], self.frame_id, self.timestamp_ms)


def default_size(sensor: tuple[int, int]) -> tuple[int, int]:
    """1920 wide with the sensor's aspect, height a multiple of 16: the whole field of view (handclean-pi)."""
    width, height = sensor
    return 1920, round(1920 * height / width / 16) * 16


def csi_rgb_frames(cfg: CameraConfig, stop: Event) -> Generator[ColorFrame, None, None]:
    manual = cfg.csi_rgb
    fps = cfg.capture.fps
    frame_us = round(1_000_000 / fps)
    if manual.exposure_us is not None and manual.exposure_us > frame_us:
        raise ValueError("camera.csi_rgb.exposure_us exceeds the capture frame duration")
    controls: dict[str, object] = {"FrameDurationLimits": (frame_us, frame_us)}
    if manual.exposure_us is not None:
        controls.update(
            AeEnable=False,
            ExposureTime=manual.exposure_us,
            AnalogueGain=manual.analogue_gain if manual.analogue_gain is not None else 1.0,
        )
    if manual.colour_gains is not None:
        controls.update(AwbEnable=False, ColourGains=manual.colour_gains)
    try:
        from picamera2 import Picamera2  # pyright: ignore[reportMissingModuleSource]  # Raspberry Pi OS only
    except ImportError as exc:
        raise RuntimeError("CAMERA_SOURCE=csi-rgb requires access to Raspberry Pi OS Picamera2") from exc

    camera = Picamera2()
    try:
        width, height = manual.size or default_size(camera.sensor_resolution)
        # Picamera2's BGR888 format is laid out as RGB bytes in Python.
        config = camera.create_video_configuration(
            main={"size": (width, height), "format": "BGR888"},
            controls=controls,
            sensor={"output_size": manual.sensor_mode} if manual.sensor_mode else {},
            buffer_count=4,
            queue=False,
        )
        camera.configure(config)
        camera.start()
        log.info("CSI RGB camera %dx%d at %d fps, sensor mode %s, exposure %s, white balance %s",
                 width, height, fps, manual.sensor_mode or "auto",
                 "auto" if manual.exposure_us is None else f"{manual.exposure_us} us",
                 "auto" if manual.colour_gains is None else manual.colour_gains)
        frame_id = 0
        while not stop.is_set():
            request = camera.capture_request()
            try:
                image = request.make_array("main")
                timestamp = request.get_metadata().get("SensorTimestamp")
                if type(timestamp) is not int or timestamp < 0:
                    raise RuntimeError("CSI camera did not supply a valid SensorTimestamp")
                if image.dtype != np.uint8 or image.shape != (height, width, 3):
                    raise RuntimeError("CSI camera did not supply the configured RGB frame")
            finally:
                request.release()
            frame_id += 1
            yield ColorFrame(np.ascontiguousarray(image), frame_id, timestamp // 1_000_000)
    finally:
        camera.close()
