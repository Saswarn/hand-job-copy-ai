import json
import logging
import math
import os
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import IO, Annotated, Literal

import numpy as np
from pydantic import BaseModel, Field

from app.services.camera_rgb import ColorFrame
from app.services.engine_scan import EngineReply
from app.schemas.ws import Side
from app.utils.image import Palette, palette


Mode = Literal["mock", "real"]
ENGINE_DIR = Path(__file__).resolve().parents[2] / "engine-check"
STARTUP_TIMEOUT_S = 60.0  # loading MediaPipe + the residue lookup table; 4.8 s on a laptop, Pi not measured yet
CALL_TIMEOUT_S = 15.0  # longest wait for one engine call before the worker is killed

log = logging.getLogger(__name__)

Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class _Ready(BaseModel):
    type: Literal["ready"]
    mask_backend: str
    startup_ms: int
    level_colors: Annotated[list[Color], Field(min_length=5, max_length=5)]


class _LevelsHeader(BaseModel):
    levels_shape: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]] | None = None


def _levels_shape(data: object) -> tuple[int, int] | None:
    """Shape of the level map that follows a reply line; only object replies can carry one."""
    if not isinstance(data, dict):
        return None
    return _LevelsHeader.model_validate(data).levels_shape


class EngineProcess:
    """HandCleanEngine in its own Python process (engine-check/worker.py), fed RGB frames over a pipe."""

    def __init__(self, python: Path, mode: Mode) -> None:
        self._process: subprocess.Popen[bytes] = subprocess.Popen(
            [str(python), "-I", str(ENGINE_DIR / "worker.py"), mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=ENGINE_DIR,  # engine.toml paths are relative to it
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        self._input: IO[bytes] = self._process.stdin
        self._output: IO[bytes] = self._process.stdout
        try:
            ready = _Ready.model_validate(self._read("startup", STARTUP_TIMEOUT_S).data)
        except BaseException:
            self.close()
            raise
        self._palette = palette(ready.level_colors)
        log.info("HandCleanEngine %s ready in %d ms, mask backend %s", mode, ready.startup_ms, ready.mask_backend)

    @property
    def palette(self) -> Palette:
        return self._palette

    @property
    def pid(self) -> int:
        return self._process.pid

    def alive(self) -> bool:
        return self._process.poll() is None

    def _read(self, command: str, timeout: float) -> EngineReply:
        watchdog = threading.Timer(timeout, self._process.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            line = self._output.readline()
            if not line:
                raise RuntimeError(f"engine subprocess stopped during {command}")
            data: object = json.loads(line)
            shape = _levels_shape(data)
            if shape is None:
                return EngineReply(data)
            payload = self._output.read(math.prod(shape))
            if len(payload) != math.prod(shape):
                raise RuntimeError(f"engine level map was truncated during {command}")
            levels = np.frombuffer(payload, dtype=np.uint8).reshape(shape)
            if int(levels.max(initial=0)) > 7:
                raise ValueError("engine level map has values above 7")
            return EngineReply(data, levels)
        finally:
            watchdog.cancel()
            watchdog.join()

    def _call(self, command: str, frame: ColorFrame | None = None, side: Side | None = None) -> EngineReply:
        request: dict[str, object] = {"command": command}
        if frame is not None:
            request.update(shape=frame.image.shape, frame_id=frame.frame_id, timestamp_ms=frame.timestamp_ms)
        if side is not None:
            request["side"] = side
        self._input.write(json.dumps(request).encode("ascii") + b"\n")
        if frame is not None:
            self._input.write(np.ascontiguousarray(frame.image).data.cast("B"))
        self._input.flush()
        return self._read(command, CALL_TIMEOUT_S)

    def start_session(self) -> str:
        result = self._call("start_session").data
        if not isinstance(result, str):
            raise ValueError("invalid engine session ID")
        return result

    def push_preview(self, frame: ColorFrame) -> EngineReply:
        return self._call("push_preview", frame)

    def submit_capture(self, frame: ColorFrame, side: Side) -> EngineReply:
        return self._call("submit_capture", frame, side)

    def finish_session(self) -> object:
        return self._call("finish_session").data

    def close(self) -> None:
        try:
            with suppress(BrokenPipeError):
                self._input.close()
        finally:
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            finally:
                self._output.close()


class EngineWorker:
    """One EngineProcess shared by every scan: loaded once, restarted when it dies (EngineHost)."""

    def __init__(self, python: Path, mode: Mode) -> None:
        self._python = python
        self._mode: Mode = mode
        self._lock = threading.Lock()
        self._process: EngineProcess | None = None
        self._closed = False

    def start(self) -> None:
        """Load the engine in the background so the backend serves the kiosk while MediaPipe loads."""
        threading.Thread(target=self._warm_up, name="engine-start", daemon=True).start()

    def _warm_up(self) -> None:
        try:
            self.acquire()
        except Exception:
            log.exception("HandCleanEngine failed to start; the next engine scan tries again")

    def acquire(self) -> EngineProcess:
        with self._lock:
            if self._closed:
                raise RuntimeError("engine worker is closed")
            if self._process is not None and not self._process.alive():
                log.warning("HandCleanEngine worker stopped; starting a new one")
                self._process.close()
                self._process = None
            if self._process is None:
                self._process = EngineProcess(self._python, self._mode)
            return self._process

    def discard(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is not None:
            process.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.discard()


def configure_engine_worker() -> EngineWorker | None:
    mode = os.environ.get("HANDCLEAN_ENGINE")
    if mode is None:
        return None
    if mode not in ("mock", "real"):
        raise ValueError("HANDCLEAN_ENGINE must be mock or real")
    if os.environ.get("CAMERA_SOURCE") not in ("csi", "csi-rgb", "fake-rgb"):
        raise ValueError("HANDCLEAN_ENGINE requires CAMERA_SOURCE=csi, csi-rgb, or fake-rgb")
    python = ENGINE_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        raise RuntimeError("Install HandCleanEngine with systems/scripts/setup-engine.sh")
    return EngineWorker(python, mode)
