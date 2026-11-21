import time
from collections.abc import Callable
from threading import Event
from typing import Protocol

from app.schemas.ws import EngineCapture, EnginePreview, EngineResult, Side
from app.services.camera_rgb import ColorFrame
from app.services.latest import Latest

EngineMessage = EnginePreview | EngineCapture | EngineResult


class Engine(Protocol):
    def start_session(self) -> str: ...
    def push_preview(self, frame: ColorFrame) -> object: ...
    def submit_capture(self, frame: ColorFrame, side: Side) -> object: ...
    def finish_session(self) -> object: ...
    def close(self) -> None: ...


EngineFactory = Callable[[], Engine]


def run_engine_scan(
    factory: EngineFactory,
    frames: Latest[ColorFrame],
    seq: int,
    deadline: float,
    stop: Event,
    publish: Callable[[EngineMessage], None],
) -> None:
    """Own one engine session and call it entirely from the same worker thread."""
    if stop.is_set():
        return
    engine = factory()
    try:
        engine.start_session()
        captured: set[Side] = set()
        while len(captured) < 2 and not stop.is_set():
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                break
            seq, frame = frames.wait_newer(seq, min(0.1, time_left))
            if frame is None or stop.is_set():
                continue
            preview = EnginePreview.model_validate(engine.push_preview(frame.preview()), from_attributes=True)
            if stop.is_set() or time.monotonic() >= deadline:
                break
            publish(preview)
            side = preview.side
            if not preview.ready_to_capture or side not in ("palm", "back") or side in captured:
                continue
            capture = EngineCapture.model_validate(engine.submit_capture(frame, side), from_attributes=True)
            if stop.is_set() or time.monotonic() >= deadline:
                break
            if capture.side != side:
                raise ValueError("engine returned a different capture side")
            publish(capture)
            if capture.status == "ok":
                captured.add(side)
        if not stop.is_set() and len(captured) == 2:
            publish(EngineResult.model_validate(engine.finish_session(), from_attributes=True))
    finally:
        engine.close()
