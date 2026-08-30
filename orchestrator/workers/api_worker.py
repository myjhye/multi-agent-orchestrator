"""
api_worker.py — a worker that calls the Anthropic Messages API directly.

Used for tasks that need LLM reasoning but not agent capabilities
(no tools, no file access, no multi-turn loops). The Evaluator is
the primary use case: it reads text and returns a JSON score.
"""

from __future__ import annotations

import anthropic

from .base import LogCallback, ToolCallback, WorkerSpec


class ApiWorker:
    def __init__(self, spec: WorkerSpec, model: str | None = None) -> None:
        self.spec = spec
        self.model = model or spec.model or "claude-haiku-4-5-20251001"

    async def run(self, task: str, on_log: LogCallback, on_tool: ToolCallback) -> str:
        client = anthropic.Anthropic()

        await on_log("Evaluating output...\n")

        response = client.messages.create(
            model=self.model,
            max_tokens=512,
            system=self.spec.system_prompt,
            messages=[{"role": "user", "content": task}],
        )

        result = response.content[0].text.strip()
        await on_log(result + "\n")
        return result
