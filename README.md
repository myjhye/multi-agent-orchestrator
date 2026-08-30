# Local Multi-Agent Orchestrator

User types a request → system breaks it into steps → AI workers execute each step in order → results stream to the browser live.

All orchestration logic is built from scratch in async Python. No LangGraph, no CrewAI, no framework.

```
"Research CSV pitfalls, then write a parser that avoids them"

  → Researcher → Coder → Reviewer → Writer → final answer
```

## Workers

Five specialized agents. Four use the Claude Agent SDK; the Evaluator calls the Anthropic API directly.

| Worker | Role | Tools | Backend |
|---|---|---|---|
| **Researcher** | Gathers and cross-checks facts from the web | WebSearch, Read | SDK agent |
| **Coder** | Writes code to satisfy a spec | Read, Write, Edit, Bash | SDK agent |
| **Reviewer** | Reviews code from the previous step and writes tests | Read | SDK agent |
| **Writer** | Combines all prior outputs into the final answer | Read | SDK agent |
| **Evaluator** | Scores the final output for completeness, correctness, and quality | — | Direct API call |

## How It Works

```
Request → Planner → Orchestrator → EventBus → Chat UI
```

**Planner** reads the request, picks workers, sets the order. Two modes: rule-based (keyword matching) or LLM-based (Claude API, auto-fallback).

**Orchestrator** runs each step, passes the output forward, emits events. No domain logic — pure coordination.

**EventBus** streams typed events over WebSocket. The orchestrator doesn't know the UI exists.

**Chat UI** renders it all: left pane is chat, right pane is the live pipeline.

### Example

Request: *"What are the tradeoffs between REST and GraphQL?"*

| Step | Worker | What happens | Output |
|---|---|---|---|
| 1 | **Researcher** | Searches the web, cross-checks 11 sources | 8 tradeoff categories documented (performance, caching, over-fetching, versioning, tooling, learning curve, ...) |
| 2 | **Evaluator** | Scores the research for accuracy and completeness | 4/5 overall — flagged 10 missing areas (error handling, real-time, security, versioning, tooling) |
| 3 | **Writer** | Reads research + evaluation feedback, fills gaps | 10-section comparison with decision matrix, incorporating all flagged gaps |

The Evaluator's feedback directly improved the final output — Writer added 5 sections (error handling, real-time, security, versioning, tooling) that were absent from the original research.

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
         ├─ MockWorker (mock_worker.py) ← development/testing
         ├─ SdkWorker (sdk_worker.py)   ← Claude Agent SDK
         └─ ApiWorker (api_worker.py)   ← direct API (Evaluator)
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
│       ├── mock_worker.py     # Simulated worker
│       └── api_worker.py      # Direct Anthropic API (Evaluator)
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

Four issues found when connecting the mock-verified architecture to real Claude Agent SDK workers.

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

### 4. SDK agent couldn't read temp files for evaluation

The Evaluator worker, using the SDK agent loop, failed to read temp files containing upstream outputs — returning "permission error" instead of scoring the result.

**Fix:** Evaluator doesn't need tools, multi-turn loops, or file access. Replaced `SdkWorker` with `ApiWorker` — a direct Anthropic Messages API call that receives the full prompt as a message, returns a JSON score, and avoids the SDK CLI entirely.

```python
# Before — SDK agent loop, fails on temp file read
if name == "evaluator":
    return SdkWorker(spec, model=model)  # CLI subprocess → permission error

# After — direct API call, no tools needed
if name == "evaluator":
    return ApiWorker(spec, model=model)  # simple API call → JSON score
```
