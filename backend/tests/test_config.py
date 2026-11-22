import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import CONFIG_PATH, load_config


def write_config(tmp_path: Path, **changes: Any) -> Path:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data.update(changes)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_repo_config_is_valid() -> None:
    cfg = load_config()
    assert len(cfg.uv_gpio) == 3
    assert cfg.camera.crop == (0, 0, 1, 1)


def test_unknown_key_fails(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="scan_secs"):
        load_config(write_config(tmp_path, scan_secs=5))


@pytest.mark.parametrize(
    "changes",
    [
        {"uv_gpio": [17, 27]},  # need exactly 3 channels
        {"dirt_conf": 1.5},
        {"stream": {"min_width": 1366, "quality": 0}},
    ],
)
def test_out_of_range_fails(tmp_path: Path, changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        load_config(write_config(tmp_path, **changes))


def test_empty_crop_fails(tmp_path: Path) -> None:
    camera = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["camera"]
    camera["crop"] = [0.5, 0, 0.5, 1]
    with pytest.raises(ValidationError, match="x1 < x2"):
        load_config(write_config(tmp_path, camera=camera))


def test_csi_rgb_is_optional_and_automatic() -> None:
    csi = load_config().camera.csi_rgb
    assert (csi.exposure_us, csi.colour_gains, csi.size, csi.sensor_mode) == (None, None, None, None)


@pytest.mark.parametrize(
    "csi_rgb,error",
    [
        ({"analogue_gain": 2.0}, "needs exposure_us"),  # auto exposure sets the gain itself
        ({"exposure_us": 0}, "greater than 0"),
        ({"colour_gains": [1.8]}, "colour_gains"),
        ({"size": [1920, 0]}, "greater than 0"),
    ],
)
def test_bad_csi_rgb_fails(tmp_path: Path, csi_rgb: dict[str, Any], error: str) -> None:
    camera = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["camera"]
    camera["csi_rgb"] = csi_rgb
    with pytest.raises(ValidationError, match=error):
        load_config(write_config(tmp_path, camera=camera))
