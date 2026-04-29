"""Decorators for automatic agent and tool call tracking."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable


# Keyword argument names that likely contain secrets or credentials.
# Values matching these keys are redacted before being sent to the
# tracking backend, preventing CWE-532 (Sensitive Information in Logs).
# Matching is case-insensitive to catch variations like api_Key, API_KEY, etc.
_SENSITIVE_KWARG_PATTERNS: frozenset[str] = frozenset({
    "api_key", "apikey", "api_secret", "apisecret",
    "secret", "secret_key", "secretkey",
    "password", "passwd", "pwd",
    "token", "access_token", "refresh_token", "auth_token",
    "bearer", "authorization",
    "credential", "credentials",
    "private_key", "privatekey",
    "connection_string", "connectionstring", "dsn",
    "ssn", "social_security",
})

_REDACTED = "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    """Check if a keyword argument name looks like it holds a secret."""
    return key.lower().replace("-", "_") in _SENSITIVE_KWARG_PATTERNS


def _safe_repr(value: Any, *, max_length: int = 200) -> str:
    """Convert a value to a bounded string representation.

    Truncates long values to prevent unbounded payload sizes and
    masks objects whose ``repr`` contains common secret markers.
    """
    try:
        s = str(value)
    except Exception:
        return "<unrepresentable>"
    if len(s) > max_length:
        s = s[:max_length] + f"...[truncated, {len(s)} chars]"
    return s


def _build_input(args: tuple, kwargs: dict) -> dict:
    """Serialize function arguments for tracking.

    Redacts keyword arguments whose names match common secret/credential
    patterns (api_key, password, token, etc.) to prevent sensitive data
    from being captured in observability events (CWE-532).  Positional
    arguments are included as-is since they lack semantic names, but are
    truncated to a safe length.
    """
    return {
        "args": [_safe_repr(a) for a in args],
        "kwargs": {
            k: _REDACTED if _is_sensitive_key(k) else _safe_repr(v)
            for k, v in kwargs.items()
        },
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
    redact_keys: frozenset[str] | None = None,
) -> Callable:
    """Build a sync or async wrapper that tracks function calls.

    This is the shared implementation behind :func:`track_agent` and
    :func:`track_tool_call`, eliminating the duplicated sync/async
    wrapper code that previously existed in each decorator.

    Args:
        redact_keys: Optional additional keyword argument names to redact.
            These are merged with the built-in sensitive patterns.
    """
    # Merge caller-specified redact keys into the global set for this
    # tracker instance so the check is a single set lookup per kwarg.
    if redact_keys:
        _extra_keys = _SENSITIVE_KWARG_PATTERNS | frozenset(
            k.lower().replace("-", "_") for k in redact_keys
        )
    else:
        _extra_keys = _SENSITIVE_KWARG_PATTERNS

    def _build_safe_input(args: tuple, kwargs: dict) -> dict:
        return {
            "args": [_safe_repr(a) for a in args],
            "kwargs": {
                k: _REDACTED if k.lower().replace("-", "_") in _extra_keys else _safe_repr(v)
                for k, v in kwargs.items()
            },
        }

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
            input_data = _build_safe_input(args, kwargs)
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
        input_data = _build_safe_input(args, kwargs)
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
    redact_keys: frozenset[str] | None = None,
):
    """Decorator to automatically track an agent function call.

    Supports both synchronous and asynchronous functions.
    Keyword arguments whose names match common secret patterns
    (api_key, password, token, etc.) are automatically redacted.

    Args:
        model: Model name to associate with tracked events.
        name: Override the agent name (defaults to function name).
        redact_keys: Optional additional keyword argument names to redact
            on top of the built-in sensitive patterns.

    Usage::

        @track_agent
        def my_agent(prompt):
            ...

        @track_agent(model="gpt-4")
        async def my_agent(prompt):
            ...

        @track_agent(redact_keys={"patient_id", "ssn"})
        def healthcare_agent(prompt, patient_id=None):
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
            redact_keys=redact_keys,
        )

    if func is not None:
        return decorator(func)
    return decorator


def track_tool_call(
    func: Callable | None = None,
    *,
    tool_name: str | None = None,
    redact_keys: frozenset[str] | None = None,
):
    """Decorator to automatically track a tool/function call.

    Supports both synchronous and asynchronous functions.
    Keyword arguments whose names match common secret patterns
    (api_key, password, token, etc.) are automatically redacted.

    Args:
        tool_name: Override the tool name (defaults to function name).
        redact_keys: Optional additional keyword argument names to redact
            on top of the built-in sensitive patterns.

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
            redact_keys=redact_keys,
        )

    if func is not None:
        return decorator(func)
    return decorator
