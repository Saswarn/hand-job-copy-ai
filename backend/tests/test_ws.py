import time
from collections.abc import Callable
from threading import Event, get_ident

import numpy as np
import pytest
from fastapi.testclient import TestClient
from gpiozero import Device
from pydantic import TypeAdapter
from starlette.testclient import WebSocketTestSession

from app.core.config import load_config
from app.main import app
from app.schemas.ws import EngineCapture, EnginePreview, EngineResult, Side
from app.services.camera import Camera
from app.services.camera_rgb import ColorFrame
from app.services.detector import Detector
from app.services.engine_overlay import EngineOverlay
from app.services.engine_scan import EngineReply
from app.services.uv import UvService
from app.utils.image import Box, Palette, palette

PINS = load_config().uv_gpio
_json_message = TypeAdapter(dict[str, object])


def pin_states() -> list[bool]:
    factory = Device.pin_factory
    assert factory is not None
    return [bool(factory.pin(p).state) for p in PINS]


def receive_json(ws: WebSocketTestSession) -> dict[str, object]:
    """Next text message, skipping the JPEG frames in between."""
    while True:
        msg = ws.receive()
        text = msg.get("text")
        if isinstance(text, str):
            return _json_message.validate_json(text)


def wait_until(cond: Callable[[], bool], timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_health() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_ws_streams_jpeg_and_controls_uv() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert receive_json(ws) == {"type": "uv", "channels": [False, False, False]}
            assert ws.receive_bytes()[:2] == b"\xff\xd8"  # JPEG frame

            ws.send_json({"type": "uv_set", "channel": 1, "on": True})
            assert receive_json(ws) == {"type": "uv", "channels": [False, True, False]}
            assert pin_states() == [False, True, False]

            ws.send_json({"type": "uv_set", "channel": 5, "on": True})
            assert receive_json(ws)["type"] == "error"
            ws.send_text("not json")
            assert receive_json(ws)["type"] == "error"
            ws.send_json({"type": "binary_view", "on": True})
            assert receive_json(ws)["type"] == "error"

        # frontend gone -> every channel off
        assert wait_until(lambda: pin_states() == [False, False, False])


def test_shutdown_turns_uv_off() -> None:
    with TestClient(app):
        uv: UvService = app.state.uv
        uv.set(0, True)
        assert pin_states()[0]
    assert pin_states() == [False, False, False]


class FixedDetector(Detector):
    def __init__(self, boxes: list[Box]) -> None:
        self.boxes = boxes

    def latest(self) -> list[Box]:
        return list(self.boxes)


class FailingDetector(Detector):
    def latest(self) -> list[Box]:
        raise ValueError("detector failed")


def start_scan(ws: WebSocketTestSession) -> None:
    assert receive_json(ws) == {"type": "uv", "channels": [False, False, False]}
    ws.send_json({"type": "scan_start"})
    assert receive_json(ws) == {"type": "uv", "channels": [True, True, True]}


@pytest.mark.parametrize("dirty", [False, True])
def test_scan_counts_down_and_finishes_with_uv_off(dirty: bool) -> None:
    boxes = [Box("dirt", 0.9, 0.1, 0.1, 0.2, 0.2)] if dirty else []
    with TestClient(app) as client:
        app.state.config = load_config().model_copy(update={"scan_seconds": 1.05})
        app.state.detector = FixedDetector(boxes)
        with client.websocket_connect("/ws") as ws:
            start_scan(ws)
            remaining_values: list[float] = []
            while True:
                message = receive_json(ws)
                if message.get("state") == "done":
                    assert message == {
                        "type": "scan", "state": "done", "remaining_s": None,
                        "result": "dirt" if dirty else "clean",
                    }
                    assert pin_states() == [False, False, False]
                    break
                assert message["state"] == "scanning"
                assert message["result"] is None
                remaining = message["remaining_s"]
                assert isinstance(remaining, (float, int))
                assert 0 <= remaining <= 1.05
                remaining_values.append(float(remaining))
                assert pin_states() == [True, True, True]
            assert len(remaining_values) >= 3
            assert remaining_values == sorted(remaining_values, reverse=True)
            assert receive_json(ws) == {"type": "uv", "channels": [False, False, False]}


def test_disconnect_during_scan_turns_uv_off() -> None:
    with TestClient(app) as client:
        app.state.config = load_config().model_copy(update={"scan_seconds": 10.0})
        app.state.detector = FixedDetector([])
        with client.websocket_connect("/ws") as ws:
            start_scan(ws)
            assert receive_json(ws)["state"] == "scanning"
            assert pin_states() == [True, True, True]
        assert wait_until(lambda: pin_states() == [False, False, False])
        # A cancelled scan must not turn the lights back on later.
        with client.websocket_connect("/ws") as ws:
            assert receive_json(ws) == {"type": "uv", "channels": [False, False, False]}


def test_duplicate_scan_is_rejected_and_other_messages_still_work() -> None:
    with TestClient(app) as client:
        app.state.config = load_config().model_copy(update={"scan_seconds": 0.2})
        with client.websocket_connect("/ws") as ws:
            start_scan(ws)
            assert receive_json(ws)["state"] == "scanning"
            ws.send_json({"type": "scan_start"})
            assert receive_json(ws) == {"type": "error", "message": "scan already in progress"}
            ws.send_json({"type": "uv_set", "channel": 0, "on": False})
            assert receive_json(ws) == {"type": "uv", "channels": [False, True, True]}
            assert receive_json(ws)["state"] == "done"
            assert receive_json(ws) == {"type": "uv", "channels": [False, False, False]}
            # A completed task must not block the next scan.
            ws.send_json({"type": "scan_start"})
            assert receive_json(ws) == {"type": "uv", "channels": [True, True, True]}
            assert receive_json(ws)["state"] == "scanning"


def test_detector_error_turns_uv_off_and_reports_error() -> None:
    with TestClient(app) as client:
        app.state.detector = FailingDetector()
        with client.websocket_connect("/ws") as ws:
            start_scan(ws)
            assert receive_json(ws) == {"type": "error", "message": "scan failed"}
            assert pin_states() == [False, False, False]
            assert receive_json(ws) == {"type": "uv", "channels": [False, False, False]}


class MockScanEngine:
    def __init__(self, *, fail: bool = False, wait: Event | None = None) -> None:
        self.thread_id = 0  # every call of one session must come from the same thread
        self.captures: list[Side] = []
        self.fail = fail
        self.wait = wait
        self.started_preview = Event()

    @property
    def palette(self) -> Palette:
        return palette(["#00e5ff", "#00e65a", "#ffeb00", "#ff8c00", "#ff0040"])

    def start_session(self) -> str:
        self.thread_id = get_ident()
        return "test"

    def push_preview(self, frame: ColorFrame) -> EngineReply:
        assert get_ident() == self.thread_id
        assert frame.pixel_format == "RGB"
        assert frame.image.shape == (320, 640, 3)
        self.started_preview.set()
        if self.wait is not None:
            self.wait.wait(2)
        if self.fail:
            raise ValueError("engine error")
        side = "back" if frame.frame_id >= 4 else "palm"
        preview = EnginePreview(
            frame_id=frame.frame_id, hands_found=2, side=side, stable_ms=1000,
            ready_to_capture=True, needed_sides=[side], guidance="CAPTURE_NOW",
        )
        hands = [[(0.25, 0.5)] * 21, [(0.75, 0.5)] * 21]
        return EngineReply({**preview.model_dump(), "landmarks": hands}, np.ones((32, 64), np.uint8))

    def submit_capture(self, frame: ColorFrame, side: Side) -> EngineReply:
        assert get_ident() == self.thread_id
        assert frame.image.shape == (400, 800, 3)
        self.captures.append(side)
        capture = EngineCapture(
            status="NO_HAND" if len(self.captures) == 1 else "ok", side=side, hands=[],
            warnings=["MASK_FALLBACK"] if side == "back" else [],
        )
        return EngineReply(capture, np.full((32, 64), 3, np.uint8) if capture.status == "ok" else None)

    def finish_session(self) -> EngineResult:
        assert get_ident() == self.thread_id
        return EngineResult(status="ok", S=80, S_worst=70, worst_view="left_back", missing_views=[])


class FactoryHost:
    """EngineHost that makes a new MockScanEngine per scan, in the scan's worker thread."""

    def __init__(self, factory: Callable[[], MockScanEngine]) -> None:
        self.factory = factory
        self.discarded = 0

    def acquire(self) -> MockScanEngine:
        return self.factory()

    def discard(self) -> None:
        self.discarded += 1


def engine_overlay() -> EngineOverlay:
    overlay: EngineOverlay = app.state.engine_overlay
    return overlay


def add_color_frame(frame_id: int) -> None:
    camera: Camera = app.state.camera
    camera.latest_color.put(ColorFrame(np.zeros((400, 800, 3), np.uint8), frame_id, frame_id * 1000))


def start_engine_scan(ws: WebSocketTestSession) -> None:
    assert receive_json(ws)["type"] == "uv"
    ws.send_json({"type": "engine_scan_start"})
    assert receive_json(ws) == {"type": "uv", "channels": [True, True, True]}


def test_engine_scan_retries_capture_and_scores_two_sides() -> None:
    with TestClient(app) as client:
        app.state.engine_host = FactoryHost(MockScanEngine)
        app.state.config = load_config().model_copy(update={"engine_scan_seconds": 2.0})
        add_color_frame(0)  # A frame taken before UV was turned on is stale.
        with client.websocket_connect("/ws") as ws:
            start_engine_scan(ws)
            for frame_id, status, side in [(1, "NO_HAND", "palm"), (2, "ok", "palm"), (3, None, "palm"), (4, "ok", "back")]:
                add_color_frame(frame_id)
                preview = receive_json(ws)
                assert (preview["type"], preview["frame_id"]) == ("engine_preview", frame_id)
                assert "landmarks" not in preview  # drawn on the display frame, not sent
                overlay = engine_overlay().get()
                assert overlay is not None and overlay.live is not None and len(overlay.hands) == 2
                assert (overlay.inset is None) == (frame_id < 3)  # the first ok capture adds the inset
                if status is None:
                    continue
                capture = receive_json(ws)
                assert (capture["type"], capture["status"], capture["side"]) == ("engine_capture", status, side)
                assert capture["warnings"] == (["MASK_FALLBACK"] if side == "back" else [])
            result = receive_json(ws)
            assert (result["type"], result["status"], result["S"]) == ("engine_result", "ok", 80)
            assert pin_states() == [False, False, False]
            # After the score only the last capture stays on screen, for RESULT_SECONDS.
            assert wait_until(lambda: (o := engine_overlay().get()) is not None and o.live is None)
            overlay = engine_overlay().get()
            assert overlay is not None and overlay.inset is not None and overlay.hands == ()


def test_engine_timeout_drops_uv_while_engine_is_blocked() -> None:
    resume = Event()
    entered = Event()
    with TestClient(app) as client:
        app.state.config = load_config().model_copy(update={"engine_scan_seconds": 0.5})

        def factory() -> MockScanEngine:
            engine = MockScanEngine(wait=resume)
            engine.started_preview = entered
            return engine

        app.state.engine_host = FactoryHost(factory)
        with client.websocket_connect("/ws") as ws:
            start_engine_scan(ws)
            add_color_frame(1)
            try:
                assert entered.wait(0.4)
                timeout = receive_json(ws)
                assert (timeout["type"], timeout["status"], timeout["reason"]) == (
                    "engine_result", "incomplete", "timeout",
                )
                assert pin_states() == [False, False, False]
            finally:
                resume.set()
            assert wait_until(lambda: app.state.scan_task.done() is True)
            assert engine_overlay().get() is None


def test_engine_scan_reserves_uv_and_disconnect_aborts_scan() -> None:
    with TestClient(app) as client:
        app.state.engine_host = FactoryHost(MockScanEngine)
        app.state.config = load_config().model_copy(update={"engine_scan_seconds": 5.0})
        with client.websocket_connect("/ws") as observer:
            assert receive_json(observer)["type"] == "uv"
            with client.websocket_connect("/ws") as owner:
                start_engine_scan(owner)
                observer.send_json({"type": "scan_start"})
                assert receive_json(observer)["message"] == "scan already in progress"
                observer.send_json({"type": "uv_set", "channel": 0, "on": False})
                assert receive_json(observer)["message"] == "UV is owned by the engine scan"
            assert wait_until(lambda: pin_states() == [False, False, False])
            assert wait_until(lambda: app.state.scan_task.done() is True)
            assert engine_overlay().get() is None
            observer.send_json({"type": "engine_scan_start"})
            assert receive_json(observer) == {"type": "uv", "channels": [True, True, True]}


def test_engine_error_turns_uv_off() -> None:
    with TestClient(app) as client:
        host = FactoryHost(lambda: MockScanEngine(fail=True))
        app.state.engine_host = host
        with client.websocket_connect("/ws") as ws:
            start_engine_scan(ws)
            add_color_frame(1)
            assert receive_json(ws) == {"type": "error", "message": "engine scan failed"}
            assert pin_states() == [False, False, False]
            assert host.discarded == 1  # a failed engine is replaced on the next scan
            assert engine_overlay().get() is None


def test_only_engine_scan_owner_can_cancel_and_turn_off_uv() -> None:
    with TestClient(app) as client:
        app.state.engine_host = FactoryHost(MockScanEngine)
        with client.websocket_connect("/ws") as owner, client.websocket_connect("/ws") as other:
            assert receive_json(other)["type"] == "uv"
            start_engine_scan(owner)
            other.send_json({"type": "engine_scan_cancel"})
            # A following command confirms that the other client's cancel has been handled.
            other.send_json({"type": "scan_start"})
            assert receive_json(other)["message"] == "scan already in progress"
            assert pin_states() == [True, True, True]
            owner.send_json({"type": "engine_scan_cancel"})
            assert receive_json(owner) == {"type": "uv", "channels": [False, False, False]}
            assert pin_states() == [False, False, False]


def test_engine_scan_is_unavailable_without_engine() -> None:
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert receive_json(ws)["type"] == "uv"
        ws.send_json({"type": "engine_scan_start"})
        assert receive_json(ws) == {"type": "error", "message": "engine scan unavailable"}
        assert pin_states() == [False, False, False]


class LoadingHost(FactoryHost):
    """EngineHost whose engine is still loading until `loaded` is set, or fails to load."""

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(MockScanEngine)
        self.loading = Event()
        self.loaded = Event()
        self.fail = fail

    def acquire(self) -> MockScanEngine:
        self.loading.set()
        self.loaded.wait(2)
        if self.fail:
            raise RuntimeError("engine failed to start")
        return super().acquire()


def test_engine_scan_turns_uv_on_only_after_the_engine_has_loaded() -> None:
    host = LoadingHost()
    with TestClient(app) as client:
        app.state.engine_host = host
        with client.websocket_connect("/ws") as ws:
            assert receive_json(ws)["type"] == "uv"
            ws.send_json({"type": "engine_scan_start"})
            assert host.loading.wait(1)
            time.sleep(0.1)
            assert pin_states() == [False, False, False]  # no UV while the engine loads
            host.loaded.set()
            assert receive_json(ws) == {"type": "uv", "channels": [True, True, True]}
            ws.send_json({"type": "engine_scan_cancel"})
            assert receive_json(ws) == {"type": "uv", "channels": [False, False, False]}


def test_engine_that_fails_to_load_reports_error_without_uv() -> None:
    host = LoadingHost(fail=True)
    host.loaded.set()
    with TestClient(app) as client:
        app.state.engine_host = host
        with client.websocket_connect("/ws") as ws:
            assert receive_json(ws)["type"] == "uv"
            ws.send_json({"type": "engine_scan_start"})
            assert receive_json(ws) == {"type": "error", "message": "engine scan failed"}
            assert receive_json(ws) == {"type": "uv", "channels": [False, False, False]}
            assert pin_states() == [False, False, False]
