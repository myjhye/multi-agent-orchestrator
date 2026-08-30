# Local Multi-Agent Orchestrator

User types a request → system breaks it into steps → AI workers execute each step in order → results stream to the browser live.

All orchestration logic is built from scratch in async Python. No LangGraph, no CrewAI, no framework.

```
"Research CSV pitfalls, then write a parser that avoids them"

  → Researcher → Coder → Reviewer → Writer → final answer
```

## Workers

Four specialized agents, each backed by the Claude Agent SDK.

| Worker | Role | Tools |
|---|---|---|
| **Researcher** | Gathers and cross-checks facts from the web | WebSearch, Read |
| **Coder** | Writes code to satisfy a spec | Read, Write, Edit, Bash |
| **Reviewer** | Reviews code from the previous step and writes tests | Read |
| **Writer** | Combines all prior outputs into the final answer | Read |

## How It Works

```
Request → Planner → Orchestrator → EventBus → Chat UI
```

**Planner** reads the request, picks workers, sets the order. Two modes: rule-based (keyword matching) or LLM-based (Claude API, auto-fallback).

**Orchestrator** runs each step, passes the output forward, emits events. No domain logic — pure coordination.

**EventBus** streams typed events over WebSocket. The orchestrator doesn't know the UI exists.

**Chat UI** renders it all: left pane is chat, right pane is the live pipeline.

### Example

Request: *"Research CSV pitfalls, then write a parser that avoids them"*

| Step | Worker | What happens | Output |
|---|---|---|---|
| 1 | **Researcher** | Searches the web, cross-checks sources | 13 CSV pitfalls documented (encoding, BOM, quoted fields, ragged rows, ...) |
| 2 | **Coder** | Reads step 1 output, writes code addressing each pitfall | `robust_csv_parser.py` — encoding detection, RFC 4180 quote handling, malformed row recovery |
| 3 | **Reviewer** | Reads step 2 output, finds bugs, writes tests | 5 issues identified + pytest suite covering all 7 pitfall categories |
| 4 | **Writer** | Reads steps 1–3, assembles final answer | Pitfall summary → corrected code → test suite → caveats, in one structured response |

## Architecture

```
  Browser (static/index.html)
    │  WebSocket
    ▼
  FastAPI Server (server.py)
    │
    ▼
  Orchestrator (orchestrator.py)        ← from scratch
    ├─ Planner (planner.py)             ← rule | llm
    ├─ EventBus (events.py)             ← visibility
    └─ Workers (registry.py)
         ├─ MockWorker (mock_worker.py) ← zero-cost testing
         └─ SdkWorker (sdk_worker.py)   ← Claude Agent SDK
```

## Project Structure

```
multi-agent-orchestrator/
├── run.py                     # Entry point
├── requirements.txt
├── .env.example
├── orchestrator/
│   ├── server.py              # FastAPI + WebSocket
│   ├── orchestrator.py        # DAG execution engine
│   ├── planner.py             # Request → execution plan
│   ├── events.py              # Event types + async EventBus
│   ├── config.py              # .env loader
│   └── workers/
│       ├── base.py            # Worker protocol
│       ├── registry.py        # Agent roster + factory
│       ├── sdk_worker.py      # Claude Agent SDK wrapper
│       └── mock_worker.py     # Simulated worker
└── static/
    └── index.html             # Chat UI + live pipeline
```

## Setup

```bash
git clone https://github.com/myjhye/multi-agent-orchestrator.git
cd multi-agent-orchestrator
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # edit .env — options are documented inside
python run.py                    # → http://127.0.0.1:8000
```

## SDK Integration: Issues & Fixes

Three issues found when connecting the mock-verified architecture to real Claude Agent SDK workers.

### 1. Workers ignored the user request

SDK agents skipped the `=== User request ===` delimiter. Mock workers process strings as data; SDK agents interpret them as conversation.

**Fix:** Explicit `YOUR TASK:` label, upstream outputs placed at prompt top, `"Do not ask for clarification"` added.

```python
# Before
parts = [step.instruction, "", "=== User request ===", request]

# After
parts.append(f"YOUR TASK: {task_desc}")
parts.append("Do not ask for clarification. Do not search the filesystem.")
```

### 2. Reviewer explored the entire project

With Bash/Write/Edit available, the agent ran `git status`, read every file, and installed packages instead of reviewing the provided code.

**Fix:** `allowed_tools=["Read"]`, `max_turns` 14→4, scope constraint in `system_prompt`.

### 3. Single worker failure killed the entire run

One timeout aborted all downstream steps, discarding earlier results.

**Fix:** Catch exceptions in `_run_step`, return fallback string instead of re-raising. Failed step shows red in UI; workflow continues.
