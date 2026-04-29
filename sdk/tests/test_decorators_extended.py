"""Additional tests for agentlens.decorators — edge cases and deeper coverage."""

import asyncio
from unittest.mock import MagicMock, patch, call

import pytest

from agentlens.decorators import track_agent, track_tool_call


# ── track_agent: args/kwargs serialization ─────────────────────────

class TestTrackAgentInputCapture:
    def test_captures_positional_args(self):
        @track_agent
        def add(a, b):
            return a + b

        with patch("agentlens.track") as mock_track:
            add(3, 7)
            kw = mock_track.call_args[1]
            assert kw["input_data"]["args"] == ["3", "7"]

    def test_captures_kwargs(self):
        @track_agent
        def greet(name="world"):
            return f"hello {name}"

        with patch("agentlens.track") as mock_track:
            greet(name="alice")
            kw = mock_track.call_args[1]
            assert kw["input_data"]["kwargs"] == {"name": "alice"}

    def test_captures_mixed_args_and_kwargs(self):
        @track_agent
        def fn(a, b, c=0):
            return a + b + c

        with patch("agentlens.track") as mock_track:
            fn(1, 2, c=3)
            kw = mock_track.call_args[1]
            assert kw["input_data"]["args"] == ["1", "2"]
            assert kw["input_data"]["kwargs"] == {"c": "3"}

    def test_no_args(self):
        @track_agent
        def noop():
            return None

        with patch("agentlens.track") as mock_track:
            noop()
            kw = mock_track.call_args[1]
            assert kw["input_data"]["args"] == []
            assert kw["input_data"]["kwargs"] == {}


# ── track_agent: output capture ────────────────────────────────────

class TestTrackAgentOutputCapture:
    def test_none_return_tracked(self):
        @track_agent
        def returns_none():
            return None

        with patch("agentlens.track") as mock_track:
            result = returns_none()
            assert result is None
            kw = mock_track.call_args[1]
            assert kw["output_data"]["result"] is None

    def test_complex_return_stringified(self):
        @track_agent
        def returns_dict():
            return {"key": [1, 2, 3]}

        with patch("agentlens.track") as mock_track:
            result = returns_dict()
            assert result == {"key": [1, 2, 3]}
            kw = mock_track.call_args[1]
            assert "key" in kw["output_data"]["result"]

    def test_error_captures_type_and_message(self):
        @track_agent
        def raises_type_error():
            raise TypeError("bad type")

        with patch("agentlens.track") as mock_track:
            with pytest.raises(TypeError, match="bad type"):
                raises_type_error()
            kw = mock_track.call_args[1]
            assert kw["output_data"]["error"] == "bad type"
            assert kw["output_data"]["error_type"] == "TypeError"


# ── track_agent: duration tracking ─────────────────────────────────

class TestTrackAgentDuration:
    def test_duration_is_positive(self):
        @track_agent
        def slow():
            import time
            time.sleep(0.01)
            return "done"

        with patch("agentlens.track") as mock_track:
            slow()
            kw = mock_track.call_args[1]
            assert kw["duration_ms"] > 0

    def test_error_duration_is_positive(self):
        @track_agent
        def slow_fail():
            import time
            time.sleep(0.01)
            raise RuntimeError("fail")

        with patch("agentlens.track") as mock_track:
            with pytest.raises(RuntimeError):
                slow_fail()
            kw = mock_track.call_args[1]
            assert kw["duration_ms"] > 0


# ── track_agent: async with parameters ────────────────────────────

class TestTrackAgentAsyncParams:
    def test_async_with_model(self):
        @track_agent(model="claude-3")
        async def agent(x):
            return x

        with patch("agentlens.track") as mock_track:
            result = asyncio.run(agent(42))
            assert result == 42
            kw = mock_track.call_args[1]
            assert kw["model"] == "claude-3"

    def test_async_with_custom_name(self):
        @track_agent(name="my-async-agent")
        async def agent():
            return "hi"

        with patch("agentlens.track") as mock_track:
            asyncio.run(agent())
            kw = mock_track.call_args[1]
            assert "my-async-agent" in kw["reasoning"]

    def test_async_error_captures_type(self):
        @track_agent
        async def async_type_error():
            raise KeyError("missing")

        with patch("agentlens.track") as mock_track:
            with pytest.raises(KeyError):
                asyncio.run(async_type_error())
            kw = mock_track.call_args[1]
            assert kw["output_data"]["error_type"] == "KeyError"

    def test_async_preserves_name(self):
        @track_agent(model="gpt-4")
        async def my_async_func():
            pass

        assert my_async_func.__name__ == "my_async_func"

    def test_async_sdk_not_initialized(self):
        @track_agent
        async def safe_async():
            return "ok"

        with patch("agentlens.track", side_effect=RuntimeError("not init")):
            result = asyncio.run(safe_async())
            assert result == "ok"


# ── track_agent: edge cases ───────────────────────────────────────

class TestTrackAgentEdgeCases:
    def test_returns_falsy_values(self):
        @track_agent
        def returns_zero():
            return 0

        with patch("agentlens.track") as mock_track:
            result = returns_zero()
            assert result == 0
            kw = mock_track.call_args[1]
            assert kw["output_data"]["result"] == "0"

    def test_returns_empty_string(self):
        @track_agent
        def returns_empty():
            return ""

        with patch("agentlens.track") as mock_track:
            result = returns_empty()
            assert result == ""
            kw = mock_track.call_args[1]
            assert kw["output_data"]["result"] == ""

    def test_returns_false(self):
        @track_agent
        def returns_false():
            return False

        with patch("agentlens.track") as mock_track:
            result = returns_false()
            assert result is False
            kw = mock_track.call_args[1]
            assert kw["output_data"]["result"] == "False"

    def test_model_none_by_default(self):
        @track_agent
        def agent():
            return "ok"

        with patch("agentlens.track") as mock_track:
            agent()
            kw = mock_track.call_args[1]
            assert kw["model"] is None

    def test_reasoning_contains_duration(self):
        @track_agent
        def agent():
            return "ok"

        with patch("agentlens.track") as mock_track:
            agent()
            kw = mock_track.call_args[1]
            assert "ms" in kw["reasoning"]


# ── track_tool_call: args/kwargs ──────────────────────────────────

class TestTrackToolCallInputCapture:
    def test_captures_positional_args(self):
        @track_tool_call
        def search(query, limit):
            return []

        with patch("agentlens.track") as mock_track:
            search("hello", 10)
            kw = mock_track.call_args[1]
            assert kw["tool_input"]["args"] == ["hello", "10"]

    def test_captures_kwargs(self):
        @track_tool_call
        def search(query, limit=5):
            return []

        with patch("agentlens.track") as mock_track:
            search("hello", limit=20)
            kw = mock_track.call_args[1]
            assert kw["tool_input"]["kwargs"] == {"limit": "20"}


# ── track_tool_call: output capture ──────────────────────────────

class TestTrackToolCallOutputCapture:
    def test_none_return(self):
        @track_tool_call
        def void_tool():
            pass

        with patch("agentlens.track") as mock_track:
            result = void_tool()
            assert result is None
            kw = mock_track.call_args[1]
            assert kw["tool_output"]["result"] is None

    def test_error_captures_type(self):
        @track_tool_call
        def bad_tool():
            raise OSError("permission denied")

        with patch("agentlens.track") as mock_track:
            with pytest.raises(OSError):
                bad_tool()
            kw = mock_track.call_args[1]
            assert kw["tool_output"]["error_type"] == "OSError"
            assert kw["tool_output"]["error"] == "permission denied"


# ── track_tool_call: async ───────────────────────────────────────

class TestTrackToolCallAsync:
    def test_async_with_custom_name(self):
        @track_tool_call(tool_name="web_lookup")
        async def search(q):
            return f"found: {q}"

        with patch("agentlens.track") as mock_track:
            result = asyncio.run(search("test"))
            assert result == "found: test"
            kw = mock_track.call_args[1]
            assert kw["tool_name"] == "web_lookup"

    def test_async_error(self):
        @track_tool_call
        async def fail_tool():
            raise ConnectionError("timeout")

        with patch("agentlens.track") as mock_track:
            with pytest.raises(ConnectionError):
                asyncio.run(fail_tool())
            kw = mock_track.call_args[1]
            assert kw["event_type"] == "tool_error"

    def test_async_preserves_name(self):
        @track_tool_call(tool_name="custom")
        async def original():
            pass

        assert original.__name__ == "original"

    def test_async_sdk_not_initialized(self):
        @track_tool_call
        async def safe_tool():
            return 42

        with patch("agentlens.track", side_effect=RuntimeError("not init")):
            result = asyncio.run(safe_tool())
            assert result == 42


# ── track_tool_call: duration ────────────────────────────────────

class TestTrackToolCallDuration:
    def test_duration_recorded(self):
        @track_tool_call
        def tool():
            import time
            time.sleep(0.01)
            return "done"

        with patch("agentlens.track") as mock_track:
            tool()
            kw = mock_track.call_args[1]
            assert kw["duration_ms"] > 0


# ── Sensitive data redaction (CWE-532 fix) ───────────────────────

class TestSensitiveDataRedaction:
    """Verify that secret/credential kwargs are redacted before tracking."""

    def test_api_key_redacted(self):
        @track_agent
        def agent(prompt, api_key="sk-secret"):
            return "ok"

        with patch("agentlens.track") as mock_track:
            agent("hello", api_key="sk-secret-key-12345")
            kw = mock_track.call_args[1]
            assert kw["input_data"]["kwargs"]["api_key"] == "[REDACTED]"

    def test_password_redacted(self):
        @track_tool_call
        def login(user, password=""):
            return True

        with patch("agentlens.track") as mock_track:
            login("admin", password="hunter2")
            kw = mock_track.call_args[1]
            assert kw["tool_input"]["kwargs"]["password"] == "[REDACTED]"

    def test_token_redacted(self):
        @track_agent
        def call_api(url, token=None):
            return {}

        with patch("agentlens.track") as mock_track:
            call_api("https://api.example.com", token="ghp_xxx123")
            kw = mock_track.call_args[1]
            assert kw["input_data"]["kwargs"]["token"] == "[REDACTED]"

    def test_secret_key_redacted(self):
        @track_agent
        def setup(config, secret_key=None):
            return config

        with patch("agentlens.track") as mock_track:
            setup({"a": 1}, secret_key="super-secret")
            kw = mock_track.call_args[1]
            assert kw["input_data"]["kwargs"]["secret_key"] == "[REDACTED]"

    def test_non_sensitive_kwargs_preserved(self):
        @track_agent
        def agent(prompt, model="gpt-4", temperature=0.7):
            return "ok"

        with patch("agentlens.track") as mock_track:
            agent("hello", model="gpt-4", temperature=0.7)
            kw = mock_track.call_args[1]
            assert kw["input_data"]["kwargs"]["model"] == "gpt-4"
            assert kw["input_data"]["kwargs"]["temperature"] == "0.7"

    def test_multiple_sensitive_keys(self):
        @track_agent
        def multi(api_key="", password="", token="", name=""):
            return True

        with patch("agentlens.track") as mock_track:
            multi(api_key="k", password="p", token="t", name="alice")
            kw = mock_track.call_args[1]
            kwargs = kw["input_data"]["kwargs"]
            assert kwargs["api_key"] == "[REDACTED]"
            assert kwargs["password"] == "[REDACTED]"
            assert kwargs["token"] == "[REDACTED]"
            assert kwargs["name"] == "alice"

    def test_case_insensitive_matching(self):
        @track_agent
        def agent(API_KEY="", Password=""):
            return True

        with patch("agentlens.track") as mock_track:
            agent(API_KEY="key", Password="pass")
            kw = mock_track.call_args[1]
            kwargs = kw["input_data"]["kwargs"]
            assert kwargs["API_KEY"] == "[REDACTED]"
            assert kwargs["Password"] == "[REDACTED]"

    def test_custom_redact_keys(self):
        @track_agent(redact_keys={"patient_id", "ssn"})
        def healthcare(prompt, patient_id=None, ssn=None):
            return "ok"

        with patch("agentlens.track") as mock_track:
            healthcare("check", patient_id="P123", ssn="123-45-6789")
            kw = mock_track.call_args[1]
            kwargs = kw["input_data"]["kwargs"]
            assert kwargs["patient_id"] == "[REDACTED]"
            assert kwargs["ssn"] == "[REDACTED]"

    def test_custom_redact_keys_tool(self):
        @track_tool_call(redact_keys={"db_password"})
        def connect(host, db_password=None):
            return True

        with patch("agentlens.track") as mock_track:
            connect("localhost", db_password="secret")
            kw = mock_track.call_args[1]
            assert kw["tool_input"]["kwargs"]["db_password"] == "[REDACTED]"

    def test_positional_args_not_redacted_but_truncated(self):
        """Positional args can't be redacted by name but are length-bounded."""
        @track_agent
        def agent(prompt):
            return "ok"

        long_value = "x" * 500
        with patch("agentlens.track") as mock_track:
            agent(long_value)
            kw = mock_track.call_args[1]
            arg_str = kw["input_data"]["args"][0]
            assert len(arg_str) < 500  # truncated
            assert "truncated" in arg_str

    def test_async_redaction(self):
        @track_agent
        async def async_agent(prompt, api_key=None):
            return "ok"

        with patch("agentlens.track") as mock_track:
            asyncio.run(async_agent("hello", api_key="secret"))
            kw = mock_track.call_args[1]
            assert kw["input_data"]["kwargs"]["api_key"] == "[REDACTED]"

    def test_connection_string_redacted(self):
        @track_tool_call
        def connect(connection_string=""):
            return True

        with patch("agentlens.track") as mock_track:
            connect(connection_string="postgresql://user:pass@host/db")
            kw = mock_track.call_args[1]
            assert kw["tool_input"]["kwargs"]["connection_string"] == "[REDACTED]"


# ── track_tool_call: edge cases ──────────────────────────────────

class TestTrackToolCallEdgeCases:
    def test_default_tool_name_from_function(self):
        @track_tool_call
        def my_custom_tool():
            return "ok"

        with patch("agentlens.track") as mock_track:
            my_custom_tool()
            kw = mock_track.call_args[1]
            assert kw["tool_name"] == "my_custom_tool"

    def test_returns_falsy_zero(self):
        @track_tool_call
        def zero_tool():
            return 0

        with patch("agentlens.track") as mock_track:
            result = zero_tool()
            assert result == 0
            kw = mock_track.call_args[1]
            assert kw["tool_output"]["result"] == "0"

    def test_returns_empty_list(self):
        @track_tool_call
        def empty_tool():
            return []

        with patch("agentlens.track") as mock_track:
            result = empty_tool()
            assert result == []
            kw = mock_track.call_args[1]
            assert kw["tool_output"]["result"] == "[]"
