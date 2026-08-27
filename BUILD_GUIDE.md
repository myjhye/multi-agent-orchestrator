# 로컬 멀티에이전트 오케스트레이터 — 단계별 구현 가이드

이 문서는 과제를 **처음부터 stage별로** 구현하기 위한 설계 + 코드 draft입니다.
코딩 툴(예: Claude Code)에서 각 Stage를 순서대로 만들고, Stage 끝의
**체크포인트**로 검증한 뒤 다음 Stage로 넘어가세요.

## 설계 원칙 3가지

1. **바닥부터 위로 쌓는다.** 가시성 → 워커 → 플래너 → 오케스트레이터 → 서버 → UI.
   아래 계층이 검증되어야 위 계층을 신뢰할 수 있습니다.
2. **mock 우선.** API 키 없이 돌아가는 가짜 워커를 먼저 만들어, 오케스트레이션
   로직과 UI를 크레딧 소모 없이 전부 검증합니다. 실제 Claude Agent SDK 워커는
   마지막 Stage에서 config 값 하나로 갈아끼웁니다.
3. **오케스트레이터는 '지능'을 갖지 않는다.** 계획·순서·출력 전달·이벤트·취합만
   담당하고, 실제 에이전트 작업은 전부 SDK 워커가 합니다. 이게 "orchestration
   layer from scratch" 요구사항의 핵심입니다.

## 최종 디렉터리 구조

```
multi-agent-orchestrator/
├── run.py                       # 진입점 → 서버 실행
├── requirements.txt
├── .env.example
├── README.md
├── orchestrator/
│   ├── __init__.py
│   ├── server.py                # FastAPI: 정적 UI + /ws + /api/info
│   ├── orchestrator.py          # 직접 구현한 코디네이션 엔진
│   ├── planner.py               # 요청 → 의존성 있는 Plan
│   ├── events.py                # 이벤트 타입 + per-run async 버스 (가시성)
│   ├── config.py                # .env / 설정, 모드 결정
│   └── workers/
│       ├── __init__.py
│       ├── base.py              # Worker 인터페이스 + WorkerSpec
│       ├── registry.py          # 4개 에이전트 로스터 + build_worker 팩토리
│       ├── sdk_worker.py        # Claude Agent SDK 기반 워커
│       └── mock_worker.py       # 시뮬레이션 워커 (API 키 불필요)
└── static/
    └── index.html               # 단일 파일 채팅 UI + 라이브 파이프라인
```

## 빌드 순서 한눈에

| Stage | 만드는 것 | 검증 방법 |
|---|---|---|
| 0 | 프로젝트 뼈대 · 가상환경 · 의존성 | 서버 deps import 확인 |
| 1 | 가시성 레이어 `events.py` | 단독 이벤트 emit/stream 테스트 |
| 2 | 워커 인터페이스 + mock 워커 | mock 워커 단독 실행 |
| 3 | SDK 워커 + 레지스트리 | 레지스트리 로스터 확인 |
| 4 | 플래너 | 요청별 plan 분기 확인 |
| 5 | 오케스트레이터 엔진 + config | **CLI 통합 테스트 (mock)** |
| 6 | 서버 + WebSocket | WebSocket 왕복 테스트 |
| 7 | 채팅 UI | 브라우저에서 mock 전 과정 |
| 8 | 실제 SDK 모드 전환 | 키 넣고 `WORKER_MODE=sdk` |

---

# Stage 0 — 프로젝트 뼈대 & 환경

**목표:** 폴더 구조와 의존성을 잡고, 이후 Stage가 얹힐 토대를 만든다.

먼저 폴더를 만듭니다.

```bash
mkdir -p multi-agent-orchestrator/orchestrator/workers multi-agent-orchestrator/static
cd multi-agent-orchestrator
```

`requirements.txt` — 웹 서버와 Claude Agent SDK. SDK는 Claude Code CLI가
wheel 안에 번들되어 있어 별도 설치가 필요 없습니다 (Python 3.10+).

```text
# Core web server (orchestrator API + WebSocket)
fastapi==0.115.6
uvicorn[standard]==0.34.0

# Claude Agent SDK — powers the workers.
# The Claude Code CLI is bundled inside this wheel (Python 3.10+).
claude-agent-sdk==0.1.3

# Config loading
python-dotenv==1.0.1
```

`.env.example` — 설정 템플릿. `WORKER_MODE=auto`면 키가 있으면 sdk,
없으면 mock으로 자동 결정됩니다.

```bash
# ── Copy this file to `.env` and fill in your key ──────────────────
#   cp .env.example .env

# Your Anthropic API key. Get one at https://console.anthropic.com
# Required when WORKER_MODE=sdk. Leave blank to run in mock mode.
ANTHROPIC_API_KEY=

# Which worker backend to use:
#   sdk   → real Claude Agent SDK agents (needs ANTHROPIC_API_KEY, costs tokens)
#   mock  → simulated agents (no API, no cost — good for trying the UI/flow)
#   auto  → sdk if ANTHROPIC_API_KEY is set, otherwise mock   [default]
WORKER_MODE=auto

# Model the SDK workers use (optional; SDK has its own default otherwise)
# WORKER_MODEL=claude-sonnet-4-6

# Host/port for the local server
HOST=127.0.0.1
PORT=8000
```

가상환경 생성 + 설치:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

**체크포인트:** 아래가 에러 없이 출력되면 통과.

```bash
python3 -c "import fastapi, uvicorn, dotenv; print('web deps ok')"
python3 --version    # 3.10 이상이어야 함
```

> SDK(`claude-agent-sdk`)는 Stage 8 전까지 실제로 쓰지 않습니다. 지금 설치가
> 실패해도 mock 경로 개발은 계속할 수 있어요.

---

# Stage 1 — 가시성 레이어 (events.py)

**목표:** "오케스트레이션 과정을 볼 수 있게" 하라는 요구사항의 토대. 오케스트레이터가
하는 모든 일을 타입이 있는 `Event`로 emit하고, 서버가 그걸 WebSocket으로 흘려보냅니다.

의존성이 전혀 없는 가장 아래 계층이라 먼저 만듭니다: run마다 `asyncio.Queue` 하나 +
이벤트 봉투(envelope).

`orchestrator/events.py`

```python
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
```

**체크포인트:** 이벤트를 emit하고 stream으로 되받는지 단독 테스트.

```bash
PYTHONPATH=. python3 - << 'EOF'
import asyncio
from orchestrator.events import EventBus, EventType

async def main():
    bus = EventBus("run1")
    async def producer():
        await bus.emit(EventType.RUN_STARTED, request="hi")
        await bus.emit(EventType.RUN_COMPLETED, result="done")
        await bus.close()
    asyncio.create_task(producer())
    async for ev in bus.stream():
        print(ev.type.value, ev.payload)

asyncio.run(main())
EOF
```

기대 출력:
```
run_started {'request': 'hi'}
run_completed {'result': 'done'}
```

---

# Stage 2 — 워커 인터페이스 + mock 워커

**목표:** 오케스트레이터가 워커를 어떻게 바라보는지 정의하고(인터페이스), API 없이
돌아가는 mock 워커를 만든다. 이걸로 이후 모든 상위 계층을 크레딧 없이 검증할 수 있습니다.

## 2-1. 워커 인터페이스 — `orchestrator/workers/base.py`

오케스트레이터는 워커가 진짜 SDK 에이전트인지 mock인지 **모릅니다**. 오직 이 계약만
압니다: task 문자열과 스트리밍 콜백을 주면, 최종 결과 문자열을 돌려준다.

```python
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
```

## 2-2. mock 워커 — `orchestrator/workers/mock_worker.py`

SDK 워커와 **동일한 스트리밍 형태**를 흉내 냅니다. 역할(researcher/coder/reviewer/
writer)에 맞는 그럴듯한 출력을 내서, 멀티스텝 워크플로우가 mock에서도 일관되게
읽힙니다. `writer`는 프롬프트에 실린 이전 단계 출력들을 파싱해 실제로 취합합니다.

```python
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
    short = (t[:80] + "…") if len(t) > 80 else t

    if role == "researcher":
        return (
            [
                f"Interpreting the request: {short}",
                "Searching for relevant, up-to-date sources…",
                "Cross-referencing three sources for agreement.",
                "Extracting the key facts and discarding filler.",
            ],
            [("WebSearch", short)],
            "RESEARCH NOTES\n"
            "- Key finding 1 relevant to the request.\n"
            "- Key finding 2 with a supporting detail.\n"
            "- Key finding 3 and one caveat to keep in mind.\n"
            "(mock research — enable WORKER_MODE=sdk for real sourcing)",
        )

    if role == "coder":
        return (
            [
                f"Understanding the spec: {short}",
                "Sketching the function signature and edge cases.",
                "Writing the implementation…",
            ],
            [("Write", "solution.py")],
            "```python\n"
            "import re\n\n"
            "def is_valid_email(address: str) -> bool:\n"
            '    """Return True if `address` looks like a valid email."""\n'
            "    pattern = r\"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$\"\n"
            "    return re.match(pattern, address) is not None\n"
            "```\n"
            "(mock implementation — enable WORKER_MODE=sdk for a real agent)",
        )

    if role == "reviewer":
        return (
            [
                "Reading the code produced in the previous step.",
                "Checking correctness, edge cases, and style.",
                "Writing tests to lock in the behavior…",
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
            "(mock review — enable WORKER_MODE=sdk for a real agent)",
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
                "Polishing the wording…",
            ],
            [],
            "Final answer (assembled from the previous steps):\n\n"
            + body
            + "\n\n(mock synthesis — enable WORKER_MODE=sdk for a real agent.)",
        )

    return ([f"Working on: {short}"], [], f"Completed: {short}")


def _extract_upstream(task: str) -> list[str]:
    """Pull the '=== Output from step X ===' sections out of a composed prompt."""
    sections: list[str] = []
    marker = "=== Output from step"
    idx = task.find(marker)
    while idx != -1:
        nxt = task.find(marker, idx + len(marker))
        chunk = task[idx:nxt] if nxt != -1 else task[idx:]
        # drop the header line, keep the body
        lines = chunk.splitlines()
        body = "\n".join(lines[1:]).strip()
        if body:
            sections.append(body)
        idx = nxt
    return sections
```

**체크포인트:** mock 워커 단독 실행.

```bash
PYTHONPATH=. python3 - << 'EOF'
import asyncio
from orchestrator.workers.base import WorkerSpec
from orchestrator.workers.mock_worker import MockWorker

async def main():
    spec = WorkerSpec(name="coder", title="Coder", description="", system_prompt="")
    w = MockWorker(spec)
    async def on_log(t): print("log>", t.strip())
    async def on_tool(n, d): print("tool>", n, d)
    out = await w.run("Write an email validator", on_log, on_tool)
    print("--- RESULT ---"); print(out)

asyncio.run(main())
EOF
```

coder 역할의 로그·툴·코드 결과가 순서대로 출력되면 통과.

---

# Stage 3 — SDK 워커 + 레지스트리

**목표:** 실제 Claude Agent SDK 워커를 구현하고, 4개 에이전트 로스터를 정의한다.
mock/sdk를 config 값 하나로 스왑하는 팩토리도 여기서.

## 3-1. SDK 워커 — `orchestrator/workers/sdk_worker.py`

과제가 요구하는 "Claude Agent SDK로 만든 워커". SDK의 `query()` async 제너레이터
호출 하나를 감싸서, SDK 메시지 스트림을 오케스트레이터의 log/tool 콜백으로 번역합니다.
`import`는 `run()` 안에서 지연 로딩 — SDK 미설치 머신에서도 mock 모드로 부팅되도록.

```python
"""
sdk_worker.py — a worker backed by the Claude Agent SDK.

This is the required "worker built with the Claude Agent SDK". Each instance
wraps one call to the SDK's `query()` async generator, translating the stream
of SDK messages into the orchestrator's log/tool callbacks.

The SDK talks to Claude for us: it runs the agent loop, executes built-in tools
(Read, Write, Edit, Bash, WebSearch, ...), and streams messages back.
"""

from __future__ import annotations

from .base import LogCallback, ToolCallback, WorkerSpec

# Imported lazily inside run() so the app can still boot in mock mode on a
# machine where the SDK / its CLI isn't installed.


class SdkWorker:
    def __init__(self, spec: WorkerSpec, model: str | None = None) -> None:
        self.spec = spec
        self.model = model

    async def run(self, task: str, on_log: LogCallback, on_tool: ToolCallback) -> str:
        from claude_agent_sdk import (
            query,
            ClaudeAgentOptions,
            AssistantMessage,
            TextBlock,
        )

        options_kwargs = dict(
            system_prompt=self.spec.system_prompt,
            allowed_tools=self.spec.allowed_tools,
            permission_mode="acceptEdits",  # unattended: auto-approve file edits
            max_turns=self.spec.max_turns,
        )
        if self.model:
            options_kwargs["model"] = self.model

        options = ClaudeAgentOptions(**options_kwargs)

        collected: list[str] = []

        async for message in query(prompt=task, options=options):
            # Text the agent produced -> stream it as a log line.
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        collected.append(block.text)
                        await on_log(block.text)
                    else:
                        # Tool-use and other blocks vary by SDK version; read
                        # them defensively by attribute rather than by class.
                        tool_name = getattr(block, "name", None)
                        if tool_name:
                            detail = _short_repr(getattr(block, "input", ""))
                            await on_tool(str(tool_name), detail)
            else:
                # ResultMessage / SystemMessage / etc. — surface a compact note
                # so the timeline shows the agent finishing, without noise.
                result_text = getattr(message, "result", None)
                if isinstance(result_text, str) and result_text.strip():
                    collected.append(result_text)
                    await on_log(result_text)

        return "".join(collected).strip()


def _short_repr(value: object, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"
```

## 3-2. 레지스트리 — `orchestrator/workers/registry.py`

4개 워커의 정적 정의(persona·허용 툴·max_turns)와, 모드에 따라 `SdkWorker` 또는
`MockWorker`를 반환하는 `build_worker` 팩토리. 4개 모두 SDK 백엔드라, 과제의
"SDK 워커 2개 이상" 요건을 넉넉히 충족합니다.

```python
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
        max_turns=10,
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
        max_turns=14,
    ),
    "reviewer": WorkerSpec(
        name="reviewer",
        title="Reviewer",
        description="Reviews code and writes tests.",
        system_prompt=(
            "You are a rigorous code reviewer. Given code, review it for correctness, "
            "edge cases, and style, then write tests that lock in the intended "
            "behavior. Return your review notes followed by the test code."
        ),
        allowed_tools=["Read", "Write", "Edit", "Bash"],
        max_turns=14,
    ),
    "writer": WorkerSpec(
        name="writer",
        title="Writer",
        description="Synthesizes prior outputs into the final answer.",
        system_prompt=(
            "You are a clear technical writer. Given the outputs of previous steps, "
            "combine them into a single, well-structured final answer for the user. "
            "Preserve any code verbatim. Be complete but concise."
        ),
        allowed_tools=["Read"],
        max_turns=8,
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
```

## 3-3. 패키지 노출 — `orchestrator/workers/__init__.py`

```python
from .base import Worker, WorkerSpec
from .registry import WORKER_SPECS, build_worker, worker_titles

__all__ = ["Worker", "WorkerSpec", "WORKER_SPECS", "build_worker", "worker_titles"]
```

**체크포인트:** 로스터가 로드되는지 확인.

```bash
PYTHONPATH=. python3 -c "from orchestrator.workers import WORKER_SPECS, worker_titles; print(worker_titles())"
```

기대: `{'researcher': 'Researcher', 'coder': 'Coder', 'reviewer': 'Reviewer', 'writer': 'Writer'}`

---

# Stage 4 — 플래너 (planner.py)

**목표:** 자연어 요청을 **의존성 있는 단계 리스트(Plan)**로 분해한다. 지금은 규칙
기반이라 투명하고 예측 가능합니다. 나중에 LLM 계획 생성으로 바꾸고 싶으면 이
`Planner.plan()` 하나만 고치면 됩니다 — 같은 `Step` 리스트만 반환하면 오케스트레이터는
손댈 필요 없습니다(이게 지능을 끼워 넣는 단일 seam).

`orchestrator/planner.py`

```python
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
    "코드", "함수", "구현", "스크립트", "버그", "리팩터", "알고리즘",
)
_RESEARCH_WORDS = (
    "research", "find", "compare", "summarize", "summary", "explain", "news",
    "overview", "investigate", "who", "what", "why", "trend",
    "조사", "비교", "요약", "설명", "정리", "뉴스", "알아봐", "찾아",
)
_BUILD_AFTER_RESEARCH = ("research and", "조사해서", "조사하고", "찾아서", "알아보고")


class Planner:
    def plan(self, request: str) -> Plan:
        text = request.lower()

        wants_code = any(w in text for w in _CODE_WORDS)
        wants_research = any(w in text for w in _RESEARCH_WORDS)
        research_then_build = any(w in text for w in _BUILD_AFTER_RESEARCH)

        # Workflow 1: research → code → review → synthesize (4 steps, 4 workers)
        if wants_code and (wants_research or research_then_build):
            return self._research_build_review(request)

        # Workflow 2 (flagship): code → review → synthesize (3 steps, 3 workers)
        if wants_code:
            return self._code_review(request)

        # Workflow 3: research → synthesize (2 workers)
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
```

**체크포인트:** 요청별로 다른 워크플로우가 선택되는지 확인.

```bash
PYTHONPATH=. python3 - << 'EOF'
from orchestrator.planner import Planner
p = Planner()
for req in [
    "Write a Python function with tests",
    "Research small language models and summarize",
    "Research CSV pitfalls, then write a CSV reader with tests",
]:
    plan = p.plan(req)
    print(req[:40], "->", [s.worker for s in plan.steps])
EOF
```

기대:
```
Write a Python function with tests       -> ['coder', 'reviewer', 'writer']
Research small language models and summa -> ['researcher', 'writer']
Research CSV pitfalls, then write a CSV r -> ['researcher', 'coder', 'reviewer', 'writer']
```

---

# Stage 5 — 오케스트레이터 엔진 + config

**목표:** 이 프로젝트의 심장. 플래너·워커·이벤트를 엮어 실제로 조율한다. 그리고 여기서
**첫 통합 테스트**를 mock 모드로 돌립니다.

## 5-1. 설정 — `orchestrator/config.py`

`.env`를 읽고 `WORKER_MODE`(sdk/mock/auto)를 실제 모드로 결정합니다.

```python
"""config.py — read settings from the environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    worker_mode: str          # resolved: "sdk" or "mock"
    requested_mode: str       # raw value from env: sdk | mock | auto
    has_api_key: bool
    model: str | None
    host: str
    port: int


def load_settings() -> Settings:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    requested = os.getenv("WORKER_MODE", "auto").strip().lower()

    if requested == "sdk":
        mode = "sdk"
    elif requested == "mock":
        mode = "mock"
    else:  # auto
        mode = "sdk" if api_key else "mock"

    return Settings(
        worker_mode=mode,
        requested_mode=requested,
        has_api_key=bool(api_key),
        model=os.getenv("WORKER_MODEL", "").strip() or None,
        host=os.getenv("HOST", "127.0.0.1").strip(),
        port=int(os.getenv("PORT", "8000")),
    )
```

## 5-2. 엔진 — `orchestrator/orchestrator.py`

동작: (1) 플래너로 요청 분해 → (2) 의존성 위상정렬 순서로 단계 실행 → (3) 각 단계에
자신이 의존하는 단계들의 출력을 주입(= 워크플로우를 '흐름'으로 만드는 바통 패스) →
(4) 매 단계 이벤트 emit → (5) 마지막 단계 출력을 최종 결과로 반환. 여기엔 에이전트
프레임워크가 전혀 없습니다 — 워커를 구동하는 순수 async Python뿐.

```python
"""
orchestrator.py — the coordination engine, built from scratch.

This is the "orchestration layer" the brief asks for. It owns no agent
intelligence of its own; its whole job is coordination:

  1. Ask the Planner to decompose the request into steps.
  2. Run steps in dependency order.
  3. Feed each step the outputs of the steps it depends on (the bucket-passing
     that makes a 3+ step, multi-worker workflow actually a *workflow* and not
     three unrelated calls).
  4. Emit an event at every stage so the UI can show what's happening.
  5. Assemble the final result and return it.

No agent framework is used here — just plain async Python driving the workers.
"""

from __future__ import annotations

from .events import EventBus, EventType
from .planner import Plan, Planner, Step
from .workers import WORKER_SPECS, build_worker


class Orchestrator:
    def __init__(self, *, mode: str, model: str | None) -> None:
        self.mode = mode
        self.model = model
        self.planner = Planner()

    async def run(self, run_id: str, request: str, bus: EventBus) -> str:
        await bus.emit(EventType.RUN_STARTED, request=request, mode=self.mode)

        # 1) Plan ------------------------------------------------------------
        plan: Plan = self.planner.plan(request)
        await bus.emit(
            EventType.PLAN_CREATED,
            goal=plan.goal,
            rationale=plan.rationale,
            steps=[
                {
                    "id": s.id,
                    "worker": s.worker,
                    "title": WORKER_SPECS[s.worker].title,
                    "instruction": s.instruction,
                    "depends_on": s.depends_on,
                }
                for s in plan.steps
            ],
        )

        # 2) Execute steps in dependency order -------------------------------
        outputs: dict[str, str] = {}
        try:
            for step in self._in_execution_order(plan.steps):
                result = await self._run_step(step, request, outputs, bus)
                outputs[step.id] = result
        except Exception as exc:  # noqa: BLE001 — surface any worker failure
            await bus.emit(EventType.RUN_FAILED, error=str(exc))
            await bus.close()
            raise

        # 3) Final result = output of the terminal step ----------------------
        final_step = plan.steps[-1]
        final = outputs[final_step.id]

        await bus.emit(
            EventType.RUN_COMPLETED,
            result=final,
            steps_run=[
                {"id": s.id, "worker": s.worker, "title": WORKER_SPECS[s.worker].title}
                for s in plan.steps
            ],
        )
        await bus.close()
        return final

    # ── internals ────────────────────────────────────────────────────────────

    async def _run_step(
        self,
        step: Step,
        request: str,
        outputs: dict[str, str],
        bus: EventBus,
    ) -> str:
        spec = WORKER_SPECS[step.worker]
        prompt = self._compose_prompt(step, request, outputs)

        await bus.emit(
            EventType.STEP_STARTED,
            step_id=step.id,
            worker=step.worker,
            title=spec.title,
            instruction=step.instruction,
            depends_on=step.depends_on,
        )

        async def on_log(text: str) -> None:
            await bus.emit(EventType.STEP_LOG, step_id=step.id, worker=step.worker, text=text)

        async def on_tool(name: str, detail: str) -> None:
            await bus.emit(
                EventType.STEP_TOOL, step_id=step.id, worker=step.worker, tool=name, detail=detail
            )

        worker = build_worker(step.worker, mode=self.mode, model=self.model)

        try:
            result = await worker.run(prompt, on_log, on_tool)
        except Exception as exc:  # noqa: BLE001
            await bus.emit(EventType.STEP_FAILED, step_id=step.id, worker=step.worker, error=str(exc))
            raise

        await bus.emit(
            EventType.STEP_COMPLETED,
            step_id=step.id,
            worker=step.worker,
            title=spec.title,
            output=result,
        )
        return result

    def _compose_prompt(self, step: Step, request: str, outputs: dict[str, str]) -> str:
        """Build the worker prompt: its instruction + the request + upstream outputs."""
        parts = [step.instruction, "", "=== User request ===", request]
        for dep in step.depends_on:
            if dep in outputs:
                parts += ["", f"=== Output from step {dep} ===", outputs[dep]]
        return "\n".join(parts)

    @staticmethod
    def _in_execution_order(steps: list[Step]) -> list[Step]:
        """
        Topological order: only run a step once every step it depends on has run.
        Templates are already authored in order, but we gate on dependencies so
        the engine stays correct for any plan shape.
        """
        by_id = {s.id: s for s in steps}
        done: set[str] = set()
        ordered: list[Step] = []
        remaining = list(steps)

        while remaining:
            progressed = False
            for step in list(remaining):
                if all(dep in done for dep in step.depends_on):
                    ordered.append(step)
                    done.add(step.id)
                    remaining.remove(step)
                    progressed = True
            if not progressed:
                missing = {s.id: s.depends_on for s in remaining}
                raise ValueError(f"Unsatisfiable step dependencies: {missing}")

        # touch by_id so linters don't flag it; also validates dep targets exist
        for step in steps:
            for dep in step.depends_on:
                if dep not in by_id:
                    raise ValueError(f"Step {step.id} depends on unknown step {dep}")
        return ordered
```

## 5-3. 패키지 노출 — `orchestrator/__init__.py`

```python
from .orchestrator import Orchestrator
from .planner import Planner, Plan, Step
from .events import EventBus, Event, EventType
from .config import Settings, load_settings

__all__ = [
    "Orchestrator",
    "Planner",
    "Plan",
    "Step",
    "EventBus",
    "Event",
    "EventType",
    "Settings",
    "load_settings",
]
```

**체크포인트 (가장 중요):** mock 모드로 전체 파이프라인을 CLI에서 돌려봅니다.

```bash
PYTHONPATH=. python3 - << 'EOF'
import asyncio
from orchestrator.orchestrator import Orchestrator
from orchestrator.events import EventBus

async def main():
    orch = Orchestrator(mode="mock", model=None)
    bus = EventBus("t1")
    async def show():
        async for ev in bus.stream():
            t, p = ev.type.value, ev.payload
            if t == "plan_created":
                print("PLAN:", [s["worker"] for s in p["steps"]])
            elif t == "step_started": print("  START", p["title"])
            elif t == "step_completed": print("  DONE ", p["title"], f"({len(p['output'])} chars)")
            elif t == "run_completed": print("FINAL len:", len(p["result"]))
    asyncio.create_task(show())
    await orch.run("t1", "Write a Python function to validate an email address, with tests", bus)

asyncio.run(main())
EOF
```

기대: PLAN이 `['coder','reviewer','writer']`로 잡히고, 3단계가 START/DONE되며,
최종 결과 길이가 출력되면 **아키텍처가 통째로 검증된 것**입니다. UI·서버 없이도 조율이
동작함을 확인한 셈이에요.

---

# Stage 6 — 서버 + WebSocket

**목표:** 브라우저가 요청을 보내면 오케스트레이션 run을 띄우고, 그 run의 모든 이벤트를
JSON으로 브라우저에 스트리밍한다. 이 스트림이 UI 라이브 가시성의 원천입니다.

## 6-1. 서버 — `orchestrator/server.py`

```python
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
    orchestrator = Orchestrator(mode=settings.worker_mode, model=settings.model)

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
        + ("  (no API key — simulated workers)" if settings.worker_mode == "mock" else "")
        + f"\n  open: http://{settings.host}:{settings.port}\n"
    )
    print(banner)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
```

## 6-2. 진입점 — `run.py`

```python
#!/usr/bin/env python3
"""Entry point: `python run.py` starts the local server + UI."""

from orchestrator.server import serve

if __name__ == "__main__":
    serve()
```

**체크포인트:** 서버를 띄우고 WebSocket 왕복을 테스트.

```bash
# 터미널 A
WORKER_MODE=mock python run.py

# 터미널 B
python3 - << 'EOF'
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        await ws.send(json.dumps({"request": "Write a Python function with tests"}))
        while True:
            ev = json.loads(await ws.recv())
            print(ev["type"])
            if ev["type"] == "run_completed":
                print("result len:", len(ev["payload"]["result"])); break
asyncio.run(main())
EOF
```

`run_started → plan_created → step_started/step_log/... → run_completed` 순서로
이벤트가 흐르면 통과. (`pip install websockets`가 필요할 수 있음 — 테스트용)

---

# Stage 7 — 채팅 UI (index.html)

**목표:** 요청 입력 + 결과 확인 + **오른쪽에 라이브 오케스트레이션 파이프라인**.
외부 의존성 없는 단일 파일(vanilla JS + WebSocket)이라 로컬에서 완전히 오프라인 동작.

시그니처 요소는 오른쪽 파이프라인: 각 단계가 노드로 연결되고, 활성 노드는 점이 맥동하며
워커 출력이 실시간 스트리밍되고, 완료되면 초록색으로 바뀝니다 — 에이전트 간 바통이
넘어가는 걸 눈으로 봅니다.

`static/index.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Multi-Agent Orchestrator</title>
<style>
  /* ── Operations-console palette: deep ink, signal-colored status ───────── */
  :root {
    --bg:        #0b0f17;
    --panel:     #131926;
    --panel-2:   #0f1420;
    --line:      #232c3d;
    --line-soft: #1a2231;
    --ink:       #e6ebf2;
    --muted:     #8a97ab;
    --faint:     #5b6678;

    --idle:      #3a465c;   /* step not yet run          */
    --active:    #5cc8ff;   /* step running / streaming   */
    --running:   #f2b45c;   /* worker busy               */
    --done:      #57d38c;   /* step complete             */
    --failed:    #ff6b6b;   /* step errored              */

    --mono: ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code", Menlo, Consolas, monospace;
    --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }

  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: var(--bg);
    color: var(--ink);
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.5;
    display: flex;
    flex-direction: column;
  }

  /* ── Header ────────────────────────────────────────────────────────────── */
  header {
    display: flex; align-items: center; gap: 16px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--line);
    background: var(--panel-2);
    flex-wrap: wrap;
  }
  .brand { display: flex; align-items: baseline; gap: 10px; }
  .brand h1 {
    font-size: 15px; font-weight: 650; margin: 0; letter-spacing: .2px;
  }
  .brand .sub {
    font-family: var(--mono); font-size: 11px; color: var(--faint);
    text-transform: uppercase; letter-spacing: 1.5px;
  }
  .mode {
    font-family: var(--mono); font-size: 11px; letter-spacing: .5px;
    padding: 3px 9px; border-radius: 999px;
    border: 1px solid var(--line); color: var(--muted);
  }
  .mode.sdk  { color: var(--done);   border-color: color-mix(in srgb, var(--done) 45%, transparent); }
  .mode.mock { color: var(--running); border-color: color-mix(in srgb, var(--running) 45%, transparent); }
  .roster { display: flex; gap: 6px; margin-left: auto; flex-wrap: wrap; }
  .chip {
    font-family: var(--mono); font-size: 11px; color: var(--muted);
    padding: 3px 9px; border: 1px solid var(--line-soft); border-radius: 6px;
    background: var(--panel);
  }

  /* ── Two-pane body ─────────────────────────────────────────────────────── */
  main { flex: 1; display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1.05fr); min-height: 0; }
  .pane { min-height: 0; display: flex; flex-direction: column; }
  .pane.chat { border-right: 1px solid var(--line); }
  .pane-head {
    padding: 10px 18px; font-family: var(--mono); font-size: 11px;
    letter-spacing: 1.5px; text-transform: uppercase; color: var(--faint);
    border-bottom: 1px solid var(--line-soft);
  }

  /* ── Chat ──────────────────────────────────────────────────────────────── */
  .messages { flex: 1; overflow-y: auto; padding: 20px 18px; display: flex; flex-direction: column; gap: 16px; }
  .msg { max-width: 92%; }
  .msg .who {
    font-family: var(--mono); font-size: 10px; letter-spacing: 1px;
    text-transform: uppercase; color: var(--faint); margin-bottom: 5px;
  }
  .msg.user { align-self: flex-end; text-align: right; }
  .msg.user .bubble {
    background: color-mix(in srgb, var(--active) 14%, var(--panel));
    border: 1px solid color-mix(in srgb, var(--active) 30%, var(--line));
  }
  .bubble {
    display: inline-block; text-align: left;
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 12px; padding: 11px 14px; white-space: pre-wrap; word-break: break-word;
  }
  .bubble pre {
    font-family: var(--mono); font-size: 12.5px; background: #0a0e16;
    border: 1px solid var(--line-soft); border-radius: 8px;
    padding: 11px 12px; margin: 8px 0 4px; overflow-x: auto; white-space: pre;
  }
  .bubble code { font-family: var(--mono); font-size: 12.5px; }

  .composer { border-top: 1px solid var(--line); padding: 14px 16px; background: var(--panel-2); }
  .examples { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
  .example {
    font-size: 12px; color: var(--muted); cursor: pointer;
    padding: 4px 10px; border: 1px solid var(--line-soft); border-radius: 999px;
    background: var(--panel); transition: border-color .15s, color .15s;
  }
  .example:hover { color: var(--ink); border-color: var(--active); }
  .row { display: flex; gap: 10px; }
  textarea {
    flex: 1; resize: none; height: 46px; max-height: 160px;
    background: var(--panel); color: var(--ink);
    border: 1px solid var(--line); border-radius: 10px;
    padding: 12px 14px; font-family: var(--sans); font-size: 14px;
  }
  textarea:focus { outline: 2px solid color-mix(in srgb, var(--active) 55%, transparent); outline-offset: 1px; border-color: var(--active); }
  button.send {
    background: var(--active); color: #06121f; border: none; border-radius: 10px;
    padding: 0 20px; font-weight: 650; font-size: 14px; cursor: pointer; letter-spacing: .2px;
  }
  button.send:disabled { background: var(--idle); color: var(--faint); cursor: not-allowed; }

  /* ── Orchestration pipeline (the signature element) ────────────────────── */
  .orch { flex: 1; overflow-y: auto; padding: 18px; }
  .plan-note {
    font-size: 13px; color: var(--muted); background: var(--panel-2);
    border: 1px solid var(--line-soft); border-left: 2px solid var(--active);
    border-radius: 8px; padding: 10px 12px; margin-bottom: 18px;
  }
  .plan-note b { color: var(--ink); font-weight: 600; }
  .empty { color: var(--faint); font-size: 13px; margin-top: 8px; }

  .pipeline { position: relative; margin-left: 6px; }
  .node { position: relative; padding: 0 0 22px 26px; }
  /* the flow line connecting steps */
  .node::before {
    content: ""; position: absolute; left: 6px; top: 16px; bottom: -6px; width: 2px;
    background: var(--line); 
  }
  .node:last-child::before { display: none; }
  .node.done::before { background: color-mix(in srgb, var(--done) 55%, var(--line)); }

  .dot {
    position: absolute; left: 0; top: 3px; width: 13px; height: 13px; border-radius: 50%;
    background: var(--bg); border: 2px solid var(--idle);
  }
  .node.active .dot { border-color: var(--active); box-shadow: 0 0 0 4px color-mix(in srgb, var(--active) 18%, transparent); animation: pulse 1.3s ease-in-out infinite; }
  .node.done   .dot { border-color: var(--done); background: var(--done); }
  .node.failed .dot { border-color: var(--failed); background: var(--failed); }

  @keyframes pulse { 0%,100% { box-shadow: 0 0 0 3px color-mix(in srgb, var(--active) 22%, transparent);} 50% { box-shadow: 0 0 0 7px color-mix(in srgb, var(--active) 6%, transparent);} }

  .node-head { display: flex; align-items: center; gap: 8px; }
  .node-title { font-weight: 600; font-size: 14px; }
  .node-worker { font-family: var(--mono); font-size: 10px; letter-spacing: .5px; color: var(--faint); text-transform: uppercase; }
  .node-status {
    margin-left: auto; font-family: var(--mono); font-size: 10px; letter-spacing: .5px;
    text-transform: uppercase; color: var(--muted);
  }
  .node.active .node-status { color: var(--active); }
  .node.done   .node-status { color: var(--done); }
  .node.failed .node-status { color: var(--failed); }
  .node-instr { font-size: 12.5px; color: var(--muted); margin: 3px 0 0; }

  .tools { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .tool {
    font-family: var(--mono); font-size: 10.5px; color: var(--active);
    background: color-mix(in srgb, var(--active) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--active) 30%, transparent);
    border-radius: 5px; padding: 2px 7px;
  }
  .tool .arg { color: var(--faint); }

  .log {
    font-family: var(--mono); font-size: 11.5px; line-height: 1.55; color: var(--muted);
    background: var(--panel-2); border: 1px solid var(--line-soft); border-radius: 8px;
    padding: 9px 11px; margin-top: 9px; max-height: 190px; overflow-y: auto;
    white-space: pre-wrap; word-break: break-word;
  }
  .log:empty { display: none; }

  /* ── Scrollbars ────────────────────────────────────────────────────────── */
  ::-webkit-scrollbar { width: 9px; height: 9px; }
  ::-webkit-scrollbar-thumb { background: var(--line); border-radius: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }

  /* ── Responsive ────────────────────────────────────────────────────────── */
  @media (max-width: 860px) {
    main { grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; }
    .pane.chat { border-right: none; border-bottom: 1px solid var(--line); }
    .roster { display: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .node.active .dot { animation: none; }
  }
</style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>Multi-Agent Orchestrator</h1>
      <span class="sub">local</span>
    </div>
    <span id="mode" class="mode">connecting…</span>
    <div id="roster" class="roster"></div>
  </header>

  <main>
    <section class="pane chat">
      <div class="pane-head">Chat</div>
      <div id="messages" class="messages">
        <div class="msg assistant">
          <div class="who">Orchestrator</div>
          <div class="bubble">Send a request and I'll plan it, route it across the worker agents, and stream the whole thing on the right. Try one of the examples below.</div>
        </div>
      </div>
      <div class="composer">
        <div class="examples" id="examples"></div>
        <div class="row">
          <textarea id="input" placeholder="Ask for something that takes a few steps…" rows="1"></textarea>
          <button id="send" class="send">Send</button>
        </div>
      </div>
    </section>

    <section class="pane">
      <div class="pane-head">Orchestration</div>
      <div id="orch" class="orch">
        <div class="empty">No run yet. The plan and each worker's live output will appear here.</div>
      </div>
    </section>
  </main>

<script>
const $ = (s, r=document) => r.querySelector(s);
const messages = $("#messages");
const orch = $("#orch");
const input = $("#input");
const send = $("#send");

let ws, busy = false;
const nodes = {};   // step_id -> { el, log, tools, statusEl }

// ── Connection ─────────────────────────────────────────────────────────────
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
  ws.onclose = () => { setBusy(false); };
}
connect();

fetch("/api/info").then(r => r.json()).then(info => {
  const badge = $("#mode");
  badge.textContent = info.mode === "sdk" ? "SDK workers" : "Mock workers";
  badge.classList.add(info.mode);
  badge.title = info.mode === "mock"
    ? "Simulated agents — set ANTHROPIC_API_KEY and WORKER_MODE=sdk for real ones"
    : "Real Claude Agent SDK agents";
  $("#roster").innerHTML = info.workers
    .map(w => `<span class="chip" title="${w.description}">${w.title}</span>`).join("");
});

const EXAMPLES = [
  "Write a Python function to validate an email address, with tests",
  "Research the latest on small language models and summarize it",
  "Research CSV parsing pitfalls, then write a robust Python CSV reader with tests",
];
$("#examples").innerHTML = EXAMPLES
  .map(x => `<span class="example">${x}</span>`).join("");
$("#examples").querySelectorAll(".example").forEach(el =>
  el.onclick = () => { input.value = el.textContent; input.focus(); autosize(); });

// ── Sending ────────────────────────────────────────────────────────────────
function submit() {
  const text = input.value.trim();
  if (!text || busy || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ request: text }));
  input.value = ""; autosize();
}
send.onclick = submit;
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
});
function autosize() { input.style.height = "46px"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; }
input.addEventListener("input", autosize);

function setBusy(v) {
  busy = v; send.disabled = v; send.textContent = v ? "Running…" : "Send";
}

// ── Event handling ─────────────────────────────────────────────────────────
function handleEvent(ev) {
  const p = ev.payload || {};
  switch (ev.type) {
    case "run_started":  onRunStarted(p); break;
    case "plan_created": onPlanCreated(p); break;
    case "step_started": onStepStarted(p); break;
    case "step_log":     onStepLog(p); break;
    case "step_tool":    onStepTool(p); break;
    case "step_completed": onStepDone(p); break;
    case "step_failed":  onStepFailed(p); break;
    case "run_completed": onRunCompleted(p); break;
    case "run_failed":   onRunFailed(p); break;
  }
}

function onRunStarted(p) {
  setBusy(true);
  addMessage("user", "You", p.request);
  for (const k in nodes) delete nodes[k];
  orch.innerHTML = "";
}

function onPlanCreated(p) {
  const note = document.createElement("div");
  note.className = "plan-note";
  note.innerHTML = `<b>Plan</b> · ${escapeHtml(p.rationale || "")}`;
  orch.appendChild(note);

  const pipe = document.createElement("div");
  pipe.className = "pipeline";
  orch.appendChild(pipe);

  p.steps.forEach((s, i) => {
    const node = document.createElement("div");
    node.className = "node";
    const deps = s.depends_on && s.depends_on.length
      ? ` · needs ${s.depends_on.join(", ")}` : "";
    node.innerHTML = `
      <span class="dot"></span>
      <div class="node-head">
        <span class="node-title">${i+1}. ${escapeHtml(s.title)}</span>
        <span class="node-worker">${escapeHtml(s.worker)}${deps}</span>
        <span class="node-status">queued</span>
      </div>
      <p class="node-instr">${escapeHtml(s.instruction)}</p>
      <div class="tools"></div>
      <div class="log"></div>`;
    pipe.appendChild(node);
    nodes[s.id] = {
      el: node,
      log: node.querySelector(".log"),
      tools: node.querySelector(".tools"),
      statusEl: node.querySelector(".node-status"),
    };
  });
}

function onStepStarted(p) {
  const n = nodes[p.step_id]; if (!n) return;
  n.el.classList.add("active");
  n.statusEl.textContent = "running";
}
function onStepLog(p) {
  const n = nodes[p.step_id]; if (!n) return;
  n.log.textContent += p.text;
  n.log.scrollTop = n.log.scrollHeight;
}
function onStepTool(p) {
  const n = nodes[p.step_id]; if (!n) return;
  const t = document.createElement("span");
  t.className = "tool";
  t.innerHTML = `${escapeHtml(p.tool)} <span class="arg">${escapeHtml(p.detail || "")}</span>`;
  n.tools.appendChild(t);
}
function onStepDone(p) {
  const n = nodes[p.step_id]; if (!n) return;
  n.el.classList.remove("active"); n.el.classList.add("done");
  n.statusEl.textContent = "done";
}
function onStepFailed(p) {
  const n = nodes[p.step_id]; if (!n) return;
  n.el.classList.remove("active"); n.el.classList.add("failed");
  n.statusEl.textContent = "failed";
  n.log.textContent += "\n[error] " + (p.error || "");
}
function onRunCompleted(p) {
  addMessage("assistant", "Orchestrator", p.result);
  setBusy(false);
}
function onRunFailed(p) {
  addMessage("assistant", "Orchestrator", "The run failed: " + (p.error || "unknown error"));
  setBusy(false);
}

// ── Rendering helpers ───────────────────────────────────────────────────────
function addMessage(role, who, text) {
  const m = document.createElement("div");
  m.className = "msg " + role;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = renderMarkdown(text);
  m.innerHTML = `<div class="who">${escapeHtml(who)}</div>`;
  m.appendChild(bubble);
  messages.appendChild(m);
  messages.scrollTop = messages.scrollHeight;
}

// Minimal, safe markdown: escape everything, then re-enable ```code``` fences.
function renderMarkdown(text) {
  const esc = escapeHtml(text || "");
  return esc.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code>${code}</code></pre>`);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

autosize();
input.focus();
</script>
</body>
</html>
```

**체크포인트:** `python run.py` 후 <http://127.0.0.1:8000> 접속.
- 헤더에 `Mock workers` 배지 + 워커 4개 칩이 보인다.
- 예시 칩을 눌러 Send → 왼쪽에 요청, 오른쪽에 Plan → 단계가 순서대로 점등 →
  각 노드에 로그 스트리밍 + 툴 배지 → 초록 완료 → 왼쪽에 최종 답변.

---

# Stage 8 — 실제 Claude Agent SDK 모드로 전환

**목표:** 여태 mock으로 검증한 파이프라인을 진짜 에이전트로 돌린다. 코드는 한 줄도 안
바뀝니다 — config만.

```bash
# .env 편집
ANTHROPIC_API_KEY=sk-ant-...      # https://console.anthropic.com
WORKER_MODE=sdk                    # 또는 auto (키 있으면 자동 sdk)

python run.py
```

- 헤더 배지가 `SDK workers`로 바뀐다.
- 이제 각 단계는 실제 Claude Agent SDK 에이전트: 파일 읽기/쓰기, 셸 실행, 웹 검색 등
  `allowed_tools`에 준 도구를 실제로 사용합니다.
- `build_worker`가 `SdkWorker`를 반환하고, `SdkWorker.run()`이 `query()`를 호출하며,
  SDK 메시지 스트림이 Stage 1의 이벤트로 번역되어 같은 UI에 그대로 흐릅니다.

**첫 실전 요청 추천:** *"Write a Python function to validate an email address, with tests"*
— Coder → Reviewer → Writer 3단계, 3워커. 과제의 "3+ 단계 · 2+ 워커 워크플로우 1개
완주" 요건을 그대로 시연합니다.

---

# 요구사항 대응표 (제출용)

| 요구사항 | 구현 |
|---|---|
| 단일 머신 실행 | Python 1프로세스 + 브라우저, `127.0.0.1` |
| 오케스트레이션 직접 구현 | `orchestrator/orchestrator.py` — 순수 async Python, 프레임워크 없음 |
| SDK 워커 2개 이상 | `sdk_worker.py` + `registry.py`의 4개 역할(모두 SDK 백엔드) |
| 3+ 단계 · 2+ 워커 워크플로우 | Coder→Reviewer→Writer (및 4워커 research→build→review→write) |
| 과정 가시성 | `events.py` → WebSocket → UI 라이브 파이프라인 |
| 채팅 UI | `static/index.html` |
| 산출물: 소스코드 + 실행법 | 전체 코드 + README |

# 다음 확장 아이디어 (여유되면)

- **LLM 플래너:** `Planner.plan()`을 원시 Anthropic Messages API 호출로 교체해
  JSON 계획을 생성(오케스트레이터는 SDK-free 유지). Step 리스트만 같으면 됨.
- **병렬 실행:** 현재는 순차. `_in_execution_order`가 위상정렬을 하므로, 의존성 없는
  동일 레벨 단계를 `asyncio.gather`로 병렬화 가능.
- **재시도/가드레일:** 단계 실패 시 재시도, 또는 SDK hook으로 위험 명령 차단.