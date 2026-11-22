from typing import TypedDict

import numpy as np
from numpy.typing import NDArray

class _Stream(TypedDict):
    size: tuple[int, int]
    format: str

class _Sensor(TypedDict, total=False):
    output_size: tuple[int, int]

class _Request:
    def make_array(self, name: str) -> NDArray[np.uint8]: ...
    def get_metadata(self) -> dict[str, object]: ...
    def release(self) -> None: ...

class Picamera2:
    sensor_resolution: tuple[int, int]
    def __init__(self) -> None: ...
    def create_video_configuration(
        self,
        *,
        main: _Stream,
        controls: dict[str, object],
        sensor: _Sensor,
        buffer_count: int,
        queue: bool,
    ) -> object: ...
    def configure(self, config: object) -> None: ...
    def start(self) -> None: ...
    def capture_request(self) -> _Request: ...
    def close(self) -> None: ...
