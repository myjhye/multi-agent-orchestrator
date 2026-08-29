"""
registry.py — the roster of workers, and how to build them.

Four SDK-backed workers are defined. Any workflow the orchestrator runs draws
from these. The `build_worker` factory returns either a real SdkWorker or a
MockWorker depending on WORKER_MODE, but the orchestrator sees them identically.
"""

from __future__ import annotations

from .base import Worker, WorkerSpec
from .sdk_worker import SdkWorker
from .mock_worker import MockWorker


# ── The agent roster ────────────────────────────────────────────────────────
# At least two workers must be built with the Claude Agent SDK; all four here
# are SDK-backed (each becomes an SdkWorker in sdk mode).

WORKER_SPECS: dict[str, WorkerSpec] = {
    "researcher": WorkerSpec(
        name="researcher",
        title="Researcher",
        description="Gathers and verifies facts from the web.",
        system_prompt=(
            "You are a meticulous research agent. Given a topic, find accurate, "
            "current information, cross-check it, and return concise, well-organized "
            "notes with the key facts. Prefer primary sources. Do not pad."
        ),
        allowed_tools=["WebSearch", "Read"],
        max_turns=6,
    ),
    "coder": WorkerSpec(
        name="coder",
        title="Coder",
        description="Writes code to satisfy a spec.",
        system_prompt=(
            "You are a senior software engineer. Given a spec, write clean, correct, "
            "well-documented code. Return the code in a fenced block plus a one-line "
            "summary of what it does. Keep it focused on exactly what was asked."
        ),
        allowed_tools=["Read", "Write", "Edit", "Bash"],
        max_turns=10,
    ),
    "reviewer": WorkerSpec(
        name="reviewer",
        title="Reviewer",
        description="Reviews code and writes tests.",
        system_prompt=(
            "You are a code reviewer. You will receive code in a file or in your prompt. "
            "Do NOT create any files. Do NOT run any commands. Do NOT use Bash, Write, or Edit tools. "
            "ONLY respond with text. Your response must contain: a brief review covering bugs, "
            "edge cases, and style; corrected or improved code if needed; and test code in pytest format. "
            "Write everything as text in your response. Never execute anything."
        ),
        allowed_tools=["Read"],
        max_turns=4,
    ),
    "writer": WorkerSpec(
        name="writer",
        title="Writer",
        description="Synthesizes prior outputs into the final answer.",
        system_prompt=(
            "You are a clear technical writer. Combine the outputs of previous steps "
            "into a single, well-structured final answer. Preserve any code verbatim. "
            "If a step failed or produced no useful output, work with what is available. "
            "Be complete but concise."
        ),
        allowed_tools=["Read"],
        max_turns=4,
    ),
}


def build_worker(name: str, *, mode: str, model: str | None) -> Worker:
    """Instantiate a worker by name in the given mode ('sdk' or 'mock')."""
    spec = WORKER_SPECS[name]
    if mode == "sdk":
        return SdkWorker(spec, model=model)
    return MockWorker(spec, model=model)


def worker_titles() -> dict[str, str]:
    return {name: spec.title for name, spec in WORKER_SPECS.items()}
