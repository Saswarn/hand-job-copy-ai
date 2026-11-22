import os
import threading
from pathlib import Path

import pytest

from app.core.config import load_config
from app.services.camera import csi_frames, open_camera


@pytest.mark.skipif(os.name != "posix", reason="rpicam-vid runs on Raspberry Pi OS")
def test_csi_reads_split_frames_and_stops_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HANDCLEAN_ENGINE", raising=False)
    executable = tmp_path / "rpicam-vid"
    executable.write_text(
        "#!/bin/sh\n"
        "printf '\\377\\330one\\377'\n"
        "sleep 0.1\n"
        "printf '\\331\\377\\330two\\377\\331'\n"
        "exec sleep 30\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)

    stop = threading.Event()
    with_frames = csi_frames(1920, 1080, 30, stop)
    try:
        assert next(with_frames) == b"\xff\xd8one\xff\xd9"
        assert next(with_frames) == b"\xff\xd8two\xff\xd9"
        stop.set()
        with pytest.raises(StopIteration):
            next(with_frames)
    finally:
        with_frames.close()

    monkeypatch.setenv("CAMERA_SOURCE", "csi")
    camera = open_camera(load_config().camera)
    camera.start()
    camera.stop()


def test_csi_reports_missing_rpicam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("CAMERA_SOURCE", "csi")
    with pytest.raises(RuntimeError, match="requires rpicam-vid"):
        open_camera(load_config().camera)
