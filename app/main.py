from __future__ import annotations

import asyncio
import io
import logging
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw

from .config import settings
from .game import GameState
from .labels import LABELS
from .predictor import Predictor
from .websocket_manager import ConnectionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("drawai")
BASE_DIR = Path(__file__).resolve().parent

game = GameState(settings.game_duration_seconds, settings.win_confidence_threshold)
manager = ConnectionManager()
predictor: Predictor | None = None
background_tasks: list[asyncio.Task] = []


def render_drawing(paths: list[list[dict]], size: int = 512) -> Image.Image | None:
    segments = [segment for path in paths for segment in path]
    if not segments:
        return None
    xs = [float(s[k]) for s in segments for k in ("x1", "x2")]
    ys = [float(s[k]) for s in segments for k in ("y1", "y2")]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y, 0.08) * 1.25
    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
    left, top = cx - span / 2, cy - span / 2
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    for segment in segments:
        points = [
            ((segment["x1"] - left) / span * size, (segment["y1"] - top) / span * size),
            ((segment["x2"] - left) / span * size, (segment["y2"] - top) / span * size),
        ]
        width = max(3, round(float(segment.get("width", 0.014)) / span * size))
        draw.line(points, fill="black", width=width, joint="curve")
    return image


async def broadcast_state() -> None:
    status = predictor.status if predictor else {"ready": False, "error": "Loading model"}
    await asyncio.gather(
        manager.send_role("draw", game.snapshot("draw", status)),
        manager.send_role("display", game.snapshot("display", status)),
    )


async def game_loop() -> None:
    while True:
        await asyncio.sleep(0.2)
        if game.status != "running":
            continue
        if game.remaining() <= 0:
            await game.end("timeout")
            logger.info("Round %d timed out", game.round_number)
            await broadcast_state()
            continue
        if not predictor or not predictor.ready or game.drawing_revision == game.predicted_revision:
            continue
        revision = game.drawing_revision
        structure_revision = game.structure_revision
        async with game.lock:
            paths = [[dict(segment) for segment in path] for path in game.paths]
        image = render_drawing(paths)
        game.predicted_revision = revision
        if image is None:
            continue
        results = await asyncio.to_thread(predictor.predict, image)
        # New stroke segments do not invalidate an in-flight inference: showing a
        # recent snapshot keeps guesses moving during continuous drawing. Clear,
        # undo, and a new round do invalidate it via structure_revision.
        if game.status != "running" or structure_revision != game.structure_revision:
            continue
        game.predictions = [{"label": p.label, "confidence": p.confidence} for p in results]
        if results and results[0].label == game.target and results[0].confidence >= game.threshold:
            await game.end("ai_won")
            logger.info("AI guessed %s in %.1fs", game.target, game.elapsed())
        await broadcast_state()
        await asyncio.sleep(max(0.0, settings.prediction_interval_seconds - 0.2))


@asynccontextmanager
async def lifespan(_: FastAPI):
    global predictor
    predictor = await asyncio.to_thread(
        Predictor, LABELS, settings.model_name, settings.model_pretrained, settings.predictor_enabled
    )
    background_tasks.append(asyncio.create_task(game_loop()))
    logger.info("DrawAI ready — display: http://localhost:%d/display", settings.port)
    logger.info("Phone: http://<laptop-lan-ip>:%d/draw", settings.port)
    yield
    for task in background_tasks:
        task.cancel()


app = FastAPI(title="DrawAI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("display.html", {"request": request})


@app.get("/draw", response_class=HTMLResponse)
async def draw_page(request: Request):
    return templates.TemplateResponse("draw.html", {"request": request})


@app.get("/display", response_class=HTMLResponse)
async def display_page(request: Request):
    return templates.TemplateResponse("display.html", {"request": request})


def draw_url(request: Request) -> str:
    if settings.public_host:
        value = settings.public_host.rstrip("/")
        return f"{value}/draw" if value.startswith(("http://", "https://")) else f"http://{value}:{settings.port}/draw"
    return str(request.url_for("draw_page"))


@app.get("/qr.png")
async def qr_code(request: Request):
    try:
        import qrcode
        image = qrcode.make(draw_url(request))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return Response(output.getvalue(), media_type="image/png")
    except ImportError:
        return Response(status_code=404)


@app.get("/api/status")
async def status(request: Request):
    model_status = predictor.status if predictor else {"ready": False, "error": "Loading model"}
    return {"ok": True, "predictor": model_status, "drawUrl": draw_url(request), "game": game.snapshot("display", model_status)}


@app.websocket("/ws/{role}")
async def websocket_endpoint(websocket: WebSocket, role: str):
    if role not in {"draw", "display"}:
        await websocket.close(code=1008)
        return
    await manager.connect(websocket, role)
    try:
        status = predictor.status if predictor else {"ready": False, "error": "Loading model"}
        await websocket.send_json(game.snapshot(role, status))
        while True:
            message = await websocket.receive_json()
            event = message.get("type")
            if role != "draw" and event != "ping":
                continue
            if event in {"start", "next"}:
                await game.start()
                logger.info("Started round %d", game.round_number)
                await broadcast_state()
            elif event == "stroke":
                try:
                    segment = await game.add_segment(message)
                except (KeyError, TypeError, ValueError):
                    segment = None
                if segment:
                    await manager.send_role("display", {"type": "stroke", **segment})
            elif event == "clear":
                await game.clear()
                await broadcast_state()
            elif event == "undo":
                await game.undo()
                await broadcast_state()
            elif event == "done":
                await game.end("stopped")
                await broadcast_state()
            elif event == "ping":
                await websocket.send_json({"type": "pong", "serverNow": time.time()})
    except WebSocketDisconnect:
        await manager.disconnect(websocket, role)
    except Exception:
        logger.exception("WebSocket error for %s", role)
        await manager.disconnect(websocket, role)
