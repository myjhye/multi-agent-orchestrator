# Local Multi-Agent Orchestrator

A lightweight, local multi-agent orchestration engine built **from scratch** in pure asynchronous Python, powered by the **Claude Agent SDK** and FastAPI.

It features full **process visibility** with a real-time WebSocket event stream and an interactive single-page dashboard featuring a live DAG pipeline visualizer.

## Overview

The **Local Multi-Agent Orchestrator** decomposes natural language user queries into Directed Acyclic Graph (DAG) task pipelines, schedules worker agents in topological dependency order, feeds upstream outputs forward as context to dependent steps, and streams every thought, log, tool call, and state transition to the browser in real time.

### Core Design Principles
1. **Bottom-Up Construction**: Visibility layer (`events.py`) &rarr; Worker interface & registry (`workers/`) &rarr; Planner (`planner.py`) &rarr; Orchestrator coordination engine (`orchestrator.py`) &rarr; FastAPI server (`server.py`) &rarr; Chat UI (`index.html`).
2. **Mock-First Strategy**: Zero-credit testing out-of-the-box (`WORKER_MODE=mock`). Flip a single environment variable (`WORKER_MODE=sdk`) to run real Claude Agent SDK workers with zero code changes.
3. **Intelligence-less Orchestrator**: The orchestration engine has no hardcoded agent prompt intelligence. It focuses purely on scheduling, dependency graph resolution, payload passing, event broadcasting, and result aggregation.
4. **Dual-Mode Planner**: Rule-based keyword classifier by default; optionally calls the Anthropic Messages API to generate dynamic execution plans. Falls back to rule-based automatically on API failure. Controlled via `PLANNER_MODE` in `.env`.

## Requirements Mapping

| Requirement | Implementation Detail |
|---|---|
| **Single-Machine Execution** | Single Python 3.10+ process running FastAPI & Uvicorn locally on `127.0.0.1:8000`. |
| **Orchestration from Scratch** | `orchestrator/orchestrator.py` — Pure async Python DAG engine without external multi-agent frameworks (e.g., CrewAI, LangChain, AutoGen). |
| **2+ SDK Workers** | `sdk_worker.py` + `registry.py` define 4 specialized workers backed by the Claude Agent SDK (`Researcher`, `Coder`, `Reviewer`, `Writer`). |
| **3+ Step, 2+ Worker Workflow** | Supports multi-step DAGs (e.g., `Coder` &rarr; `Reviewer` &rarr; `Writer` or `Researcher` &rarr; `Coder` &rarr; `Reviewer` &rarr; `Writer`). |
| **Process Visibility** | Typed `EventBus` (`events.py`) streams every execution step, log chunk, tool invocation (`Write`, `Bash`, `WebSearch`), and status transition over WebSocket. |
| **Chat UI** | `static/index.html` — Responsive dark console UI with dual-pane chat and live DAG pipeline visualizer. |
| **Mock / SDK Mode Switch** | Configurable via `.env` (`WORKER_MODE=mock` / `WORKER_MODE=sdk` / `WORKER_MODE=auto`). |
| **Dual-Mode Planner** | `planner.py` supports `PLANNER_MODE=rule` (keyword-based, deterministic) and `PLANNER_MODE=llm` (Claude API, dynamic). Auto-fallback on failure. |

## Architecture Diagram

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

## Project Directory Structure

```
multi-agent-orchestrator/
├── run.py                       # Main entry point to launch the local web server
├── requirements.txt             # Python dependencies (FastAPI, uvicorn, claude-agent-sdk, python-dotenv)
├── .env.example                 # Environment configuration template
├── .env                         # Active environment configuration
├── README.md                    # Project documentation
├── orchestrator/
│   ├── __init__.py              # Orchestrator package exports
│   ├── server.py                # FastAPI web server & WebSocket (/ws) endpoint
│   ├── orchestrator.py          # Coordination & DAG execution engine (built from scratch)
│   ├── planner.py               # Request classification & DAG plan generator
│   ├── events.py                # Visibility layer: Event types, Event envelope, and async EventBus
│   ├── config.py                # Environment configuration loader (Settings)
│   └── workers/
│       ├── __init__.py          # Workers package exports
│       ├── base.py              # WorkerSpec metadata & Worker protocol interface
│       ├── registry.py          # Agent roster (Researcher, Coder, Reviewer, Writer) & build_worker factory
│       ├── sdk_worker.py        # Real worker backed by the Claude Agent SDK query() stream
│       └── mock_worker.py       # Simulated worker for offline/zero-cost pipeline verification
└── static/
    └── index.html               # Single-page UI with chat & live orchestration visualizer
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

### 3. Run in Mock Mode (Default / Zero-Cost Trial)

By default, `.env` is configured with `WORKER_MODE=auto` (or `WORKER_MODE=mock`). Without an `ANTHROPIC_API_KEY`, it automatically runs in **Mock Mode**, allowing you to test the complete multi-agent pipeline and live UI visualizer at zero API cost.

Start the server:

```bash
python run.py
```

Open your browser and navigate to:
[http://127.0.0.1:8000](http://127.0.0.1:8000)

**What you will see:**
- Header badge displays **`Mock workers`** and worker chips (`Researcher`, `Coder`, `Reviewer`, `Writer`).
- Click any example query (e.g. *"Write a Python function to validate an email address, with tests"*).
- Click **Send**.
- Watch the **Orchestration** panel on the right build the Plan DAG, pulse active nodes, stream logs, display tool call badges (`Write solution.py`, `Bash pytest -q`), turn green (`DONE`), and output the final response in the left chat.

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

Four issues discovered when connecting the mock-verified architecture to real Claude Agent SDK workers.

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

### 5. LLM-based planner

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
