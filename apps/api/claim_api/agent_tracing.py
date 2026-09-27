"""Mark safe, metadata-only Logfire spans as agent runs."""

import os
from contextlib import contextmanager
from functools import wraps
from typing import Callable, Iterator, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


@contextmanager
def agent_span(name: str, agent_name: str, version: str, provider: str,
               **metadata: str) -> Iterator[None]:
    if not os.getenv("LOGFIRE_TOKEN", "").strip():
        yield
        return

    import logfire
    from opentelemetry import trace

    with logfire.span(name, **metadata):
        # Logfire normalizes keyword argument names (dots become underscores).
        # Agent discovery reads the original OpenTelemetry attribute names.
        span = trace.get_current_span()
        for key, value in {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent_name,
            "gen_ai.agent.version": version,
            "gen_ai.provider.name": provider,
            "openinference.span.kind": "AGENT",
        }.items():
            span.set_attribute(key, value)
        yield


@contextmanager
def diagnostic_span(name: str, **metadata: str | int) -> Iterator[object | None]:
    """Trace timing and bounded metadata without claim text or caller identity."""
    if not os.getenv("LOGFIRE_TOKEN", "").strip():
        yield None
        return

    import logfire
    from opentelemetry import trace

    with logfire.span(name, **metadata):
        yield trace.get_current_span()


def trace_operation(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Time a metadata-free operation inside the current request trace."""
    def decorate(operation: Callable[P, R]) -> Callable[P, R]:
        @wraps(operation)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with diagnostic_span(name):
                return operation(*args, **kwargs)
        return wrapped
    return decorate
