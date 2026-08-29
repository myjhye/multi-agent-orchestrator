"""
planner.py — decompose a natural-language request into a plan.

Two modes:
  - "llm":  Calls the Anthropic Messages API to generate a JSON plan.
            The orchestrator stays SDK-free; this is a raw API call.
  - "rule": Original keyword-based classification (zero-cost, deterministic).

The Planner is the single seam where intelligence enters the orchestration.
Swapping modes changes only how plans are generated — the orchestrator,
workers, and event system are untouched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

AVAILABLE_WORKERS = {
    "researcher": "Gathers and verifies facts from the web.",
    "coder": "Writes code to satisfy a spec.",
    "reviewer": "Reviews code and writes tests.",
    "writer": "Synthesizes prior outputs into the final answer.",
}


@dataclass
class Step:
    id: str
    worker: str
    instruction: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Plan:
    goal: str
    steps: list[Step]
    rationale: str = ""


# ── Keyword buckets for rule-based mode ─────────────────────────────────
_CODE_WORDS = (
    "code", "function", "script", "program", "bug", "refactor", "api",
    "python", "javascript", "java", "sql", "class", "implement", "algorithm",
    "write", "reader", "parser", "test", "tests",
    "코드", "함수", "구현", "스크립트", "버그", "리팩터", "알고리즘",
)
_RESEARCH_WORDS = (
    "research", "find", "compare", "summarize", "summary", "explain", "news",
    "overview", "investigate", "who", "what", "why", "trend",
    "조사", "비교", "요약", "설명", "정리", "뉴스", "알아봐", "찾아",
)
_BUILD_AFTER_RESEARCH = (
    "research and", "then write", "then build", "then create", "and write", "and build",
    "조사해서", "조사하고", "찾아서", "알아보고",
)


class Planner:
    def __init__(self, mode: str = "rule") -> None:
        self.mode = mode

    def plan(self, request: str) -> Plan:
        if self.mode == "llm":
            try:
                return self._plan_with_llm(request)
            except Exception as e:
                print(f"[Planner] LLM planning failed ({e}), falling back to rule-based")
                return self._plan_with_rules(request)
        return self._plan_with_rules(request)

    # ── LLM-based planning ──────────────────────────────────────────────

    def _plan_with_llm(self, request: str) -> Plan:
        import anthropic

        client = anthropic.Anthropic()

        worker_desc = "\n".join(
            f"  - {name}: {desc}" for name, desc in AVAILABLE_WORKERS.items()
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": f"""Decompose the following user request into an execution plan.

Available workers:
{worker_desc}

Rules:
- Each step has: id (s1, s2, ...), worker (one of the available workers), instruction (what this worker should do), depends_on (list of step ids whose output this step needs).
- Use at least 2 different workers.
- Use at least 3 steps.
- The last step should be "writer" to assemble the final answer.
- Steps must be in dependency order.
- Keep instructions concise and specific to the request.

User request: "{request}"

Respond with ONLY valid JSON, no markdown fences, no explanation:
{{
  "rationale": "one sentence explaining the plan",
  "steps": [
    {{"id": "s1", "worker": "...", "instruction": "...", "depends_on": []}},
    ...
  ]
}}"""
                }
            ],
        )

        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[: text.rfind("```")]
            text = text.strip()

        data = json.loads(text)

        steps = [
            Step(
                id=s["id"],
                worker=s["worker"],
                instruction=s["instruction"],
                depends_on=s.get("depends_on", []),
            )
            for s in data["steps"]
            if s["worker"] in AVAILABLE_WORKERS
        ]

        if len(steps) < 2:
            raise ValueError("LLM plan has fewer than 2 valid steps")

        return Plan(
            goal=request,
            steps=steps,
            rationale=data.get("rationale", "LLM-generated plan"),
        )

    # ── Rule-based planning ─────────────────────────────────────────────

    def _plan_with_rules(self, request: str) -> Plan:
        text = request.lower()

        wants_code = any(w in text for w in _CODE_WORDS)
        wants_research = any(w in text for w in _RESEARCH_WORDS)
        research_then_build = any(w in text for w in _BUILD_AFTER_RESEARCH)

        if wants_code and (wants_research or research_then_build):
            return self._research_build_review(request)
        if wants_code:
            return self._code_review(request)
        if wants_research:
            return self._research_write(request)
        return self._research_write(request)

    def _code_review(self, request: str) -> Plan:
        return Plan(
            goal=request,
            rationale="Detected a coding task. Route: Coder writes it, Reviewer "
            "checks it and adds tests, Writer assembles the final answer.",
            steps=[
                Step("s1", "coder", "Write code that satisfies the user's request."),
                Step("s2", "reviewer",
                     "Review the code from the previous step and write tests for it.",
                     depends_on=["s1"]),
                Step("s3", "writer",
                     "Combine the code and the review/tests into one final answer.",
                     depends_on=["s1", "s2"]),
            ],
        )

    def _research_write(self, request: str) -> Plan:
        return Plan(
            goal=request,
            rationale="Detected an information request. Route: Researcher gathers "
            "facts, Writer turns them into a clear answer.",
            steps=[
                Step("s1", "researcher",
                     "Research the user's request and produce sourced notes."),
                Step("s2", "writer",
                     "Turn the research notes into a clear, well-structured answer.",
                     depends_on=["s1"]),
            ],
        )

    def _research_build_review(self, request: str) -> Plan:
        return Plan(
            goal=request,
            rationale="Detected research-then-build. Route: Researcher gathers "
            "context, Coder builds on it, Reviewer tests it, Writer finalizes.",
            steps=[
                Step("s1", "researcher",
                     "Research the background needed to satisfy the request."),
                Step("s2", "coder",
                     "Using the research, write code that satisfies the request.",
                     depends_on=["s1"]),
                Step("s3", "reviewer",
                     "Review the code and write tests for it.",
                     depends_on=["s2"]),
                Step("s4", "writer",
                     "Combine the research, code, and review into a final answer.",
                     depends_on=["s1", "s2", "s3"]),
            ],
        )
