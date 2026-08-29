# Local Multi-Agent Orchestrator

## Overview

User types a request → system breaks it into steps → specialized AI workers execute each step in order → results stream to the browser live.

```
"Research CSV pitfalls, then write a parser that avoids them"

  → Researcher → Coder → Reviewer → Writer → final answer
```

Each worker's output feeds into the next. The orchestrator handles sequencing and data passing only — all domain work is the workers' job.

### Layers

**EventBus** — Every action emits a typed event. Who consumes it (terminal, WebSocket, monitoring) is a separate concern.

**Worker protocol** — All workers share the same `worker.run()` interface. Mock and real SDK workers are interchangeable; the orchestrator doesn't know the difference.

**Planner** — Turns a request into an execution plan. Rule-based (keyword matching, zero-cost) or LLM-based (Claude API, auto-fallback). The only place intelligence enters the orchestration.

**Orchestrator** — Executes the plan in dependency order, passes outputs forward, broadcasts events. No domain judgment, no framework — pure async Python.

## Architecture

```
+-----------------------------------------------------------------------------------+
|                                 Browser Chat UI                                   |
|                             (static/index.html)                                   |
+----------------------------------------+------------------------------------------+
                                         | 1. Submit Request via WS
                                         v
+-----------------------------------------------------------------------------------+
|                                FastAPI Server                                     |
|                           (orchestrator/server.py)                                |
+----------------------------------------+------------------------------------------+
                                         | 2. Spawn Run
                                         v
+-----------------------------------------------------------------------------------+
|                        Orchestrator Engine (from scratch)                         |
|                         (orchestrator/orchestrator.py)                            |
+-------------------+--------------------+--------------------+---------------------+
                    |                    |                    |
 3. Decompose Plan  |                    | 4. Execute Steps   | 5. Stream Events
                    v                    |    in Topological  |    (Real-time)
+-----------------------+                |    Order           v
|      Planner          |                v            +---------------+
| (orchestrator/        |        +---------------+    |   EventBus    |
|  planner.py)          |        | Worker Factory|    | (orchestrator/|
|                       |        | (registry.py) |    |  events.py)   |
|  rule | llm (auto)    |        +-------+-------+    +-------+-------+
+-----------------------+                |                    |
                      +------------------+------------------+ | 6. Forward JSON
                      |                                     | |    to Browser WS
                      v                                     v |
        +---------------------------+     +-----------------+----+
        |        MockWorker         |     |    SdkWorker    |    |
        |  (simulated, zero-cost)   |     | (Claude Agent   | <--+
        |                           |     |      SDK)       |
        +---------------------------+     +-----------------+
```

## Project Structure

```
multi-agent-orchestrator/
├── run.py                          # Entry point
├── requirements.txt
├── .env.example
├── orchestrator/
│   ├── server.py                   # FastAPI + WebSocket
│   ├── orchestrator.py             # DAG execution engine
│   ├── planner.py                  # Request → execution plan
│   ├── events.py                   # Event types + async EventBus
│   ├── config.py                   # .env loader
│   └── workers/
│       ├── base.py                 # Worker protocol
│       ├── registry.py             # Agent roster + factory
│       ├── sdk_worker.py           # Claude Agent SDK wrapper
│       └── mock_worker.py          # Simulated worker
└── static/
    └── index.html                  # Chat UI + live pipeline
```

## Setup & Run Guide

### 1. Prerequisites
- **Python 3.10+** (Python 3.11 recommended)
- Git

### 2. Installation

Clone the repository and create a virtual environment:

```bash
git clone https://github.com/myjhye/multi-agent-orchestrator.git
cd multi-agent-orchestrator

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment configuration
cp .env.example .env
```

### 3. Run in Mock Mode (Zero-Cost)

Without an `ANTHROPIC_API_KEY`, the system runs in mock mode automatically — simulated workers, real orchestration logic, full UI.

```bash
python run.py
# Open http://127.0.0.1:8000
```

### 4. Switch to SDK Mode (Real Claude Agent SDK Agents)

To run actual LLM agents powered by the **Claude Agent SDK**:

1. Obtain an Anthropic API Key from [console.anthropic.com](https://console.anthropic.com).
2. Edit your `.env` file:

```bash
# Set your Anthropic API Key
ANTHROPIC_API_KEY=sk-ant-api03-...

# Enable SDK worker mode (or leave as auto)
WORKER_MODE=sdk
```

3. Restart the server:

```bash
python run.py
```

4. Refresh [http://127.0.0.1:8000](http://127.0.0.1:8000):
- Header badge updates to **`SDK workers`**.
- Worker agents now execute real Claude Agent SDK agentic loops with access to system tools (`Read`, `Write`, `Edit`, `Bash`, `WebSearch`).

### 5. Planner Mode

The planner decomposes user requests into execution plans. Two modes are available:

| Mode | Behavior |
|---|---|
| `rule` | Keyword-based classification. Deterministic, zero-cost. Default when no API key is set. |
| `llm` | Calls the Anthropic Messages API to generate a dynamic JSON plan. Requires `ANTHROPIC_API_KEY`. Falls back to `rule` on failure. |
| `auto` | Uses `llm` if `ANTHROPIC_API_KEY` is set, otherwise `rule`. **(default)** |

Configure in `.env`:

```bash
PLANNER_MODE=auto
```

## SDK Integration: Issues & Fixes

Seven issues discovered when connecting the mock-verified architecture to real Claude Agent SDK workers on Windows.

### 1. Workers ignored the user request

SDK agents treated the `=== User request ===` delimiter as metadata and skipped it. Mock workers process strings as data; SDK agents interpret them as conversation.

**Fix:** Label the request explicitly with `YOUR TASK:`, add `"Do not ask for clarification"`, place upstream outputs at the top of the prompt.

```python
# Before
parts = [step.instruction, "", "=== User request ===", request]
for dep in step.depends_on:
    if dep in outputs:
        parts += ["", f"=== Output from step {dep} ===", outputs[dep]]

# After
if step.depends_on:
    parts.append("Here is the work produced by previous steps. Use it directly:\n")
    for dep in step.depends_on:
        if dep in outputs:
            parts.append(f"[STEP OUTPUT (Step {dep})]")
            parts.append(outputs[dep])
            parts.append(f"[END STEP OUTPUT (Step {dep})]\n")
parts.append(f"YOUR TASK: {task_desc}")
parts.append("Do not ask for clarification. Do not search the filesystem.")
```

### 2. Reviewer explored the entire project

With `Bash`, `Write`, and `Edit` in `allowed_tools`, the agent ran `git status`, read every source file, and installed packages instead of reviewing the provided code.

**Fix:** Strip tools to `["Read"]` only, cut `max_turns` from 14 to 4, add explicit scope constraint to `system_prompt`.

```python
# Before
allowed_tools=["Read", "Write", "Edit", "Bash"],
max_turns=14,

# After
allowed_tools=["Read"],
max_turns=4,
system_prompt="...Review ONLY that provided code. Do not search the filesystem..."
```

### 3. Windows command-line length limit (~8,191 chars)

The SDK invokes a CLI subprocess internally. Coder's output was long enough that the Reviewer's composed prompt exceeded the Windows argument length cap.

**Fix:** In `sdk_worker.py`, write prompts over 4,000 chars to a temp `.md` file and pass the file path instead. Clean up in `finally`.

```python
# Before
async for message in query(prompt=task, options=options):
    ...

# After
if len(task) > 4000:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                      delete=False, encoding="utf-8")
    tmp.write(task); tmp.close()
    prompt_arg = f"Read the file at {tmp.name} for your complete instructions."
try:
    async for message in query(prompt=prompt_arg, options=options):
        ...
finally:
    if tmp_path:
        os.unlink(tmp_path)
```

### 4. Single worker failure killed the entire run

A timeout or exception in one step raised and aborted all downstream steps — even when earlier steps had already produced usable output.

**Fix:** Catch exceptions in `_run_step` and return a fallback string instead of re-raising. The failed step still emits `STEP_FAILED` (shown as red in the UI), but the workflow continues.

```python
# Before
except asyncio.TimeoutError:
    await bus.emit(EventType.STEP_FAILED, ...)
    raise

# After
except (asyncio.TimeoutError, Exception) as exc:
    await bus.emit(EventType.STEP_FAILED, ...)
    result = "(This step could not complete. Proceed with outputs from other steps.)"
```

### 5. Rule-based planner couldn't handle novel requests

The original rule-based planner classified requests by keyword matching. This worked but couldn't handle ambiguous or novel requests outside the predefined keyword buckets.

**Fix:** Added a dual-mode `Planner` that optionally calls the Anthropic Messages API to generate a JSON execution plan. The orchestrator, workers, and event system required zero changes — `Planner.plan()` was the single seam designed for this swap from the start. API failure triggers automatic fallback to rule-based planning.

```python
# Before — rule-based only
class Planner:
    def plan(self, request: str) -> Plan:
        text = request.lower()
        if any(w in text for w in _CODE_WORDS):
            return self._code_review(request)
        ...

# After — dual-mode with fallback
class Planner:
    def __init__(self, mode: str = "rule") -> None:
        self.mode = mode

    def plan(self, request: str) -> Plan:
        if self.mode == "llm":
            try:
                return self._plan_with_llm(request)
            except Exception:
                return self._plan_with_rules(request)
        return self._plan_with_rules(request)
```

### 6. Newlines in system_prompt broke Windows CLI

The Reviewer's `system_prompt` contained literal `\n` characters for formatting. On Windows, the SDK passes `system_prompt` as a CLI argument, and the newlines split the command mid-argument — causing `"Input must be provided"` errors.

**Fix:** Rewrite all `system_prompt` strings as single continuous paragraphs without `\n`.

```python
# Before
system_prompt=(
    "IMPORTANT RULES:\n"
    "1. Do NOT create any files.\n"
    "2. Do NOT run any commands.\n"
),

# After
system_prompt=(
    "Do NOT create any files. Do NOT run any commands. "
    "ONLY respond with text."
),
```

### 7. Long upstream outputs broke CLI argument passing

Even after truncation, code-heavy upstream outputs (backticks, quotes, special characters) corrupted CLI argument escaping on Windows.

**Fix:** Lower the temp-file threshold from 4,000 to 500 chars in `sdk_worker.py`, and cap each upstream output to 2,000 chars in `_compose_prompt`. This ensures Coder (short prompt) goes direct while Reviewer/Writer (with upstream code) always use the temp-file path.

```python
# sdk_worker.py — threshold change
# Before
if len(task) > 4000:

# After
if len(task) > 500:

# orchestrator.py — output truncation
MAX_OUTPUT_LEN = 2000
if len(output) > MAX_OUTPUT_LEN:
    output = output[:MAX_OUTPUT_LEN] + "\n...(truncated)"
```
