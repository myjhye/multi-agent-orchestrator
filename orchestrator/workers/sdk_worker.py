"""
sdk_worker.py — a worker backed by the Claude Agent SDK.

This is the required "worker built with the Claude Agent SDK". Each instance
wraps one call to the SDK's `query()` async generator, translating the stream
of SDK messages into the orchestrator's log/tool callbacks.

The SDK talks to Claude for us: it runs the agent loop, executes built-in tools
(Read, Write, Edit, Bash, WebSearch, ...), and streams messages back.
"""

from __future__ import annotations

import os
import tempfile

from .base import LogCallback, ToolCallback, WorkerSpec

# Imported lazily inside run() so the app can still boot in mock mode on a
# machine where the SDK / its CLI isn't installed.


class SdkWorker:
    def __init__(self, spec: WorkerSpec, model: str | None = None) -> None:
        self.spec = spec
        self.model = model

    async def run(self, task: str, on_log: LogCallback, on_tool: ToolCallback) -> str:
        try:
            from claude_agent_sdk import (  # type: ignore
                query,
                ClaudeAgentOptions,
                AssistantMessage,
                TextBlock,
            )
        except ImportError as e:
            raise RuntimeError(
                "claude_agent_sdk is not installed in the current environment."
            ) from e

        options_kwargs = dict(
            system_prompt=self.spec.system_prompt,
            allowed_tools=self.spec.allowed_tools,
            permission_mode="acceptEdits",  # unattended: auto-approve file edits
            max_turns=self.spec.max_turns,
        )
        if self.spec.model:
            options_kwargs["model"] = self.spec.model
        elif self.model:
            options_kwargs["model"] = self.model

        options = ClaudeAgentOptions(**options_kwargs)

        # Windows command-line length limit workaround:
        # Write long prompts to a temp file and pass the file path
        prompt_arg = task
        tmp_path = None
        if len(task) > 500:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            )
            tmp.write(task)
            tmp.close()
            tmp_path = tmp.name
            prompt_arg = f"Read the file at {tmp_path} for your complete instructions and context."

        collected: list[str] = []
        try:
            async for message in query(prompt=prompt_arg, options=options):
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
                    # ResultMessage.result duplicates TextBlock text, so do not append to collected.
                    result_text = getattr(message, "result", None)
                    if isinstance(result_text, str) and result_text.strip():
                        await on_log(result_text)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        return "".join(collected).strip()


def _short_repr(value: object, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."
