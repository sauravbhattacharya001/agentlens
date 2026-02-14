"""Decorators for automatic agent and tool call tracking."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable


def track_agent(func: Callable | None = None, *, model: str | None = None, name: str | None = None):
    """Decorator to automatically track an agent function call.
    
    Supports both synchronous and asynchronous functions.
    
    Usage:
        @track_agent
        def my_agent(prompt):
            ...
        
        @track_agent(model="gpt-4")
        async def my_agent(prompt):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        agent_name = name or fn.__name__

        def _track_success(input_data: dict, output_data: dict, elapsed_ms: float) -> None:
            try:
                import agentlens
                agentlens.track(
                    event_type="agent_call",
                    input_data=input_data,
                    output_data=output_data,
                    model=model,
                    duration_ms=elapsed_ms,
                    reasoning=f"Agent '{agent_name}' executed successfully in {elapsed_ms:.1f}ms",
                )
            except RuntimeError:
                pass  # SDK not initialized, skip tracking

        def _track_error(input_data: dict, error: Exception, elapsed_ms: float) -> None:
            try:
                import agentlens
                agentlens.track(
                    event_type="agent_error",
                    input_data=input_data,
                    output_data={"error": str(error), "error_type": type(error).__name__},
                    model=model,
                    duration_ms=elapsed_ms,
                    reasoning=f"Agent '{agent_name}' failed with {type(error).__name__}: {error}",
                )
            except RuntimeError:
                pass

        def _build_input(args: tuple, kwargs: dict) -> dict:
            return {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}}

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                input_data = _build_input(args, kwargs)
                start = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    output_data = {"result": str(result) if result is not None else None}
                    _track_success(input_data, output_data, elapsed_ms)
                    return result
                except Exception as e:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    _track_error(input_data, e, elapsed_ms)
                    raise

            return async_wrapper
        else:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                input_data = _build_input(args, kwargs)
                start = time.perf_counter()
                try:
                    result = fn(*args, **kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    output_data = {"result": str(result) if result is not None else None}
                    _track_success(input_data, output_data, elapsed_ms)
                    return result
                except Exception as e:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    _track_error(input_data, e, elapsed_ms)
                    raise

            return wrapper
    
    if func is not None:
        return decorator(func)
    return decorator


def track_tool_call(func: Callable | None = None, *, tool_name: str | None = None):
    """Decorator to automatically track a tool/function call.
    
    Supports both synchronous and asynchronous functions.
    
    Usage:
        @track_tool_call
        def search_web(query):
            ...
        
        @track_tool_call(tool_name="web_search")
        async def search_web(query):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        name = tool_name or fn.__name__

        def _track_success(tool_input: dict, tool_output: dict, elapsed_ms: float) -> None:
            try:
                import agentlens
                agentlens.track(
                    event_type="tool_call",
                    tool_name=name,
                    tool_input=tool_input,
                    tool_output=tool_output,
                    duration_ms=elapsed_ms,
                )
            except RuntimeError:
                pass

        def _track_error(tool_input: dict, error: Exception, elapsed_ms: float) -> None:
            try:
                import agentlens
                agentlens.track(
                    event_type="tool_error",
                    tool_name=name,
                    tool_input=tool_input,
                    tool_output={"error": str(error), "error_type": type(error).__name__},
                    duration_ms=elapsed_ms,
                )
            except RuntimeError:
                pass

        def _build_input(args: tuple, kwargs: dict) -> dict:
            return {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}}

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tool_input = _build_input(args, kwargs)
                start = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    tool_output = {"result": str(result) if result is not None else None}
                    _track_success(tool_input, tool_output, elapsed_ms)
                    return result
                except Exception as e:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    _track_error(tool_input, e, elapsed_ms)
                    raise

            return async_wrapper
        else:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                tool_input = _build_input(args, kwargs)
                start = time.perf_counter()
                try:
                    result = fn(*args, **kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    tool_output = {"result": str(result) if result is not None else None}
                    _track_success(tool_input, tool_output, elapsed_ms)
                    return result
                except Exception as e:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    _track_error(tool_input, e, elapsed_ms)
                    raise

            return wrapper
    
    if func is not None:
        return decorator(func)
    return decorator
