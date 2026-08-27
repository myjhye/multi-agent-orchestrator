"""
base.py — what every worker looks like to the orchestrator.

The orchestrator does not know or care whether a worker is a real Claude Agent
SDK agent or a mock. It only knows this interface: give it a task string and a
callback for streaming output, get back a final result string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol


# Called by a worker to stream a line of output as it happens.
#   on_log(text)              -> a chunk of the worker's thinking/output
#   on_tool(name, detail)     -> the worker used a tool
LogCallback = Callable[[str], Awaitable[None]]
ToolCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class WorkerSpec:
    """Static definition of a worker role."""
    name: str                       # short id, e.g. "coder"
    title: str                      # human label, e.g. "Coder"
    description: str                # one line, shown in the UI
    system_prompt: str              # persona / instructions
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 12


class Worker(Protocol):
    spec: WorkerSpec

    async def run(
        self,
        task: str,
        on_log: LogCallback,
        on_tool: ToolCallback,
    ) -> str:
        """Execute the task, streaming progress via callbacks, return final text."""
        ...
