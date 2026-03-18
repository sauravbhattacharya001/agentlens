"""Decorators for automatic agent and tool call tracking."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable


def _build_input(args: tuple, kwargs: dict) -> dict:
    """Serialize function arguments for tracking."""
    return {
        "args": [str(a) for a in args],
        "kwargs": {k: str(v) for k, v in kwargs.items()},
    }


def _make_tracker(
    fn: Callable,
    *,
    event_type: str,
    error_event_type: str,
    track_name_key: str,
    track_name: str,
    model: str | None = None,
    make_reasoning: Callable[[str, float], str] | None = None,
) -> Callable:
    """Build a sync or async wrapper that tracks function calls.

    This is the shared implementation behind :func:`track_agent` and
    :func:`track_tool_call`, eliminating the duplicated sync/async
    wrapper code that previously existed in each decorator.
    """

    def _do_track(
        *,
        is_error: bool,
        input_data: dict,
        output_data: dict,
        elapsed_ms: float,
    ) -> None:
        try:
            import agentlens

            kwargs: dict[str, Any] = {
                "event_type": error_event_type if is_error else event_type,
                "duration_ms": elapsed_ms,
            }
            # Agent-style tracking uses input_data/output_data + model + reasoning.
            # Tool-style tracking uses tool_name/tool_input/tool_output.
            if track_name_key == "model":
                kwargs["input_data"] = input_data
                kwargs["output_data"] = output_data
                kwargs["model"] = model
                if make_reasoning:
                    kwargs["reasoning"] = make_reasoning(
                        "failed" if is_error else "succeeded", elapsed_ms
                    )
            else:
                kwargs["tool_name"] = track_name
                kwargs["tool_input"] = input_data
                kwargs["tool_output"] = output_data

            agentlens.track(**kwargs)
        except RuntimeError:
            pass  # SDK not initialized, skip tracking

    if asyncio.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            input_data = _build_input(args, kwargs)
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                output_data = {"result": str(result) if result is not None else None}
                _do_track(
                    is_error=False,
                    input_data=input_data,
                    output_data=output_data,
                    elapsed_ms=elapsed_ms,
                )
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000
                _do_track(
                    is_error=True,
                    input_data=input_data,
                    output_data={"error": str(e), "error_type": type(e).__name__},
                    elapsed_ms=elapsed_ms,
                )
                raise

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        input_data = _build_input(args, kwargs)
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000
            output_data = {"result": str(result) if result is not None else None}
            _do_track(
                is_error=False,
                input_data=input_data,
                output_data=output_data,
                elapsed_ms=elapsed_ms,
            )
            return result
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            _do_track(
                is_error=True,
                input_data=input_data,
                output_data={"error": str(e), "error_type": type(e).__name__},
                elapsed_ms=elapsed_ms,
            )
            raise

    return wrapper


def track_agent(
    func: Callable | None = None,
    *,
    model: str | None = None,
    name: str | None = None,
):
    """Decorator to automatically track an agent function call.

    Supports both synchronous and asynchronous functions.

    Usage::

        @track_agent
        def my_agent(prompt):
            ...

        @track_agent(model="gpt-4")
        async def my_agent(prompt):
            ...
    """

    def decorator(fn: Callable) -> Callable:
        agent_name = name or fn.__name__
        return _make_tracker(
            fn,
            event_type="agent_call",
            error_event_type="agent_error",
            track_name_key="model",
            track_name=agent_name,
            model=model,
            make_reasoning=lambda status, ms: (
                f"Agent '{agent_name}' executed successfully in {ms:.1f}ms"
                if status == "succeeded"
                else f"Agent '{agent_name}' failed"
            ),
        )

    if func is not None:
        return decorator(func)
    return decorator


def track_tool_call(
    func: Callable | None = None,
    *,
    tool_name: str | None = None,
):
    """Decorator to automatically track a tool/function call.

    Supports both synchronous and asynchronous functions.

    Usage::

        @track_tool_call
        def search_web(query):
            ...

        @track_tool_call(tool_name="web_search")
        async def search_web(query):
            ...
    """

    def decorator(fn: Callable) -> Callable:
        resolved_name = tool_name or fn.__name__
        return _make_tracker(
            fn,
            event_type="tool_call",
            error_event_type="tool_error",
            track_name_key="tool",
            track_name=resolved_name,
        )

    if func is not None:
        return decorator(func)
    return decorator
