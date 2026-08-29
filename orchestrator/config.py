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
    planner_mode: str          # "llm", "rule", or resolved from "auto"
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

    planner_requested = os.getenv("PLANNER_MODE", "auto").strip().lower()
    if planner_requested == "llm":
        planner_mode = "llm"
    elif planner_requested == "rule":
        planner_mode = "rule"
    else:
        planner_mode = "llm" if api_key else "rule"

    return Settings(
        worker_mode=mode,
        requested_mode=requested,
        has_api_key=bool(api_key),
        model=os.getenv("WORKER_MODEL", "").strip() or None,
        planner_mode=planner_mode,
        host=os.getenv("HOST", "127.0.0.1").strip(),
        port=int(os.getenv("PORT", "8000")),
    )
