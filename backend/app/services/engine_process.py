import json
import os
import subprocess
import sys
from contextlib import suppress
from functools import partial
from pathlib import Path
from threading import Timer
from typing import IO, Literal

import numpy as np

from app.services.camera_rgb import ColorFrame
from app.services.engine_scan import EngineFactory
from app.schemas.ws import Side


Mode = Literal["mock", "real"]
ENGINE_DIR = Path(__file__).resolve().parents[2] / "engine-check"


class EngineProcess:
    """Send RGB frames to an isolated Python process for one scan session."""

    def __init__(self, python: Path, mode: Mode) -> None:
        self._process: subprocess.Popen[bytes] = subprocess.Popen(
            [str(python), "-I", str(ENGINE_DIR / "worker.py"), mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        self._input: IO[bytes] = self._process.stdin
        self._output: IO[bytes] = self._process.stdout

    def _call(self, command: str, frame: ColorFrame | None = None, side: Side | None = None) -> object:
        request: dict[str, object] = {"command": command}
        if frame is not None:
            request.update(shape=frame.image.shape, frame_id=frame.frame_id, timestamp_ms=frame.timestamp_ms)
        if side is not None:
            request["side"] = side
        watchdog = Timer(15, self._process.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            self._input.write(json.dumps(request).encode("ascii") + b"\n")
            if frame is not None:
                self._input.write(np.ascontiguousarray(frame.image).data.cast("B"))
            self._input.flush()
            response = self._output.readline()
            if not response:
                raise RuntimeError(f"engine subprocess stopped during {command}")
            result: object = json.loads(response)
            return result
        finally:
            watchdog.cancel()
            watchdog.join()

    def start_session(self) -> str:
        result = self._call("start_session")
        if not isinstance(result, str):
            raise ValueError("invalid engine session ID")
        return result

    def push_preview(self, frame: ColorFrame) -> object:
        return self._call("push_preview", frame)

    def submit_capture(self, frame: ColorFrame, side: Side) -> object:
        return self._call("submit_capture", frame, side)

    def finish_session(self) -> object:
        return self._call("finish_session")

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


def configure_engine_factory() -> EngineFactory | None:
    mode = os.environ.get("HANDCLEAN_ENGINE")
    if mode is None:
        return None
    if mode not in ("mock", "real"):
        raise ValueError("HANDCLEAN_ENGINE must be mock or real")
    if os.environ.get("CAMERA_SOURCE") not in ("csi-rgb", "fake-rgb"):
        raise ValueError("HANDCLEAN_ENGINE requires CAMERA_SOURCE=csi-rgb or fake-rgb")
    python = ENGINE_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        raise RuntimeError("Install HandCleanEngine with systems/scripts/setup-engine.sh")
    return partial(EngineProcess, python, mode)
