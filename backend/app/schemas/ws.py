"""WebSocket /ws text messages. Contract: systems/api.md. Binary messages are display JPEGs."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


# frontend -> backend
class UvSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["uv_set"]
    channel: int = Field(ge=0, le=2)  # index into uv_gpio
    on: bool


class ScanStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["scan_start"]


class EngineScanStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["engine_scan_start"]


class EngineScanCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["engine_scan_cancel"]


class BinaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["binary_view"]
    on: bool


ClientMessage = Annotated[
    UvSet | ScanStart | EngineScanStart | EngineScanCancel | BinaryView, Field(discriminator="type")
]


# backend -> frontend
class UvState(BaseModel):
    type: Literal["uv"] = "uv"
    channels: list[bool]


class ScanState(BaseModel):
    """Sent about every 0.5 seconds while scanning, and once when done."""

    type: Literal["scan"] = "scan"
    state: Literal["idle", "scanning", "done"]
    remaining_s: float | None  # seconds left while scanning, else null
    result: Literal["clean", "dirt"] | None  # set when state == "done"


Side = Literal["palm", "back"]


class EnginePreview(BaseModel):
    type: Literal["engine_preview"] = "engine_preview"
    frame_id: int
    hands_found: int = Field(ge=0, le=2)
    side: Side | Literal["unsure"] | None
    stable_ms: int = Field(ge=0)
    ready_to_capture: bool
    needed_sides: list[Side]
    guidance: str
    timings_ms: dict[str, float] = Field(default_factory=dict)


class EngineHand(BaseModel):
    hand: Literal["left", "right"]
    score: float
    level_fraction: tuple[float, float, float, float, float]


class EngineCapture(BaseModel):
    type: Literal["engine_capture"] = "engine_capture"
    status: Literal["ok", "NO_HAND", "ONE_HAND"]
    side: Side
    hands: list[EngineHand]
    warnings: list[str]
    timings_ms: dict[str, float] = Field(default_factory=dict)


class EngineResult(BaseModel):
    type: Literal["engine_result"] = "engine_result"
    status: Literal["ok", "incomplete"]
    S: float | None
    S_worst: float | None
    worst_view: str | None
    missing_views: list[str]
    reason: Literal["timeout"] | None = None


class Error(BaseModel):
    type: Literal["error"] = "error"
    message: str
