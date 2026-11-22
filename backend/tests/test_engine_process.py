from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.main import app
from app.schemas.ws import EngineCapture, EnginePreview, EngineResult
from app.services.camera_rgb import ColorFrame
from app.services.engine_process import ENGINE_DIR, EngineProcess, configure_engine_factory


ENGINE_PYTHON = ENGINE_DIR / ".venv" / "bin" / "python"
requires_engine = pytest.mark.skipif(
    not ENGINE_PYTHON.is_file(), reason="run systems/scripts/setup-engine.sh first"
)


def test_engine_rejects_jpeg_camera(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMERA_SOURCE", "fake")
    monkeypatch.setenv("HANDCLEAN_ENGINE", "real")
    with pytest.raises(ValueError, match="requires CAMERA_SOURCE"):
        configure_engine_factory()


@requires_engine
def test_engine_factory_accepts_csi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMERA_SOURCE", "csi")
    monkeypatch.setenv("HANDCLEAN_ENGINE", "real")
    assert configure_engine_factory() is not None


@requires_engine
def test_real_engine_process_posts_valid_results_for_rgb_frame() -> None:
    engine = EngineProcess(Path(ENGINE_PYTHON), "real")
    frame = ColorFrame(np.zeros((240, 320, 3), np.uint8), frame_id=7, timestamp_ms=123)
    try:
        assert engine.start_session()
        preview = EnginePreview.model_validate(engine.push_preview(frame))
        assert preview.frame_id == 7 and preview.hands_found == 0
        capture = EngineCapture.model_validate(engine.submit_capture(frame, "palm"))
        assert capture.status == "NO_HAND"
        assert capture.warnings == ["MASK_FALLBACK"]
        result = EngineResult.model_validate(engine.finish_session())
        assert result.status == "incomplete" and len(result.missing_views) == 4
    finally:
        engine.close()


@requires_engine
def test_mock_engine_process_completes_scan_over_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMERA_SOURCE", "fake-rgb")
    monkeypatch.setenv("HANDCLEAN_ENGINE", "mock")
    messages = TypeAdapter(dict[str, object])
    captures: list[EngineCapture] = []
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "uv"
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
