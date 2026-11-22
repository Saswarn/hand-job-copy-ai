import asyncio
import contextlib
import logging
import time
from threading import Event

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.core.config import AppConfig
from app.schemas.ws import (
    ClientMessage, EngineCapture, EngineResult, EngineScanCancel, EngineScanStart, Error, ScanStart,
    ScanState, Side, UvSet, UvState,
)
from app.services.camera import Camera
from app.services.detector import Detector
from app.services.engine_overlay import EngineOverlay
from app.services.engine_scan import EngineHost, EngineMessage, run_engine_scan
from app.services.scan import judge
from app.services.stream import Stream
from app.services.uv import UvService
from app.utils.image import Box


router = APIRouter()
log = logging.getLogger(__name__)
_client_message: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    uv: UvService = websocket.app.state.uv
    stream: Stream = websocket.app.state.stream
    detector: Detector = websocket.app.state.detector
    await websocket.accept()
    frames = asyncio.create_task(_send_frames(websocket, stream))
    owned_engine_task: asyncio.Task[None] | None = None
    try:
        await websocket.send_text(UvState(channels=uv.state()).model_dump_json())
        while True:
            raw = await websocket.receive_text()
            try:
                msg = _client_message.validate_json(raw)
            except ValidationError as e:
                await websocket.send_text(Error(message=str(e)).model_dump_json())
                continue
            scan_task: asyncio.Task[None] | None = websocket.app.state.scan_task
            busy = scan_task is not None and not scan_task.done()
            if isinstance(msg, EngineScanCancel):
                if scan_task is not None and scan_task is owned_engine_task and busy:
                    uv.all_off()
                    if not scan_task.cancelling():
                        scan_task.cancel()
                    await websocket.send_text(UvState(channels=uv.state()).model_dump_json())
            elif isinstance(msg, UvSet):
                if busy and websocket.app.state.engine_active:
                    await websocket.send_text(Error(message="UV is owned by the engine scan").model_dump_json())
                    continue
                uv.set(msg.channel, msg.on)
                await websocket.send_text(UvState(channels=uv.state()).model_dump_json())
            elif isinstance(msg, (ScanStart, EngineScanStart)):
                if busy:
                    await websocket.send_text(Error(message="scan already in progress").model_dump_json())
                    continue
                cfg: AppConfig = websocket.app.state.config
                websocket.app.state.engine_active = isinstance(msg, EngineScanStart)
                if isinstance(msg, EngineScanStart):
                    host: EngineHost | None = websocket.app.state.engine_host
                    if host is None:
                        await websocket.send_text(Error(message="engine scan unavailable").model_dump_json())
                        continue
                    camera: Camera = websocket.app.state.camera
                    overlay: EngineOverlay = websocket.app.state.engine_overlay
                    task = asyncio.create_task(
                        _engine_scan(websocket, uv, camera, host, overlay, cfg.engine_scan_seconds)
                    )
                    owned_engine_task = task
                else:
                    task = asyncio.create_task(_scan(websocket, uv, detector, cfg))
                websocket.app.state.scan_task = task
            else:
                # TODO(backend): binary_view -> stream the binary image the AI sees instead of the display frame
                await websocket.send_text(Error(message=f"{msg.type} is not implemented yet").model_dump_json())
    except WebSocketDisconnect:
        pass
    finally:
        uv.all_off()  # frontend gone -> every UV channel off (systems/api.md)
        active: asyncio.Task[None] | None = websocket.app.state.scan_task
        try:
            if active is not None and not active.done():
                if not active.cancelling():
                    active.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await active
        finally:
            frames.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await frames


async def _send_frames(websocket: WebSocket, stream: Stream) -> None:
    """Send only the newest display frame; a slow client skips frames instead of queueing them."""
    seq = 0
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):  # socket closed under us
        while True:
            seq, jpeg = await asyncio.to_thread(stream.latest.wait_newer, seq, 0.5)
            if jpeg is not None:
                await websocket.send_bytes(jpeg)


async def _engine_scan(
    websocket: WebSocket,
    uv: UvService,
    camera: Camera,
    host: EngineHost,
    overlay: EngineOverlay,
    timeout: float,
) -> None:
    stop = Event()
    loop = asyncio.get_running_loop()
    messages: asyncio.Queue[EngineMessage | None] = asyncio.Queue()
    missing: set[Side] = {"palm", "back"}
    worker: asyncio.Task[None] | None = None

    def publish(message: EngineMessage) -> None:
        loop.call_soon_threadsafe(messages.put_nowait, message)

    async def produce() -> None:
        try:
            await asyncio.to_thread(
                run_engine_scan, host, engine, camera.latest_color, seq, deadline, stop, publish, overlay
            )
        finally:
            messages.put_nowait(None)

    try:
        # Wait for the engine (it may still be loading after startup) before the UV lights and the deadline start.
        engine = await asyncio.to_thread(host.acquire)
        async with asyncio.timeout(timeout):
            for i in range(3):
                uv.set(i, True)
            seq = camera.latest_color.sequence()
            deadline = time.monotonic() + timeout
            worker = asyncio.create_task(produce())
            await websocket.send_text(UvState(channels=uv.state()).model_dump_json())
            while (message := await messages.get()) is not None:
                if isinstance(message, EngineCapture) and message.status == "ok":
                    missing.discard(message.side)
                if isinstance(message, EngineResult):
                    uv.all_off()
                    await websocket.send_text(message.model_dump_json())
                    return
                await websocket.send_text(message.model_dump_json())
            await worker
            # A worker that ends without a score reached its own deadline.
            raise TimeoutError
    except TimeoutError:
        uv.all_off()
        result = EngineResult(
            status="incomplete", S=None, S_worst=None, worst_view=None, reason="timeout",
            missing_views=[f"{hand}_{side}" for side in sorted(missing) for hand in ("left", "right")],
        )
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_text(result.model_dump_json())
    except WebSocketDisconnect:
        pass
    except Exception:
        uv.all_off()
        log.exception("engine scan failed")
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_text(Error(message="engine scan failed").model_dump_json())
    finally:
        uv.all_off()
        stop.set()
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_text(UvState(channels=uv.state()).model_dump_json())
        if worker is not None:
            # Keep ownership until the blocking engine call has returned and closed.
            await asyncio.gather(worker, return_exceptions=True)


async def _scan(websocket: WebSocket, uv: UvService, detector: Detector, cfg: AppConfig) -> None:
    try:
        for i in range(3):
            uv.set(i, True)
        end_time = time.monotonic() + cfg.scan_seconds
        await websocket.send_text(UvState(channels=uv.state()).model_dump_json())
        ai_frames: list[list[Box]] = []
        while time.monotonic() < end_time:
            ai_frames.append(list(detector.latest()))
            time_left = max(0.0, end_time - time.monotonic())
            await websocket.send_text(
                ScanState(state="scanning", remaining_s=time_left, result=None).model_dump_json()
            )
            await asyncio.sleep(min(0.5, max(0.0, end_time - time.monotonic())))
        result = judge(ai_frames, cfg.dirt_conf, cfg.dirt_min_frames)
        uv.all_off()  # done must never report completion while the lights are still on
        await websocket.send_text(ScanState(state="done", remaining_s=None, result=result).model_dump_json())
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("scan failed")
        uv.all_off()
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_text(Error(message="scan failed").model_dump_json())
    finally:
        uv.all_off()
        try:
            await websocket.send_text(UvState(channels=uv.state()).model_dump_json())
        except (WebSocketDisconnect, RuntimeError):
            pass
