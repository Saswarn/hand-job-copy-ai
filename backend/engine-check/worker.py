"""Run in the engine virtual environment. Standard output is reserved for replies."""

import json
import math
import os
import sys
from dataclasses import fields
from typing import BinaryIO

# Native library diagnostics must not be able to corrupt the IPC stream.
reply_fd = os.dup(sys.stdout.fileno())
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

import numpy as np
from handclean_engine import Frame, HandCleanEngine, MockEngine


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


def main(replies: BinaryIO) -> None:
    engine = MockEngine() if sys.argv[1] == "mock" else HandCleanEngine()
    incoming = sys.stdin.buffer
    try:
        for line in incoming:
            request = json.loads(line)
            command = request["command"]
            if command == "start_session":
                result = engine.start_session()
            elif command == "push_preview":
                result = engine.push_preview(read_frame(incoming, request))
            elif command == "submit_capture":
                result = engine.submit_capture(read_frame(incoming, request), request["side"])
            elif command == "finish_session":
                result = engine.finish_session()
            else:
                raise ValueError("unknown engine command")
            if not isinstance(result, str):
                # Arrays are not part of the WebSocket result contract.
                result = {field.name: getattr(result, field.name) for field in fields(result) if field.name != "level_map"}
            replies.write(json.dumps(result, default=vars, allow_nan=False).encode("ascii") + b"\n")
            replies.flush()
    finally:
        engine.close()


if __name__ == "__main__":
    with os.fdopen(reply_fd, "wb") as output:
        main(output)
