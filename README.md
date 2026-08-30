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

Request: *"Research password hashing best practices, then implement a secure hasher in Python with tests"*

| Step | Worker | What happens | Output |
|---|---|---|---|
| 1 | **Researcher** | Searches web, cross-checks OWASP/NIST/RFC sources | Best practices: Argon2id primary, bcrypt fallback, OWASP baseline params, salting strategy |
| 2 | **Coder** | Reads step 1, implements hasher following recommendations | `secure_hasher.py` — Argon2id (OWASP params) + bcrypt fallback, auto-detect on verify, 72-byte guard |
| 3 | **Reviewer** | Reads step 2, finds issues, writes tests | 5 issues found (unused import, missing constant-time docs, ...) + pytest suite with edge cases |
| 4 | **Writer** | Reads steps 1–3, assembles final answer | Best practices overview → corrected implementation → test suite → installation instructions |
| 5 | **Evaluator** | Scores final answer against original request | `{"completeness": 4, "correctness": 4, "quality": 4, "overall": 4}` + improvement notes |

Each step's output feeds into the next. The Evaluator is auto-appended to every workflow and scores the result in the UI.

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

### 4. Evaluator failed to read temp files via SDK agent

The Evaluator, running as an SDK agent, couldn't read temp files containing upstream outputs — returning "permission error" instead of scoring.

**Fix:** The Evaluator doesn't need tools or multi-turn agent loops. Replaced `SdkWorker` with `ApiWorker` — a direct Anthropic Messages API call. Additionally, the orchestrator now auto-appends an Evaluator step to every workflow regardless of planner mode, so both rule-based and LLM-based plans always get scored.

```python
# ApiWorker — no CLI subprocess, no temp files, no tools
class ApiWorker:
    async def run(self, task, on_log, on_tool):
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=self.model,
            system=self.spec.system_prompt,
            messages=[{"role": "user", "content": task}],
        )
        return response.content[0].text
```
