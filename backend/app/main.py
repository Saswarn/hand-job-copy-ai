import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import config, health, ws
from app.core.config import CONFIG_PATH, load_config
from app.services.camera import Camera, open_camera
from app.services.detector import Detector
from app.services.engine_overlay import EngineOverlay
from app.services.engine_process import EngineWorker, configure_engine_worker
from app.services.stream import Stream
from app.services.uv import UvService

# uvicorn configures only its own loggers; this shows app INFO lines (engine ready, camera settings) next to them.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.config_path = CONFIG_PATH
    app.state.config = cfg = load_config(app.state.config_path)
    app.state.uv = uv = UvService(cfg.uv_gpio)  # every channel starts off
    app.state.scan_task = None
    app.state.engine_active = False
    app.state.engine_overlay = overlay = EngineOverlay()
    camera: Camera | None = None
    stream: Stream | None = None
    engine: EngineWorker | None = None
    try:
        app.state.engine_host = engine = configure_engine_worker()
        app.state.camera = camera = open_camera(cfg.camera)
        detector = Detector()
        app.state.detector = detector
        app.state.stream = stream = Stream(camera, detector, cfg.camera.crop, cfg.stream, overlay)
        camera.start()
        stream.start()
        if engine is not None:
            engine.start()  # loads in the background; scans wait for it
        yield
    finally:
        uv.close()  # UV off before anything else
        for service in (stream, camera):
            if service:
                service.stop()
        if engine is not None:
            engine.close()


app = FastAPI(title="HandCleanStation", lifespan=lifespan)
# Electron renderer: http://localhost:<vite port> in dev, file:// ("null" origin) when packaged.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(health.router)
app.include_router(config.router)
app.include_router(ws.router)


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception) -> JSONResponse:
    request.app.state.uv.all_off()
    log.exception("unhandled error", exc_info=exc)
    return JSONResponse({"detail": "internal error"}, status_code=500)
