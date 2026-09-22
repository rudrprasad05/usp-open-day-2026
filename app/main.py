from __future__ import annotations

import asyncio
import io
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageChops, ImageDraw

from .config import settings
from .game import GameState
from .leaderboard import Leaderboard
from .labels import LABELS
from .predictor import Predictor
from .websocket_manager import ConnectionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("drawai")
BASE_DIR = Path(__file__).resolve().parent

game = GameState(settings.game_duration_seconds)
leaderboard = Leaderboard(Path(settings.leaderboard_db_path)) if settings.leaderboard_db_path else Leaderboard()
manager = ConnectionManager()
predictor: Predictor | None = None
background_tasks: list[asyncio.Task] = []


def render_drawing(paths: list[list[dict]], size: int = 512) -> Image.Image | None:
    segments = [segment for path in paths for segment in path]
    # A handful of tiny movements produces mostly noise, not a useful sketch.
    if len(segments) < 3:
        return None
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    for segment in segments:
        points = [(segment["x1"] * size, segment["y1"] * size),
                  (segment["x2"] * size, segment["y2"] * size)]
        width = max(5, round(float(segment.get("width", 0.014)) * size))
        draw.line(points, fill="black", width=width, joint="curve")
        radius = width / 2
        for x, y in points:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="black")
    ink_bbox = ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
    if ink_bbox is None:
        return None
    left, top, right, bottom = ink_bbox
    if max(right - left, bottom - top) < size * 0.04:
        return None
    # Center the real ink, with 12.5% whitespace on each side. The result is
    # always opaque RGB, black-on-white, and contains no browser UI.
    width, height = right - left, bottom - top
    side = max(width, height)
    square = Image.new("RGB", (side, side), "white")
    square.paste(image.crop(ink_bbox), ((side - width) // 2, (side - height) // 2))
    result = Image.new("RGB", (size, size), "white")
    inner = round(size * 0.75)
    result.paste(square.resize((inner, inner), Image.Resampling.LANCZOS), ((size - inner) // 2, (size - inner) // 2))
    return result


async def predict_paths(paths: list[list[dict]], purpose: str = "live") -> list[dict]:
    if predictor is None or not predictor.ready:
        raise RuntimeError(predictor.error if predictor else "Predictor is not initialized")
    segment_count = sum(map(len, paths))
    logger.info("[AI] %s prediction requested", purpose.capitalize())
    logger.info("[AI] Rendering %d stroke segments", segment_count)
    image = render_drawing(paths)
    if image is None:
        logger.info("[AI] Skipping prediction: not enough drawing content yet")
        return []
    if settings.save_debug_prediction_images:
        debug_path = BASE_DIR.parent / "debug" / "prediction-latest.png"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(debug_path)
        logger.info("[AI] Saved classifier image: %s", debug_path)
    logger.info("[AI] Running inference")
    started = time.monotonic()
    try:
        # The game needs every class score, including targets outside the top five.
        # The predictor is never told which target the current round uses.
        predictions = await asyncio.to_thread(predictor.predict_all, image)
    except Exception:
        logger.exception("[AI ERROR] Prediction failed")
        raise
    logger.info("[AI] Prediction completed in %.0fms", (time.monotonic() - started) * 1000)
    logger.info("[AI] Top predictions: %s", ", ".join(f"{p.label}={p.confidence:.2f}" for p in predictions[:5]))
    return [{"label": p.label, "confidence": p.confidence} for p in predictions]


async def broadcast_state() -> None:
    status = predictor.status if predictor else {"ready": False, "error": "Loading model"}
    await asyncio.gather(
        manager.send_role("draw", game.snapshot("draw", status)),
        manager.send_role("display", game.snapshot("display", status)),
    )


async def broadcast_leaderboard() -> None:
    entries = await asyncio.to_thread(leaderboard.top, 20)
    await asyncio.gather(
        manager.send_role("display", {"type": "leaderboard", "entries": entries[:5]}),
        manager.send_role("leaderboard", {"type": "leaderboard", "entries": entries}),
    )


async def finalize_round(reason: str) -> None:
    job = await game.begin_finalization(reason)
    if job is None:
        return
    # Freeze input immediately, then score a fresh copy of the final drawing.
    await broadcast_state()
    try:
        final_predictions = await predict_paths(job.paths, purpose="final")
        if predictor:
            predictor.error = None
    except Exception as exc:
        logger.exception("[AI ERROR] Final prediction failed; scoring round as 0%%")
        if predictor:
            predictor.error = f"Final prediction failed: {type(exc).__name__}: {exc}"
        final_predictions = []
    finished, completed = await game.complete_finalization(job, final_predictions)
    if not finished:
        return
    logger.info("Round %d/%d finished: %s, score %.1f%%", game.round_number, 5, game.outcome, game.round_results[-1].score)
    if completed:
        try:
            _, rank = await asyncio.to_thread(leaderboard.save_session, completed)
            await game.set_leaderboard_rank(completed.session_id, rank)
            logger.info("Saved session %s for %s: %.1f%%, rank #%d", completed.session_id, completed.player_name, completed.score, rank)
        except Exception:
            logger.exception("Could not save completed session to SQLite")
    await broadcast_state()
    if completed:
        await broadcast_leaderboard()


async def game_loop() -> None:
    next_prediction_at = 0.0
    while True:
        await asyncio.sleep(0.1)
        if game.status != "running":
            continue
        if game.remaining() <= 0:
            await finalize_round("timeout")
            continue
        if not predictor or not predictor.ready or game.drawing_revision == game.predicted_revision:
            continue
        if time.monotonic() < next_prediction_at:
            continue
        async with game.lock:
            revision = game.drawing_revision
            structure_revision = game.structure_revision
            round_number = game.round_number
            paths = [[dict(segment) for segment in path] for path in game.paths]
        next_prediction_at = time.monotonic() + settings.prediction_interval_seconds
        game.predicted_revision = revision
        try:
            results = await predict_paths(paths)
        except Exception as exc:
            # Keep the worker alive. A later stroke can trigger a new attempt.
            predictor.error = f"Prediction failed: {type(exc).__name__}: {exc}"
            await broadcast_state()
            continue
        # New stroke segments do not invalidate an in-flight inference: showing a
        # recent snapshot keeps guesses moving during continuous drawing. Clear,
        # undo, and a new round do invalidate it via structure_revision.
        if game.status != "running" or round_number != game.round_number or structure_revision != game.structure_revision:
            continue
        if game.remaining() <= 0:
            await finalize_round("timeout")
            continue
        if not results:
            continue
        predictor.error = None
        accepted = await game.apply_predictions(results, round_number, structure_revision)
        if not accepted:
            continue
        payload = {"type": "prediction", "predictions": results[:5]}
        logger.info("[WS] Broadcasting prediction to %d display client(s)", manager.count("display"))
        await manager.send_role("display", payload)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global predictor
    leaderboard.initialize()
    logger.info("Leaderboard SQLite database: %s", leaderboard.path)
    predictor = await asyncio.to_thread(
        Predictor, LABELS, settings.model_name, settings.model_pretrained,
        settings.predictor_enabled, settings.prediction_logit_scale,
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
    return templates.TemplateResponse(request, "display.html")


@app.get("/draw", response_class=HTMLResponse)
async def draw_page(request: Request):
    return templates.TemplateResponse(request, "draw.html")


@app.get("/display", response_class=HTMLResponse)
async def display_page(request: Request):
    return templates.TemplateResponse(request, "display.html")


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request):
    return templates.TemplateResponse(request, "leaderboard.html")


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


@app.get("/api/leaderboard")
async def leaderboard_api(limit: int = Query(default=10, ge=1, le=100)):
    return {"entries": await asyncio.to_thread(leaderboard.top, limit)}


@app.post("/api/debug/predict-current")
async def debug_predict_current():
    async with game.lock:
        paths = [[dict(segment) for segment in path] for path in game.paths]
    stroke_count = sum(map(len, paths))
    if predictor is None or not predictor.ready:
        return JSONResponse({"strokeCount": stroke_count, "error": predictor.error if predictor else "Predictor not loaded"}, status_code=503)
    try:
        predictions = await predict_paths(paths)
    except Exception as exc:
        return JSONResponse({"strokeCount": stroke_count, "error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    if not predictions:
        return JSONResponse({"strokeCount": stroke_count, "error": "Not enough drawing content"}, status_code=422)
    return {"strokeCount": stroke_count, "predictions": predictions[:5]}


@app.websocket("/ws/{role}")
async def websocket_endpoint(websocket: WebSocket, role: str):
    if role not in {"draw", "display", "leaderboard"}:
        await websocket.close(code=1008)
        return
    await manager.connect(websocket, role)
    try:
        if role == "leaderboard":
            await websocket.send_json({"type": "leaderboard", "entries": await asyncio.to_thread(leaderboard.top, 20)})
        else:
            status = predictor.status if predictor else {"ready": False, "error": "Loading model"}
            await websocket.send_json(game.snapshot(role, status))
            if role == "display":
                await websocket.send_json({"type": "leaderboard", "entries": await asyncio.to_thread(leaderboard.top, 5)})
        while True:
            message = await websocket.receive_json()
            event = message.get("type")
            if role != "draw" and event != "ping":
                continue
            if event == "start":
                try:
                    started = await game.start_session(message.get("playerName"))
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
                if started:
                    logger.info("Started session for %s, round 1/5", game.player_name)
                    await broadcast_state()
                else:
                    await websocket.send_json({"type": "error", "message": "Finish the current session first."})
            elif event == "next":
                if await game.next_round():
                    logger.info("Started round %d/5", game.round_number)
                    await broadcast_state()
                else:
                    await websocket.send_json({"type": "error", "message": "This round is not ready to advance."})
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
            elif event in {"done", "finish_round"}:
                await finalize_round("done")
            elif event == "ping":
                await websocket.send_json({"type": "pong", "serverNow": time.time()})
    except WebSocketDisconnect:
        await manager.disconnect(websocket, role)
    except Exception:
        logger.exception("WebSocket error for %s", role)
        await manager.disconnect(websocket, role)
