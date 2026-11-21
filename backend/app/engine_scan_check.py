"""Check the engine scan WebSocket contract without using the kiosk UI."""

import argparse
import math
import sys
import time
from collections.abc import Iterator
from statistics import median

from pydantic import TypeAdapter
from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

from app.schemas.ws import EngineCapture, EnginePreview, EngineResult, Side, UvState

_message = TypeAdapter(dict[str, object])


def messages(socket: ClientConnection, deadline: float) -> Iterator[str | bytes]:
    while (remaining := deadline - time.monotonic()) > 0:
        yield socket.recv(timeout=remaining)
    raise TimeoutError("WebSocket check did not finish before the deadline")


def check_scan(url: str, expect: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    with connect(url, open_timeout=min(timeout, 5), close_timeout=2) as socket:
        incoming = messages(socket, deadline)
        initial = UvState.model_validate_json(next(incoming))
        if initial.channels != [False, False, False]:
            raise ValueError("UV must be off before starting the check")

        socket.send('{"type":"engine_scan_start"}')
        started = time.monotonic()
        jpeg_frames = 0
        guidance: str | None = None
        captures: set[Side] = set()
        preview_ms: list[float] = []
        capture_ms: list[float] = []
        uv_on = False
        result: EngineResult | None = None
        for raw in incoming:
            if isinstance(raw, bytes):
                if not raw.startswith(b"\xff\xd8"):
                    raise ValueError("received a binary message that is not a JPEG")
                jpeg_frames += int(uv_on and result is None)
                continue
            data = _message.validate_json(raw)
            match data["type"]:
                case "error":
                    raise ValueError(str(data.get("message")))
                case "uv":
                    state = UvState.model_validate(data)
                    if state.channels == [True, True, True]:
                        uv_on = True
                        if expect == "disconnect":
                            break
                    elif state.channels == [False, False, False] and result is not None:
                        if not uv_on or jpeg_frames == 0:
                            raise ValueError("did not observe UV on and live JPEG frames during the scan")
                        elapsed = time.monotonic() - started
                        print(f"{result.model_dump_json()} | JPEG frames: {jpeg_frames} | elapsed: {elapsed:.2f} s")
                        if preview_ms:
                            print(f"Preview engine median: {median(preview_ms):.1f} ms ({len(preview_ms)} frames)")
                        if capture_ms:
                            print(f"Capture engine median: {median(capture_ms):.1f} ms ({len(capture_ms)} attempts)")
                        return
                case "engine_preview":
                    preview = EnginePreview.model_validate(data)
                    if (total := preview.timings_ms.get("total")) is not None:
                        preview_ms.append(total)
                    if preview.guidance != guidance:
                        guidance = preview.guidance
                        print(guidance, flush=True)
                case "engine_capture":
                    capture = EngineCapture.model_validate(data)
                    if (total := capture.timings_ms.get("total")) is not None:
                        capture_ms.append(total)
                    if capture.status == "ok":
                        if capture.side in captures:
                            raise ValueError(f"engine captured {capture.side} twice")
                        captures.add(capture.side)
                    print(f"{capture.side}: {capture.status} {capture.warnings}", flush=True)
                    for hand in capture.hands:
                        print(f"  {hand.hand}: {hand.score:.2f}", flush=True)
                case "engine_result":
                    result = EngineResult.model_validate(data)
                    if expect == "timeout":
                        if result.status != "incomplete" or result.reason != "timeout" or result.S is not None:
                            raise ValueError(f"expected an incomplete timeout result, got {result.status}")
                    elif result.status != "ok" or result.S is None or captures != {"palm", "back"}:
                        raise ValueError("expected both hands to be captured on both sides and a final score")
                case _:
                    raise ValueError(f"unexpected WebSocket message: {data['type']}")

    with connect(url, open_timeout=min(timeout, 5), close_timeout=2) as socket:
        state = UvState.model_validate_json(socket.recv(timeout=min(timeout, 5)))
        if state.channels != [False, False, False]:
            raise ValueError("backend reports UV still on after disconnect")
    print("Backend reports all UV channels off after disconnect.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--expect", choices=("complete", "timeout", "disconnect"), default="complete")
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    try:
        check_scan(args.url, args.expect, args.timeout)
    except (ValueError, KeyError, OSError, WebSocketException) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
