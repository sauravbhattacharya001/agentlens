"""Tests for agentlens.decorators — @track_agent and @track_tool_call."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from agentlens.decorators import track_agent, track_tool_call


class TestTrackAgent:
    def test_sync_success(self):
        @track_agent
        def my_agent(x):
            return x * 2

        with patch("agentlens.track") as mock_track:
            result = my_agent(5)
            assert result == 10
            mock_track.assert_called_once()
            call_kwargs = mock_track.call_args[1]
            assert call_kwargs["event_type"] == "agent_call"
            assert "successfully" in call_kwargs["reasoning"]

    def test_sync_error(self):
        @track_agent
        def failing_agent():
            raise ValueError("boom")

        with patch("agentlens.track") as mock_track:
            with pytest.raises(ValueError, match="boom"):
                failing_agent()
            mock_track.assert_called_once()
            call_kwargs = mock_track.call_args[1]
            assert call_kwargs["event_type"] == "agent_error"

    def test_sync_with_model(self):
        @track_agent(model="gpt-4")
        def model_agent():
            return "ok"

        with patch("agentlens.track") as mock_track:
            model_agent()
            call_kwargs = mock_track.call_args[1]
            assert call_kwargs["model"] == "gpt-4"

    def test_sync_with_custom_name(self):
        @track_agent(name="custom-name")
        def my_func():
            return "ok"

        with patch("agentlens.track") as mock_track:
            my_func()
            call_kwargs = mock_track.call_args[1]
            assert "custom-name" in call_kwargs["reasoning"]

    def test_preserves_function_name(self):
        @track_agent
        def original_name():
            pass

        assert original_name.__name__ == "original_name"

    def test_async_success(self):
        @track_agent
        async def async_agent(x):
            return x + 1

        with patch("agentlens.track") as mock_track:
            result = asyncio.get_event_loop().run_until_complete(async_agent(10))
            assert result == 11
            mock_track.assert_called_once()

    def test_async_error(self):
        @track_agent
        async def async_fail():
            raise RuntimeError("async boom")

        with patch("agentlens.track") as mock_track:
            with pytest.raises(RuntimeError, match="async boom"):
                asyncio.get_event_loop().run_until_complete(async_fail())
            call_kwargs = mock_track.call_args[1]
            assert call_kwargs["event_type"] == "agent_error"

    def test_sdk_not_initialized_silently_passes(self):
        @track_agent
        def safe_agent():
            return "works"

        # When agentlens.track raises RuntimeError (not initialized), 
        # the decorator should still return the result
        with patch("agentlens.track", side_effect=RuntimeError("not init")):
            result = safe_agent()
            assert result == "works"


class TestTrackToolCall:
    def test_sync_success(self):
        @track_tool_call
        def search(query):
            return {"results": [query]}

        with patch("agentlens.track") as mock_track:
            result = search("hello")
            assert result == {"results": ["hello"]}
            mock_track.assert_called_once()
            call_kwargs = mock_track.call_args[1]
            assert call_kwargs["event_type"] == "tool_call"
            assert call_kwargs["tool_name"] == "search"

    def test_sync_error(self):
        @track_tool_call
        def broken_tool():
            raise IOError("disk full")

        with patch("agentlens.track") as mock_track:
            with pytest.raises(IOError, match="disk full"):
                broken_tool()
            call_kwargs = mock_track.call_args[1]
            assert call_kwargs["event_type"] == "tool_error"

    def test_custom_tool_name(self):
        @track_tool_call(tool_name="web_search")
        def search(q):
            return q

        with patch("agentlens.track") as mock_track:
            search("test")
            call_kwargs = mock_track.call_args[1]
            assert call_kwargs["tool_name"] == "web_search"

    def test_preserves_function_name(self):
        @track_tool_call
        def my_tool():
            pass

        assert my_tool.__name__ == "my_tool"

    def test_async_success(self):
        @track_tool_call
        async def async_tool(x):
            return x

        with patch("agentlens.track") as mock_track:
            result = asyncio.get_event_loop().run_until_complete(async_tool(42))
            assert result == 42
            mock_track.assert_called_once()

    def test_sdk_not_initialized_silently_passes(self):
        @track_tool_call
        def safe_tool():
            return "ok"

        with patch("agentlens.track", side_effect=RuntimeError("not init")):
            result = safe_tool()
            assert result == "ok"
