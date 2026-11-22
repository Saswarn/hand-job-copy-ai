import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Annotated, Protocol

from pydantic import BaseModel, Field

from app.schemas.ws import EngineCapture, EnginePreview, EngineResult, Side
from app.services.camera_rgb import ColorFrame
from app.services.engine_overlay import EngineOverlay
from app.services.latest import Latest
from app.utils.image import Hand, Image, LevelMap, LiveLevels, Overlay, Palette, capture_inset

EngineMessage = EnginePreview | EngineCapture | EngineResult

RESULT_SECONDS = 15.0  # the last capture stays on screen this long after a score (handclean-pi --result-s)


@dataclass(frozen=True)
class EngineReply:
    data: object  # engine result fields, validated by the caller
    levels: LevelMap | None = None  # display-only level map of the same view, None when the engine sent none


class Engine(Protocol):
    @property
    def palette(self) -> Palette: ...
    def start_session(self) -> str: ...
    def push_preview(self, frame: ColorFrame) -> EngineReply: ...
    def submit_capture(self, frame: ColorFrame, side: Side) -> EngineReply: ...
    def finish_session(self) -> object: ...


class EngineHost(Protocol):
    """Keeps one engine alive across scans."""

    def acquire(self) -> Engine: ...  # a running engine, restarted if it died
    def discard(self) -> None: ...  # drop the engine after an error; the next acquire starts a new one


class _Landmarks(BaseModel):
    landmarks: list[Annotated[list[tuple[float, float]], Field(min_length=21, max_length=21)]] | None = None


def _hands(data: object) -> tuple[Hand, ...]:
    return tuple(_Landmarks.model_validate(data, from_attributes=True).landmarks or ())


def run_engine_scan(
    host: EngineHost,
    engine: Engine,
    frames: Latest[ColorFrame],
    seq: int,
    deadline: float,
    stop: Event,
    publish: Callable[[EngineMessage], None],
    overlay: EngineOverlay,
) -> None:
    """Run one session of `engine` (from `host.acquire()`) in the calling worker thread and draw its overlay."""
    if stop.is_set():
        return
    inset: Image | None = None
    scored = False
    try:
        engine.start_session()  # also drops a session left open by a cancelled scan
        captured: set[Side] = set()
        while len(captured) < 2 and not stop.is_set():
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                break
            seq, frame = frames.wait_newer(seq, min(0.1, time_left))
            if frame is None or stop.is_set():
                continue
            small = frame.preview()
            reply = engine.push_preview(small)
            preview = EnginePreview.model_validate(reply.data, from_attributes=True)
            if stop.is_set() or time.monotonic() >= deadline:
                break
            live = None if reply.levels is None else LiveLevels(reply.levels, engine.palette)
            overlay.set(Overlay(live, _hands(reply.data), inset))
            publish(preview)
            side = preview.side
            if not preview.ready_to_capture or side not in ("palm", "back") or side in captured:
                continue
            reply = engine.submit_capture(frame, side)
            capture = EngineCapture.model_validate(reply.data, from_attributes=True)
            if stop.is_set() or time.monotonic() >= deadline:
                break
            if capture.side != side:
                raise ValueError("engine returned a different capture side")
            publish(capture)
            if capture.status == "ok":
                captured.add(side)
                if reply.levels is not None:
                    inset = capture_inset(small.image, reply.levels, engine.palette)
        if not stop.is_set() and len(captured) == 2:
            publish(EngineResult.model_validate(engine.finish_session(), from_attributes=True))
            scored = True
    except BaseException:
        host.discard()
        raise
    finally:
        if scored and inset is not None:
            overlay.set(Overlay(None, (), inset), keep_s=RESULT_SECONDS)
        else:
            overlay.set(None)
