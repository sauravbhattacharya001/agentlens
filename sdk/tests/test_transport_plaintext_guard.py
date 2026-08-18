"""Tests for the plaintext-HTTP credential-exposure guard in agentlens.transport.

These pin the security-relevant seam that warns (and logs) when an API key
would be sent in cleartext over HTTP to a non-localhost host. The helper
``_is_plaintext_remote`` and the constructor's warning arm were previously
exercised only indirectly; here we assert each branch directly.
"""

import warnings
from unittest.mock import patch

from agentlens.transport import Transport, _is_plaintext_remote


class TestIsPlaintextRemote:
    def test_https_is_never_plaintext(self):
        assert _is_plaintext_remote("https://example.com") is False
        assert _is_plaintext_remote("https://example.com/events") is False

    def test_http_to_remote_host_is_plaintext(self):
        assert _is_plaintext_remote("http://example.com:3000") is True

    def test_localhost_hosts_are_exempt(self):
        assert _is_plaintext_remote("http://localhost:3000") is False
        assert _is_plaintext_remote("http://127.0.0.1:3000") is False
        assert _is_plaintext_remote("http://[::1]:3000") is False

    def test_host_match_is_case_insensitive(self):
        # urlparse lowercases the netloc host, but guard also lowercases —
        # an uppercase localhost must still be treated as local.
        assert _is_plaintext_remote("http://LOCALHOST:3000") is False
        # An uppercase remote host is still remote.
        assert _is_plaintext_remote("http://Example.com") is True

    def test_non_https_non_http_remote_is_plaintext(self):
        # Only https is treated as safe; any other non-https scheme to a
        # remote host is flagged (defensive — do not assume ftp/ws is safe).
        assert _is_plaintext_remote("ftp://example.com") is True

    def test_bare_ipv6_localhost_without_brackets_is_flagged(self):
        # urlparse cannot extract the host from an unbracketed IPv6 authority,
        # so it is conservatively treated as a non-localhost remote. This pins
        # current behaviour (callers should always bracket IPv6 literals).
        assert _is_plaintext_remote("http://::1") is True


class TestConstructorPlaintextWarning:
    def test_warns_on_remote_plaintext_with_real_key(self):
        with patch("agentlens.transport.threading.Thread"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                t = Transport(endpoint="http://remote.example:3000", api_key="secretkey")
                try:
                    assert len(caught) == 1
                    assert issubclass(caught[0].category, UserWarning)
                    assert "plaintext HTTP" in str(caught[0].message)
                    assert "remote.example" in str(caught[0].message)
                finally:
                    t.close()

    def test_no_warning_when_api_key_is_default(self):
        # The default sentinel key is not a real credential — no exposure risk.
        with patch("agentlens.transport.threading.Thread"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                t = Transport(endpoint="http://remote.example:3000", api_key="default")
                try:
                    assert len(caught) == 0
                finally:
                    t.close()

    def test_no_warning_over_https(self):
        with patch("agentlens.transport.threading.Thread"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                t = Transport(endpoint="https://remote.example", api_key="secretkey")
                try:
                    assert len(caught) == 0
                finally:
                    t.close()

    def test_no_warning_for_localhost_with_real_key(self):
        with patch("agentlens.transport.threading.Thread"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                t = Transport(endpoint="http://localhost:3000", api_key="secretkey")
                try:
                    assert len(caught) == 0
                finally:
                    t.close()

    def test_warning_also_logged(self):
        # Patch Thread so the background flush loop never runs — otherwise a
        # failed network flush would emit its own logger.warning calls and
        # race this assertion.
        with patch("agentlens.transport.threading.Thread"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with patch("agentlens.transport.logger.warning") as log_warn:
                    t = Transport(
                        endpoint="http://remote.example:3000", api_key="secretkey"
                    )
                    try:
                        # The plaintext-exposure warning is logged as the
                        # first logger.warning call from the constructor.
                        logged_calls = [
                            " ".join(str(a) for a in call.args)
                            for call in log_warn.call_args_list
                        ]
                        assert any(
                            "plaintext HTTP" in msg for msg in logged_calls
                        ), logged_calls
                    finally:
                        t.close()
