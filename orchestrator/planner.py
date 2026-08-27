"""
planner.py — decompose a natural-language request into a plan.

A Plan is an ordered list of Steps. Each Step names the worker to run, an
instruction for that worker, and which earlier steps it depends on (so the
orchestrator can feed their outputs forward).

The planner here is rule-based: it classifies the request and selects a
workflow template. It is intentionally transparent and deterministic — you can
read a request and predict the plan. The Planner class is the single seam where
you could swap in an LLM-generated plan later without touching the orchestrator;
`plan()` just has to return the same Step list.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    id: str
    worker: str                       # key into WORKER_SPECS
    instruction: str                  # what this worker should do
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Plan:
    goal: str
    steps: list[Step]
    rationale: str = ""


# Keyword buckets used to classify the request.
_CODE_WORDS = (
    "code", "function", "script", "program", "bug", "refactor", "api",
    "python", "javascript", "java", "sql", "class", "implement", "algorithm",
    "write", "reader", "test", "tests",
    "코드", "함수", "구현", "스크립트", "버그", "리팩터", "알고리즘", "작성", "테스트",
)
_RESEARCH_WORDS = (
    "research", "find", "compare", "summarize", "summary", "explain", "news",
    "overview", "investigate", "who", "what", "why", "trend",
    "조사", "비교", "요약", "설명", "정리", "뉴스", "알아봐", "찾아",
)
_BUILD_AFTER_RESEARCH = (
    "research and", "then write", "then build", "and write",
    "조사해서", "조사하고", "찾아서", "알아보고",
)


class Planner:
    def plan(self, request: str) -> Plan:
        text = request.lower()

        wants_code = any(w in text for w in _CODE_WORDS)
        wants_research = any(w in text for w in _RESEARCH_WORDS)
        research_then_build = any(w in text for w in _BUILD_AFTER_RESEARCH)

        # Workflow 1: research -> code -> review -> synthesize (4 steps, 4 workers)
        if wants_code and (wants_research or research_then_build):
            return self._research_build_review(request)

        # Workflow 2 (flagship): code -> review -> synthesize (3 steps, 3 workers)
        if wants_code:
            return self._code_review(request)

        # Workflow 3: research -> synthesize (2 workers)
        if wants_research:
            return self._research_write(request)

        # Fallback: still multi-step — research the ask, then write it up.
        return self._research_write(request)

    # ── templates ───────────────────────────────────────────────────────────

    def _code_review(self, request: str) -> Plan:
        return Plan(
            goal=request,
            rationale="Detected a coding task. Route: Coder writes it, Reviewer "
            "checks it and adds tests, Writer assembles the final answer.",
            steps=[
                Step("s1", "coder",
                     "Write code that satisfies the user's request."),
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
