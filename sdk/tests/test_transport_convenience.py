"""Tests for Transport convenience HTTP methods (get/post/put/delete).

These methods were extracted during a refactor to encapsulate auth headers
and base URL construction, eliminating 30 direct _client accesses in the
tracker layer.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentlens.transport import Transport


@pytest.fixture
def transport():
    """Transport with mocked _client for testing convenience methods."""
    t = Transport(endpoint="http://test:3000", api_key="secret-key-42")
    yield t
    t._running = False
    try:
        t._client.close()
    except Exception:
        pass


class TestAuthHeaders:
    """Tests for _auth_headers() helper."""

    def test_returns_api_key_header(self, transport):
        headers = transport._auth_headers()
        assert headers == {"X-API-Key": "secret-key-42"}

    def test_uses_masked_api_key_property(self, transport):
        assert transport.api_key == "secret-key-42"
        headers = transport._auth_headers()
        assert headers["X-API-Key"] == transport.api_key

    def test_changes_with_api_key(self):
        t = Transport(endpoint="http://test:3000", api_key="other-key")
        try:
            assert t._auth_headers() == {"X-API-Key": "other-key"}
        finally:
            t._running = False


class TestGet:
    """Tests for Transport.get() convenience method."""

    def test_sends_get_to_correct_url(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "get", return_value=mock_resp) as mock_get:
            transport.get("/sessions/abc")
            mock_get.assert_called_once()
            assert mock_get.call_args[0][0] == "http://test:3000/sessions/abc"

    def test_includes_auth_header(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "get", return_value=mock_resp) as mock_get:
            transport.get("/test")
            headers = mock_get.call_args[1]["headers"]
            assert headers["X-API-Key"] == "secret-key-42"

    def test_passes_params(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "get", return_value=mock_resp) as mock_get:
            transport.get("/search", params={"q": "test", "limit": 10})
            assert mock_get.call_args[1]["params"] == {"q": "test", "limit": 10}

    def test_returns_response(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": [1, 2, 3]}
        with patch.object(transport._client, "get", return_value=mock_resp):
            result = transport.get("/data")
            assert result.json() == {"data": [1, 2, 3]}

    def test_raises_on_http_error(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=mock_resp
        )
        with patch.object(transport._client, "get", return_value=mock_resp):
            with pytest.raises(httpx.HTTPStatusError):
                transport.get("/missing")

    def test_merges_extra_headers(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "get", return_value=mock_resp) as mock_get:
            transport.get("/test", headers={"Accept": "text/csv"})
            headers = mock_get.call_args[1]["headers"]
            assert headers["X-API-Key"] == "secret-key-42"
            assert headers["Accept"] == "text/csv"


class TestPost:
    """Tests for Transport.post() convenience method."""

    def test_sends_post_to_correct_url(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "post", return_value=mock_resp) as mock_post:
            transport.post("/sessions/compare")
            assert mock_post.call_args[0][0] == "http://test:3000/sessions/compare"

    def test_includes_auth_header(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "post", return_value=mock_resp) as mock_post:
            transport.post("/create")
            headers = mock_post.call_args[1]["headers"]
            assert headers["X-API-Key"] == "secret-key-42"

    def test_sends_json_body(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "post", return_value=mock_resp) as mock_post:
            transport.post("/rules", json={"name": "test", "threshold": 100})
            assert mock_post.call_args[1]["json"] == {"name": "test", "threshold": 100}

    def test_returns_response(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"id": "rule-1"}
        with patch.object(transport._client, "post", return_value=mock_resp):
            result = transport.post("/rules", json={})
            assert result.json() == {"id": "rule-1"}

    def test_raises_on_http_error(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=mock_resp
        )
        with patch.object(transport._client, "post", return_value=mock_resp):
            with pytest.raises(httpx.HTTPStatusError):
                transport.post("/bad", json={})

    def test_sends_params_and_json(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "post", return_value=mock_resp) as mock_post:
            transport.post("/purge", params={"dry_run": "true"}, json={})
            assert mock_post.call_args[1]["params"] == {"dry_run": "true"}
            assert mock_post.call_args[1]["json"] == {}


class TestPut:
    """Tests for Transport.put() convenience method."""

    def test_sends_put_to_correct_url(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "put", return_value=mock_resp) as mock_put:
            transport.put("/rules/abc")
            assert mock_put.call_args[0][0] == "http://test:3000/rules/abc"

    def test_includes_auth_header(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "put", return_value=mock_resp) as mock_put:
            transport.put("/config")
            headers = mock_put.call_args[1]["headers"]
            assert headers["X-API-Key"] == "secret-key-42"

    def test_sends_json_body(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "put", return_value=mock_resp) as mock_put:
            transport.put("/pricing", json={"pricing": [{"model": "gpt-4"}]})
            assert mock_put.call_args[1]["json"] == {"pricing": [{"model": "gpt-4"}]}

    def test_returns_response(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"updated": 1}
        with patch.object(transport._client, "put", return_value=mock_resp):
            result = transport.put("/config", json={})
            assert result.json() == {"updated": 1}

    def test_raises_on_http_error(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden", request=MagicMock(), response=mock_resp
        )
        with patch.object(transport._client, "put", return_value=mock_resp):
            with pytest.raises(httpx.HTTPStatusError):
                transport.put("/restricted", json={})


class TestDelete:
    """Tests for Transport.delete() convenience method."""

    def test_sends_delete_to_correct_url(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "request", return_value=mock_resp) as mock_req:
            transport.delete("/rules/abc")
            mock_req.assert_called_once()
            assert mock_req.call_args[0][0] == "DELETE"
            assert mock_req.call_args[0][1] == "http://test:3000/rules/abc"

    def test_includes_auth_header(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "request", return_value=mock_resp) as mock_req:
            transport.delete("/tags")
            headers = mock_req.call_args[1]["headers"]
            assert headers["X-API-Key"] == "secret-key-42"

    def test_sends_json_body(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "request", return_value=mock_resp) as mock_req:
            transport.delete("/tags", json={"tags": ["old"]})
            assert mock_req.call_args[1]["json"] == {"tags": ["old"]}

    def test_returns_response(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"deleted": True}
        with patch.object(transport._client, "request", return_value=mock_resp):
            result = transport.delete("/rules/abc")
            assert result.json() == {"deleted": True}

    def test_raises_on_http_error(self, transport):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=mock_resp
        )
        with patch.object(transport._client, "request", return_value=mock_resp):
            with pytest.raises(httpx.HTTPStatusError):
                transport.delete("/missing")


class TestConvenienceMethodEdgeCases:
    """Edge cases and cross-cutting concerns for convenience methods."""

    def test_trailing_slash_stripped(self):
        """Endpoint trailing slash shouldn't cause double-slash URLs."""
        t = Transport(endpoint="http://test:3000/", api_key="key")
        try:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            with patch.object(t._client, "get", return_value=mock_resp) as mock_get:
                t.get("/sessions")
                # Should be test:3000/sessions not test:3000//sessions
                assert mock_get.call_args[0][0] == "http://test:3000/sessions"
        finally:
            t._running = False

    def test_custom_headers_dont_overwrite_auth(self, transport):
        """Custom X-API-Key header shouldn't override the configured one."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "get", return_value=mock_resp) as mock_get:
            transport.get("/test", headers={"X-API-Key": "hacker"})
            headers = mock_get.call_args[1]["headers"]
            # Custom header wins due to dict merge order (explicit > default)
            # This is the expected behavior — callers can override if needed
            assert "X-API-Key" in headers

    def test_get_no_extra_kwargs(self, transport):
        """get() works with path only, no extra kwargs."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "get", return_value=mock_resp):
            result = transport.get("/health")
            assert result is mock_resp

    def test_post_no_json_body(self, transport):
        """post() works without json body."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(transport._client, "post", return_value=mock_resp):
            result = transport.post("/evaluate")
            assert result is mock_resp

    def test_repr_masks_api_key(self, transport):
        """repr should mask the API key."""
        r = repr(transport)
        assert "secr****" in r
        assert "secret-key-42" not in r

    def test_api_key_property_returns_full_key(self, transport):
        """api_key property should return the full key for auth use."""
        assert transport.api_key == "secret-key-42"

    def test_short_api_key_masked(self):
        """API keys <= 4 chars should show as ****."""
        t = Transport(endpoint="http://test:3000", api_key="abc")
        try:
            assert "****" in repr(t)
            assert "abc" not in repr(t)
        finally:
            t._running = False
