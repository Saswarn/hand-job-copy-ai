"""Run in the engine virtual environment. Standard output is reserved for replies.

Protocol (one engine for the life of this process, one caller):
- first line out: {"type": "ready", "mask_backend", "startup_ms", "level_colors"}
- each request: one JSON line, followed by the RGB frame bytes when the header has "shape"
- each reply: one JSON line, followed by an H x W uint8 level map when the reply has "levels_shape"
"""

import json
import math
import os
import sys
import time
from dataclasses import fields
from typing import TYPE_CHECKING, BinaryIO

# Native library diagnostics must not be able to corrupt the IPC stream.
reply_fd = os.dup(sys.stdout.fileno())
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

import cv2
import numpy as np
from handclean_engine import CaptureResult, Frame, HandCleanEngine, MockEngine, PreviewResult
from handclean_engine.hand_mask import hybrid_labels

if TYPE_CHECKING:
    from handclean_engine.engine import HandCleanEngine as RealEngine  # HandCleanEngine() is a factory function

CONFIG = "engine.toml"  # relative to backend/engine-check, the worker's working directory
LEVELS_LONG_SIDE = 640  # capture level maps are shrunk to this for display; the score uses the full map


def read_frame(source: BinaryIO, request: dict[str, object]) -> Frame:
    shape = request["shape"]
    frame_id = request["frame_id"]
    timestamp = request["timestamp_ms"]
    if (
        not isinstance(shape, list)
        or len(shape) != 3
        or any(type(size) is not int or size <= 0 for size in shape)
        or shape[2] != 3
        or type(frame_id) is not int
        or type(timestamp) is not int
    ):
        raise ValueError("invalid engine frame header")
    count = math.prod(shape)
    data = source.read(count)
    if len(data) != count:
        raise EOFError("engine frame payload was truncated")
    return Frame(np.frombuffer(data, dtype=np.uint8).reshape(shape), "RGB", frame_id, timestamp)


def focus(rgb: np.ndarray, landmarks: list[list[tuple[float, float]]] | None) -> float:
    """Variance of the Laplacian of the grey preview, on the hands' box (else the middle half).

    Same as sharpness() in handclean-pi/pi/handclean_pi.py: only for comparing while turning the focus ring.
    """
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = grey.shape
    if landmarks:
        points = np.concatenate([np.asarray(hand) for hand in landmarks]) * [width, height]
        x0, y0 = np.clip(points.min(0).astype(int), 0, [width - 1, height - 1])
        x1, y1 = np.clip(points.max(0).astype(int) + 1, 1, [width, height])
        if (x1 - x0) * (y1 - y0) >= 400:
            grey = grey[y0:y1, x0:x1]
    else:
        grey = grey[height // 4 : 3 * height // 4, width // 4 : 3 * width // 4]
    return float(cv2.Laplacian(grey, cv2.CV_32F).var())


def rough_levels(engine: "RealEngine", rgb: np.ndarray, result: PreviewResult) -> np.ndarray | None:
    """Display-only residue levels on the preview frame, as EngineLoop._loop in handclean-pi does."""
    if result.hands_found != 2 or not result.landmarks:
        return None
    height, width = rgb.shape[:2]
    hands = [np.asarray(hand, np.float64) * [width, height] for hand in result.landmarks]
    labels = hybrid_labels(np.zeros((height, width), bool), hands, engine.cfg["mask"]["band_k"], engine.cfg["mask"]["bone_px"])
    return engine.levels(np.ascontiguousarray(rgb), labels != cv2.GC_BGD)


def shrink_levels(level_map: np.ndarray) -> np.ndarray:
    height, width = level_map.shape
    scale = LEVELS_LONG_SIDE / max(height, width)
    if scale >= 1:
        return level_map
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(level_map, size, interpolation=cv2.INTER_NEAREST)


def as_json(result: object) -> dict[str, object]:
    # level_map is sent as bytes after the JSON line, not inside it.
    return {field.name: getattr(result, field.name) for field in fields(result) if field.name != "level_map"}


def write(replies: BinaryIO, reply: object, levels: np.ndarray | None = None) -> None:
    if levels is not None:
        assert isinstance(reply, dict)
        reply["levels_shape"] = list(levels.shape)
    replies.write(json.dumps(reply, default=vars, allow_nan=False).encode("ascii") + b"\n")
    if levels is not None:
        replies.write(np.ascontiguousarray(levels, dtype=np.uint8).data.cast("B"))
    replies.flush()


def main(replies: BinaryIO) -> None:
    started = time.perf_counter()
    real = sys.argv[1] != "mock"
    engine = HandCleanEngine(CONFIG) if real else MockEngine()
    incoming = sys.stdin.buffer
    try:
        write(replies, {
            "type": "ready",
            "mask_backend": engine.mask_backend if real else "mock",
            "startup_ms": round((time.perf_counter() - started) * 1000),
            "level_colors": engine.cfg["display"]["level_colors"],
        })
        for line in incoming:
            request = json.loads(line)
            command = request["command"]
            levels: np.ndarray | None = None
            if command == "start_session":
                reply: object = engine.start_session()
            elif command == "push_preview":
                frame = read_frame(incoming, request)
                preview = engine.push_preview(frame)
                reply = as_json(preview)
                reply["focus"] = focus(frame.image, preview.landmarks)
                if real:
                    levels = rough_levels(engine, frame.image, preview)
            elif command == "submit_capture":
                capture: CaptureResult = engine.submit_capture(read_frame(incoming, request), request["side"])
                reply = as_json(capture)
                if capture.level_map is not None:
                    levels = shrink_levels(capture.level_map)
            elif command == "finish_session":
                reply = as_json(engine.finish_session())
            else:
                raise ValueError("unknown engine command")
            write(replies, reply, levels)
    finally:
        engine.close()


if __name__ == "__main__":
    with os.fdopen(reply_fd, "wb") as output:
        main(output)
