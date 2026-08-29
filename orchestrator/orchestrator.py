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
        parts = [
            f"{step.instruction}\n\n"
            f'The user\'s request is:\n"{request}"\n\n'
            f"Complete this task now. Do not ask for clarification."
        ]
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
