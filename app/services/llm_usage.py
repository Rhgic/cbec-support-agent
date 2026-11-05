"""Per-node LLM usage, isolated across threads and async tasks."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class Usage:
    token_in: int = 0
    token_out: int = 0
    cost_usd: float = 0.0


current_usage: ContextVar[Usage | None] = ContextVar("llm_usage", default=None)


def add_usage(token_in: int, token_out: int, cost_usd: float) -> None:
    usage = current_usage.get()
    if usage is not None:
        usage.token_in += token_in
        usage.token_out += token_out
        usage.cost_usd += cost_usd
