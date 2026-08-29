"""Shared fixtures and helpers for the orchestrator test suite."""

from __future__ import annotations

import asyncio

import pytest

from orchestrator.events import Event, EventBus, EventType
from orchestrator.workers import WorkerSpec
from orchestrator.workers import mock_worker as mock_worker_module


# ── speed ───────────────────────────────────────────────────────────────────
# MockWorker paces itself with asyncio.sleep() so the UI has something to
# animate. That is ~1.4s per step, which would make this suite crawl. We swap
# only the `asyncio` name *inside mock_worker* for a shim exposing a no-op
# sleep, so no other module's timing is affected.


class _InstantAsyncio:
    @staticmethod
    async def sleep(_delay: float) -> None:
        return None


@pytest.fixture(autouse=True)
def instant_mock_worker(monkeypatch):
    monkeypatch.setattr(mock_worker_module, "asyncio", _InstantAsyncio)


# ── doubles ─────────────────────────────────────────────────────────────────


class FakeWorker:
    """Deterministic stand-in for a worker: records prompts, echoes its role."""

    def __init__(self, spec: WorkerSpec, *, fail_with: Exception | None = None) -> None:
        self.spec = spec
        self.fail_with = fail_with
        self.prompts: list[str] = []

    async def run(self, task, on_log, on_tool) -> str:
        self.prompts.append(task)
        await on_log(f"{self.spec.name} thinking\n")
        await on_tool("Bash", "echo hi")
        if self.fail_with is not None:
            raise self.fail_with
        return f"OUT[{self.spec.name}]"


class WorkerFactory:
    """Replaces orchestrator.build_worker and keeps every worker it built."""

    def __init__(self, fail_on: str | None = None, error: Exception | None = None) -> None:
        self.fail_on = fail_on
        self.error = error or RuntimeError("worker exploded")
        self.built: list[FakeWorker] = []

    def __call__(self, name: str, *, mode: str, model: str | None) -> FakeWorker:
        from orchestrator.workers import WORKER_SPECS

        spec = WORKER_SPECS[name]
        worker = FakeWorker(spec, fail_with=self.error if name == self.fail_on else None)
        self.built.append(worker)
        return worker

    def prompt_for(self, role: str) -> str:
        for w in self.built:
            if w.spec.name == role:
                assert w.prompts, f"{role} was built but never run"
                return w.prompts[0]
        raise AssertionError(f"no {role} worker was built")


@pytest.fixture()
def fake_workers(monkeypatch):
    """Patch the orchestrator's worker factory with deterministic doubles."""

    def _install(fail_on: str | None = None, error: Exception | None = None) -> WorkerFactory:
        factory = WorkerFactory(fail_on=fail_on, error=error)
        monkeypatch.setattr("orchestrator.orchestrator.build_worker", factory)
        return factory

    return _install


# ── helpers ─────────────────────────────────────────────────────────────────


async def run_and_collect(orch, request: str, run_id: str = "run1", timeout: float = 2.0):
    """
    Drive a full orchestrator run while draining its EventBus concurrently.

    Returns (result, events). Re-raises whatever the run raised, but only after
    the event stream has terminated -- so a test that expects a failure still
    proves the bus was closed rather than left hanging.
    """
    bus = EventBus(run_id)
    events: list[Event] = []

    async def drain() -> None:
        async for event in bus.stream():
            events.append(event)

    drain_task = asyncio.create_task(drain())
    try:
        result = await orch.run(run_id, request, bus)
    except BaseException:
        await asyncio.wait_for(drain_task, timeout=timeout)
        raise
    await asyncio.wait_for(drain_task, timeout=timeout)
    return result, events


def types_of(events: list[Event]) -> list[str]:
    return [e.type.value for e in events]


def first_payload(events: list[Event], type_: EventType) -> dict:
    for e in events:
        if e.type is type_:
            return e.payload
    raise AssertionError(f"no {type_} event in {types_of(events)}")


def payloads(events: list[Event], type_: EventType) -> list[dict]:
    return [e.payload for e in events if e.type is type_]
