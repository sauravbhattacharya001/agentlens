"""Decorators for automatic agent and tool call tracking."""

from __future__ import annotations

import functools
import time
from typing import Any, Callable


def track_agent(func: Callable | None = None, *, model: str | None = None, name: str | None = None):
    """Decorator to automatically track an agent function call.
    
    Usage:
        @track_agent
        def my_agent(prompt):
            ...
        
        @track_agent(model="gpt-4")
        def my_agent(prompt):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import agentops
            
            agent_name = name or fn.__name__
            start = time.perf_counter()
            
            # Capture input
            input_data = {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}}
            
            try:
                result = fn(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                
                # Capture output
                output_data = {"result": str(result) if result is not None else None}
                
                try:
                    agentops.track(
                        event_type="agent_call",
                        input_data=input_data,
                        output_data=output_data,
                        model=model,
                        duration_ms=elapsed_ms,
                        reasoning=f"Agent '{agent_name}' executed successfully in {elapsed_ms:.1f}ms",
                    )
                except RuntimeError:
                    pass  # SDK not initialized, skip tracking
                
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000
                try:
                    agentops.track(
                        event_type="agent_error",
                        input_data=input_data,
                        output_data={"error": str(e), "error_type": type(e).__name__},
                        model=model,
                        duration_ms=elapsed_ms,
                        reasoning=f"Agent '{agent_name}' failed with {type(e).__name__}: {e}",
                    )
                except RuntimeError:
                    pass
                raise
        
        return wrapper
    
    if func is not None:
        return decorator(func)
    return decorator


def track_tool_call(func: Callable | None = None, *, tool_name: str | None = None):
    """Decorator to automatically track a tool/function call.
    
    Usage:
        @track_tool_call
        def search_web(query):
            ...
        
        @track_tool_call(tool_name="web_search")
        def search_web(query):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import agentops
            
            name = tool_name or fn.__name__
            start = time.perf_counter()
            
            tool_input = {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}}
            
            try:
                result = fn(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                
                tool_output = {"result": str(result) if result is not None else None}
                
                try:
                    agentops.track(
                        event_type="tool_call",
                        tool_name=name,
                        tool_input=tool_input,
                        tool_output=tool_output,
                        duration_ms=elapsed_ms,
                    )
                except RuntimeError:
                    pass
                
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000
                try:
                    agentops.track(
                        event_type="tool_error",
                        tool_name=name,
                        tool_input=tool_input,
                        tool_output={"error": str(e), "error_type": type(e).__name__},
                        duration_ms=elapsed_ms,
                    )
                except RuntimeError:
                    pass
                raise
        
        return wrapper
    
    if func is not None:
        return decorator(func)
    return decorator
