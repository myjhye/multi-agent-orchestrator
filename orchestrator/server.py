"""
server.py — the local web server.

Serves the chat UI and exposes a WebSocket. When the browser sends a request,
the server spins up an Orchestrator run, subscribes to its EventBus, and streams
every event to the browser as JSON. That stream is what gives the UI its live
visibility into the orchestration.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_settings
from .orchestrator import Orchestrator
from .events import EventBus
from .workers import WORKER_SPECS

settings = load_settings()
app = FastAPI(title="Local Multi-Agent Orchestrator")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/info")
async def info() -> dict:
    """Tell the UI which mode we're in and who the workers are."""
    return {
        "mode": settings.worker_mode,
        "requested_mode": settings.requested_mode,
        "has_api_key": settings.has_api_key,
        "model": settings.model,
        "workers": [
            {"name": s.name, "title": s.title, "description": s.description}
            for s in WORKER_SPECS.values()
        ],
    }


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    orchestrator = Orchestrator(
        mode=settings.worker_mode,
        model=settings.model,
        planner_mode=settings.planner_mode,
    )

    try:
        while True:
            data = await websocket.receive_json()
            request = (data or {}).get("request", "").strip()
            if not request:
                continue

            run_id = uuid.uuid4().hex[:8]
            bus = EventBus(run_id)

            # Run the orchestration and forward its events concurrently.
            async def drive() -> None:
                try:
                    await orchestrator.run(run_id, request, bus)
                except Exception:  # noqa: BLE001 — already reported via events
                    pass

            run_task = asyncio.create_task(drive())
            async for event in bus.stream():
                await websocket.send_json(event.to_dict())
            await run_task

    except WebSocketDisconnect:
        return


def serve() -> None:
    import uvicorn

    banner = (
        f"\n  Local Multi-Agent Orchestrator"
        f"\n  mode: {settings.worker_mode.upper()}"
        + ("  (no API key - simulated workers)" if settings.worker_mode == "mock" else "")
        + f"\n  open: http://{settings.host}:{settings.port}\n"
    )
    print(banner)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
