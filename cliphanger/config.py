"""Load config.yaml and build the generator / judge clients from it."""
from __future__ import annotations

from pathlib import Path

import yaml

from .llm import LLM

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def make_llm(cfg: dict, role: str, override: str | None = None, log: list | None = None) -> LLM:
    """role = 'generator' or 'judge'. `override` (e.g. from --model) replaces the whole chain;
    a comma-separated override is a chain of its own: "gemini/a,gemini/b"."""
    m = cfg["model"]
    chain = [s.strip() for s in override.split(",")] if override else [m[role], *m.get(f"{role}_fallbacks", [])]
    return LLM(chain[0], chain[1:], reasoning_effort=m.get(f"{role}_reasoning"),
               timeout=m.get("timeout_seconds", 150), log=log, rpm=m.get("requests_per_minute"))
