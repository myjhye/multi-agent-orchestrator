"""
events.py — the visibility layer.

Every meaningful thing the orchestrator does is emitted as an Event.
The web server subscribes to these and streams them to the browser over a
WebSocket, so the user watches the orchestration happen in real time instead
of staring at a spinner.

This is deliberately tiny and dependency-free: an asyncio.Queue per run,
plus a typed event envelope.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class EventType(str, Enum):
    # Lifecycle of a whole request ("run")
    RUN_STARTED = "run_started"
    PLAN_CREATED = "plan_created"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"

    # Lifecycle of a single step inside the plan
    STEP_STARTED = "step_started"
    STEP_LOG = "step_log"          # streamed token/line from a worker
    STEP_TOOL = "step_tool"        # a worker invoked a tool (Bash, Write, ...)
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"


@dataclass
class Event:
    type: EventType
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


class EventBus:
    """One bus per run. Producers call emit(); the server drains via stream()."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()

    async def emit(self, type: EventType, **payload: Any) -> None:
        await self._queue.put(Event(type=type, run_id=self.run_id, payload=payload))

    async def close(self) -> None:
        # Sentinel tells the streamer we're done.
        await self._queue.put(None)

    async def stream(self):
        """Yield events until the bus is closed."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
