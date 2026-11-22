import logging
import os
import signal
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.main import app
from app.schemas.ws import EngineCapture, EnginePreview, EngineResult
from app.services.camera_rgb import ColorFrame
from app.services.engine_overlay import EngineOverlay
from app.services.engine_process import ENGINE_DIR, EngineProcess, EngineWorker, configure_engine_worker
from tests.test_ws import wait_until


ENGINE_PYTHON = ENGINE_DIR / ".venv" / "bin" / "python"
requires_engine = pytest.mark.skipif(
    not ENGINE_PYTHON.is_file(), reason="run systems/scripts/setup-engine.sh first"
)


def test_engine_rejects_jpeg_camera(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMERA_SOURCE", "fake")
    monkeypatch.setenv("HANDCLEAN_ENGINE", "real")
    with pytest.raises(ValueError, match="requires CAMERA_SOURCE"):
        configure_engine_worker()


@requires_engine
def test_engine_worker_accepts_csi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMERA_SOURCE", "csi")
    monkeypatch.setenv("HANDCLEAN_ENGINE", "real")
    assert configure_engine_worker() is not None


@requires_engine
def test_real_engine_uses_cpp_grabcut_and_posts_valid_results(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.services.engine_process")
    engine = EngineProcess(Path(ENGINE_PYTHON), "real")
    frame = ColorFrame(np.zeros((240, 320, 3), np.uint8), frame_id=7, timestamp_ms=123)
    try:
        assert "mask backend grabcut_cpp" in caplog.text  # engine.toml found the C++ GrabCut
        assert engine.palette[1].tolist() == [0x00, 0xE5, 0xFF]
        assert engine.start_session()
        reply = engine.push_preview(frame)
        preview = EnginePreview.model_validate(reply.data)
        assert preview.frame_id == 7 and preview.hands_found == 0
        assert preview.focus == 0  # a black frame has no edges
        assert reply.levels is None  # no hands, no live residue
        reply = engine.submit_capture(frame, "palm")
        capture = EngineCapture.model_validate(reply.data)
        assert capture.status == "NO_HAND" and reply.levels is None
        assert capture.warnings == ["MASK_FALLBACK"]
        result = EngineResult.model_validate(engine.finish_session())
        assert result.status == "incomplete" and len(result.missing_views) == 4
    finally:
        engine.close()


@requires_engine
def test_mock_engine_returns_capture_level_map_for_display() -> None:
    engine = EngineProcess(Path(ENGINE_PYTHON), "mock")
    frame = ColorFrame(np.zeros((1440, 1920, 3), np.uint8), frame_id=1, timestamp_ms=0)
    try:
        engine.start_session()
        reply = engine.submit_capture(frame, "palm")
        assert EngineCapture.model_validate(reply.data).status == "ok"
        assert reply.levels is not None and max(reply.levels.shape) <= 640
    finally:
        engine.close()


@requires_engine
def test_engine_worker_keeps_one_process_and_restarts_a_dead_one() -> None:
    worker = EngineWorker(Path(ENGINE_PYTHON), "mock")
    try:
        first = worker.acquire()
        assert worker.acquire() is first
        os.kill(first.pid, signal.SIGKILL)
        assert wait_until(lambda: not first.alive())
        second = worker.acquire()
        assert second is not first and second.alive()
        assert second.start_session()
        worker.discard()
        assert not second.alive()
    finally:
        worker.close()
    with pytest.raises(RuntimeError, match="closed"):
        worker.acquire()


@requires_engine
def test_mock_engine_worker_completes_two_scans_over_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMERA_SOURCE", "fake-rgb")
    monkeypatch.setenv("HANDCLEAN_ENGINE", "mock")
    messages = TypeAdapter(dict[str, object])
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "uv"
        worker: EngineWorker = app.state.engine_host
        overlay: EngineOverlay = app.state.engine_overlay
        process = worker.acquire()
        for _ in range(2):
            captures: list[EngineCapture] = []
            ws.send_json({"type": "engine_scan_start"})
            while True:
                text = ws.receive().get("text")
                if not isinstance(text, str):
                    continue
                message = messages.validate_json(text)
                assert message["type"] != "error", message
                if message["type"] == "engine_capture":
                    captures.append(EngineCapture.model_validate(message))
                if message["type"] == "engine_result":
                    result = EngineResult.model_validate(message)
                    assert result.status == "ok" and result.S is not None
                    assert [capture.status for capture in captures] == ["ok", "ok"]
                    assert [capture.side for capture in captures] == ["palm", "back"]
                    break
            assert wait_until(lambda: app.state.scan_task.done() is True)
            shown = overlay.get()
            assert shown is not None and shown.inset is not None  # last capture stays on screen
        assert worker.acquire() is process  # the engine was loaded once for both scans
