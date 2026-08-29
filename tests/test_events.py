"""EventBus / Event — the visibility layer everything else reports through."""

from __future__ import annotations

import asyncio
import json

import pytest

from orchestrator.events import Event, EventBus, EventType


class TestEvent:
    def test_to_dict_serialises_type_as_a_plain_string(self):
        event = Event(type=EventType.STEP_LOG, run_id="r1", payload={"text": "hi"})
        d = event.to_dict()
        assert d["type"] == "step_log"
        assert isinstance(d["type"], str)
        assert d["run_id"] == "r1"
        assert d["payload"] == {"text": "hi"}

    def test_to_dict_is_json_encodable(self):
        """server.py hands this straight to websocket.send_json()."""
        event = Event(type=EventType.RUN_COMPLETED, run_id="r1", payload={"result": "done"})
        assert json.loads(json.dumps(event.to_dict()))["type"] == "run_completed"

    def test_timestamp_is_populated_automatically(self):
        assert Event(type=EventType.RUN_STARTED, run_id="r").ts > 0

    def test_payload_defaults_to_empty_and_is_not_shared(self):
        a = Event(type=EventType.RUN_STARTED, run_id="r")
        b = Event(type=EventType.RUN_STARTED, run_id="r")
        a.payload["x"] = 1
        assert b.payload == {}

    def test_event_type_is_a_str_enum(self):
        assert EventType.STEP_TOOL == "step_tool"


class TestEventBus:
    async def test_emit_then_stream_yields_events_in_order(self):
        bus = EventBus("r1")
        await bus.emit(EventType.RUN_STARTED, request="go")
        await bus.emit(EventType.STEP_LOG, text="one")
        await bus.emit(EventType.STEP_LOG, text="two")
        await bus.close()

        seen = [e async for e in bus.stream()]
        assert [e.type for e in seen] == [
            EventType.RUN_STARTED,
            EventType.STEP_LOG,
            EventType.STEP_LOG,
        ]
        assert [e.payload.get("text") for e in seen] == [None, "one", "two"]

    async def test_stamps_every_event_with_the_bus_run_id(self):
        bus = EventBus("abc123")
        await bus.emit(EventType.STEP_STARTED, step_id="s1")
        await bus.close()
        assert [e.run_id async for e in bus.stream()] == ["abc123"]

    async def test_kwargs_become_the_payload(self):
        bus = EventBus("r1")
        await bus.emit(EventType.STEP_TOOL, tool="Bash", detail="pytest -q")
        await bus.close()
        events = [e async for e in bus.stream()]
        assert events[0].payload == {"tool": "Bash", "detail": "pytest -q"}

    async def test_stream_blocks_until_events_arrive_then_ends_on_close(self):
        """A consumer may attach before the producer has emitted anything."""
        bus = EventBus("r1")
        seen = []

        async def consume():
            async for e in bus.stream():
                seen.append(e)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        assert seen == []  # still waiting, not spinning

        await bus.emit(EventType.STEP_LOG, text="late")
        await bus.close()
        await asyncio.wait_for(task, timeout=1)
        assert len(seen) == 1

    async def test_stream_never_terminates_without_close(self):
        """Documents the contract every producer must honour: always close()."""
        bus = EventBus("r1")
        await bus.emit(EventType.RUN_STARTED)

        async def consume():
            async for _ in bus.stream():
                pass

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(consume(), timeout=0.25)

    async def test_close_is_the_terminator_events_after_it_are_not_delivered(self):
        bus = EventBus("r1")
        await bus.emit(EventType.STEP_LOG, text="before")
        await bus.close()
        await bus.emit(EventType.STEP_LOG, text="after")

        seen = [e async for e in bus.stream()]
        assert [e.payload.get("text") for e in seen] == ["before"]
