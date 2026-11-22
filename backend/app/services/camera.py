import itertools
import logging
import os
import select
import subprocess
import threading
import time
from collections.abc import Generator
from pathlib import Path

import numpy as np
import simplejpeg

from app.core.config import CameraConfig
from app.services.camera_rgb import ColorFrame, csi_rgb_frames
from app.services.latest import Latest
from app.utils.image import Image

Frames = Generator[bytes, None, None]  # JPEG frames; close() releases the device

log = logging.getLogger(__name__)


def v4l2_frames(device: str, width: int, height: int, fps: int) -> Frames:
    """Raw MJPEG frames from a V4L2 camera (Arducam or any USB webcam). Linux only."""
    from linuxpy.video.device import Device, VideoCapture  # pyright: ignore[reportMissingModuleSource]  # Linux only

    with Device(device) as cam:
        capture = VideoCapture(cam)
        capture.set_format(width, height, "MJPG")
        capture.set_fps(fps)
        with capture:
            for frame in capture:
                yield bytes(frame)


def csi_frames(width: int, height: int, fps: int, stop: threading.Event) -> Frames:
    """Read the Raspberry Pi camera through libcamera's rpicam-vid frontend."""
    command = [
        "rpicam-vid", "--camera", "0", "--nopreview", "--timeout", "0",
        "--width", str(width), "--height", str(height), "--framerate", str(fps),
        "--codec", "mjpeg", "--output", "-",
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("CAMERA_SOURCE=csi requires rpicam-vid on PATH") from exc

    with process:
        try:
            assert process.stdout is not None
            fd = process.stdout.fileno()
            pending = bytearray()
            deadline = time.monotonic() + 10
            while not stop.is_set():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Timed out waiting for a JPEG frame from rpicam-vid")
                readable, _, _ = select.select([fd], [], [], 0.2)
                if not readable:
                    continue
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise RuntimeError("rpicam-vid stopped producing frames; check its logs above")
                pending.extend(chunk)
                if len(pending) > 32 * 1024 * 1024:
                    raise RuntimeError("rpicam-vid JPEG frame exceeded 32 MiB")
                # rpicam-vid emits consecutive JPEGs, which can span pipe reads.
                while (end := pending.find(b"\xff\xd9", 2)) != -1:
                    end += 2
                    frame = bytes(pending[:end])
                    del pending[:end]
                    if not frame.startswith(b"\xff\xd8"):
                        raise RuntimeError("rpicam-vid output did not start with a JPEG marker")
                    yield frame
                    deadline = time.monotonic() + 10
                    if stop.is_set():
                        return
        finally:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()


def fake_frames(width: int, height: int, fps: int, folder: str | None) -> Frames:
    """Dev / test stand-in for the camera: loops *.jpg from `folder`, or a generated test pattern."""
    if folder:
        frames = [p.read_bytes() for p in sorted(Path(folder).glob("*.jpg"))]
        if not frames:
            raise RuntimeError(f"CAMERA_FAKE_DIR has no *.jpg: {folder}")
    else:
        frames = [_test_pattern(width, height, i, 30) for i in range(30)]
    for data in itertools.cycle(frames):
        time.sleep(1 / fps)  # ponytail: ignores loop overhead, fine for dev
        yield data


def _test_pattern(width: int, height: int, i: int, n: int) -> bytes:
    return simplejpeg.encode_jpeg(_test_pattern_rgb(width, height, i, n), quality=85)


def _test_pattern_rgb(width: int, height: int, i: int, n: int) -> Image:
    img = np.zeros((height, width, 3), np.uint8)
    img[..., 0] = np.linspace(0, 255, width, dtype=np.uint8)
    img[..., 2] = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
    size = height // 5
    x, y = (width - size) * i // (n - 1), (height - size) // 2
    img[y : y + size, x : x + size] = 255  # square moving left to right
    return img


def fake_rgb_frames(width: int, height: int, fps: int, stop: threading.Event) -> Generator[ColorFrame, None, None]:
    for frame_id in itertools.count(1):
        if stop.wait(1 / fps):
            return
        image = _test_pattern_rgb(width, height, frame_id % 30, 30)
        yield ColorFrame(image, frame_id, time.monotonic_ns() // 1_000_000)


class Camera:
    """Keeps the newest display JPEG and, for a color source, its original frame."""

    def __init__(
        self, frames: Generator[bytes | ColorFrame, None, None], stop: threading.Event | None = None
    ) -> None:
        self.latest: Latest[bytes] = Latest()
        self.latest_color: Latest[ColorFrame] = Latest()
        self._frames = frames
        self._first = next(frames)  # opens the device now, so a missing camera fails at startup
        self._stop = stop if stop is not None else threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            self._publish(self._first)
            for data in self._frames:
                if self._stop.is_set():
                    break
                self._publish(data)
        except Exception:
            log.exception("camera thread stopped")
        finally:
            self._frames.close()

    def _publish(self, data: bytes | ColorFrame) -> None:
        if isinstance(data, ColorFrame):
            self.latest_color.put(data)
            data = simplejpeg.encode_jpeg(data.image, quality=85)
        self.latest.put(data)


def open_camera(cfg: CameraConfig) -> Camera:
    """Select simulated frames, Raspberry Pi CSI capture, or the default V4L2 device."""
    cap = cfg.capture
    stop = threading.Event()
    source = os.environ.get("CAMERA_SOURCE")
    csi_with_engine = source == "csi" and os.environ.get("HANDCLEAN_ENGINE") in ("mock", "real")
    if source == "csi-rgb" or csi_with_engine:
        return Camera(csi_rgb_frames(cfg, stop), stop)
    if source == "fake-rgb":
        return Camera(fake_rgb_frames(cap.width, cap.height, cap.fps, stop), stop)
    if source == "fake":
        frames = fake_frames(cap.width, cap.height, cap.fps, os.environ.get("CAMERA_FAKE_DIR"))
    elif source == "csi":
        frames = csi_frames(cap.width, cap.height, cap.fps, stop)
    else:
        frames = v4l2_frames(cfg.device, cap.width, cap.height, cap.fps)
    return Camera(frames, stop)
