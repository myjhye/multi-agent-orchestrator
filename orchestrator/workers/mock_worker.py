"""
mock_worker.py — a fake worker that mimics the SDK worker's streaming shape.

Why this exists: the orchestration layer, the event/visibility system, and the
chat UI are the substance of this project. Being able to run the entire pipeline
end-to-end WITHOUT an API key means anyone can verify the coordination logic
and the interface in seconds, then flip WORKER_MODE=sdk for the real thing.

The mock produces role-appropriate, task-aware output so multi-step workflows
still read coherently (the coder "writes" code, the reviewer "reviews" it, etc.).
"""

from __future__ import annotations

import asyncio

from .base import LogCallback, ToolCallback, WorkerSpec


class MockWorker:
    def __init__(self, spec: WorkerSpec, model: str | None = None) -> None:
        self.spec = spec

    async def run(self, task: str, on_log: LogCallback, on_tool: ToolCallback) -> str:
        lines, tools, result = _script_for(self.spec.name, task)

        for name, detail in tools:
            await asyncio.sleep(0.35)
            await on_tool(name, detail)

        for line in lines:
            await asyncio.sleep(0.28)
            await on_log(line + "\n")

        await asyncio.sleep(0.2)
        return result


def _script_for(role: str, task: str) -> tuple[list[str], list[tuple[str, str]], str]:
    """Return (log_lines, tool_calls, final_result) for a role + task."""
    t = task.strip()
    short = (t[:80] + "...") if len(t) > 80 else t

    if role == "researcher":
        return (
            [
                f"Interpreting the request: {short}",
                "Searching for relevant, up-to-date sources...",
                "Cross-referencing three sources for agreement.",
                "Extracting the key facts and discarding filler.",
            ],
            [("WebSearch", short)],
            "RESEARCH NOTES\n"
            "- Key finding 1 relevant to the request.\n"
            "- Key finding 2 with a supporting detail.\n"
            "- Key finding 3 and one caveat to keep in mind.\n"
            "(mock research - enable WORKER_MODE=sdk for real sourcing)",
        )

    if role == "coder":
        return (
            [
                f"Understanding the spec: {short}",
                "Sketching the function signature and edge cases.",
                "Writing the implementation...",
            ],
            [("Write", "solution.py")],
            "```python\n"
            "import re\n\n"
            "def is_valid_email(address: str) -> bool:\n"
            '    """Return True if `address` looks like a valid email."""\n'
            "    pattern = r\"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$\"\n"
            "    return re.match(pattern, address) is not None\n"
            "```\n"
            "(mock implementation - enable WORKER_MODE=sdk for a real agent)",
        )

    if role == "reviewer":
        return (
            [
                "Reading the code produced in the previous step.",
                "Checking correctness, edge cases, and style.",
                "Writing tests to lock in the behavior...",
            ],
            [("Bash", "pytest -q")],
            "REVIEW: Looks correct for common cases. Consider rejecting emails "
            "with consecutive dots and enforcing a max length.\n\n"
            "```python\n"
            "def test_valid():\n"
            "    assert is_valid_email('a@b.com')\n\n"
            "def test_invalid():\n"
            "    assert not is_valid_email('a@b')\n"
            "    assert not is_valid_email('no-at-sign')\n"
            "```\n"
            "(mock review - enable WORKER_MODE=sdk for a real agent)",
        )

    if role == "writer":
        # The composed prompt contains the upstream outputs; assemble them so the
        # mock final answer is coherent (not a generic placeholder).
        upstream = _extract_upstream(task)
        body = ("\n\n".join(upstream) if upstream
                else "(no upstream output found)")
        return (
            [
                "Gathering the outputs from previous steps.",
                "Organizing them into a clear final answer.",
                "Polishing the wording...",
            ],
            [],
            "Final answer (assembled from the previous steps):\n\n"
            + body
            + "\n\n(mock synthesis - enable WORKER_MODE=sdk for a real agent.)",
        )

    if role == "evaluator":
        return (
            [
                "Reading original user request and final answer.",
                "Scoring completeness, correctness, and quality...",
                "Formatting JSON evaluation output...",
            ],
            [],
            '{"completeness": 5, "correctness": 5, "quality": 4, "overall": 5, "issues": []}',
        )

    return ([f"Working on: {short}"], [], f"Completed: {short}")


def _extract_upstream(task: str) -> list[str]:
    """Pull the '--- Output from previous step' sections out of a composed prompt."""
    sections: list[str] = []
    marker = "--- Output from previous step"
    if marker not in task:
        marker = "=== Output from step"
    idx = task.find(marker)
    while idx != -1:
        nxt = task.find(marker, idx + len(marker))
        chunk = task[idx:nxt] if nxt != -1 else task[idx:]
        # drop the header line, keep the body
        lines = chunk.splitlines()
        body = "\n".join(lines[1:]).strip()
        if body.endswith("--- End of step output ---"):
            body = body[:-len("--- End of step output ---")].strip()
        if body:
            sections.append(body)
        idx = nxt
    return sections
