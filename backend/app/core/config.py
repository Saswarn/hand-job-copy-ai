import os
import tempfile

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.json"

Unit = Annotated[float, Field(ge=0, le=1)]
Crop = tuple[Unit, Unit, Unit, Unit]  # [x1, y1, x2, y2], 0-1 of the camera frame


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # a typo in config.json fails at startup


class Capture(_Strict):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)


PositiveGain = Annotated[float, Field(gt=0, allow_inf_nan=False)]


Size = tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]]  # width, height


class CsiRgbConfig(_Strict):
    """Picamera2 RGB capture for HandCleanEngine. Unset values stay automatic, as in handclean-pi."""

    exposure_us: int | None = Field(default=None, gt=0)  # set = auto exposure off
    analogue_gain: PositiveGain | None = None  # only with exposure_us; unset = 1.0
    colour_gains: tuple[PositiveGain, PositiveGain] | None = None  # red, blue; set = auto white balance off
    size: Size | None = None  # unset = 1920 wide with the sensor's aspect (HQ IMX477 -> 1920x1440)
    sensor_mode: Size | None = None  # e.g. [2028, 1520] = HQ binned, full field of view; unset = libcamera picks

    @model_validator(mode="after")
    def _gain_needs_exposure(self) -> "CsiRgbConfig":
        if self.analogue_gain is not None and self.exposure_us is None:
            raise ValueError("camera.csi_rgb.analogue_gain needs exposure_us (auto exposure sets the gain)")
        return self


class CameraConfig(_Strict):
    device: str
    capture: Capture
    exposure: int
    gain: int
    white_balance: int
    zoom: float = Field(gt=0)
    crop: Crop
    csi_rgb: CsiRgbConfig = CsiRgbConfig()

    @model_validator(mode="after")
    def _crop_not_empty(self) -> "CameraConfig":
        x1, y1, x2, y2 = self.crop
        if x1 >= x2 or y1 >= y2:
            raise ValueError("camera.crop needs x1 < x2 and y1 < y2")
        return self


class StreamConfig(_Strict):
    min_width: int = Field(gt=0)
    quality: int = Field(ge=1, le=100)


class AppConfig(_Strict):
    uv_gpio: tuple[int, int, int]  # BCM pins, UV channels 0-2
    camera: CameraConfig
    binary_threshold: int = Field(ge=0, le=255)
    stream: StreamConfig
    scan_seconds: float = Field(gt=0)
    engine_scan_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    dirt_conf: Unit
    dirt_min_frames: int = Field(ge=1)



def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    return AppConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_config(config: AppConfig, path: Path) -> None:
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(config.model_dump_json(indent=2))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
