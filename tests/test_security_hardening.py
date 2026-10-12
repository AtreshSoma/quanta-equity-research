"""Provider handling must not bypass TLS or leak credentials.

Audit section 15 (Security findings).

  * `_fetch_rss_feed` retried with `ssl._create_unverified_context()` whenever
    the first attempt failed with a certificate error — exactly the condition a
    network attacker produces. A feed that cannot be verified must be reported
    unavailable, not fetched anyway.
  * `APIManager._safe_request` printed request exceptions. Provider URLs carry
    API keys in the query string, and requests' exception strings embed the
    full URL, so a stack of failures could print live credentials to stdout.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import requests

MODULES = Path(__file__).resolve().parents[1] / "modules"
if str(MODULES) not in sys.path:
    sys.path.insert(0, str(MODULES))

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402

api_manager = importlib.import_module("api_manager")

SECRET_URL = "https://api.example.com/v3/quote/AAPL?apikey=SUPERSECRETKEY123"


# ---------------------------------------------------------------------------
# No certificate-validation bypass
# ---------------------------------------------------------------------------

def test_rss_source_module_has_no_unverified_ssl_context():
    src = Path(webapp.__file__).read_text(encoding="utf-8")
    assert "_create_unverified_context" not in src


def test_certificate_failure_reports_the_feed_unavailable(monkeypatch):
    """A TLS failure must yield no articles, not an unverified retry."""
    import ssl as _ssl

    calls = []

    def _boom(*args, **kwargs):
        calls.append(kwargs.get("context"))
        raise _ssl.SSLError("certificate verify failed: self signed certificate")

    monkeypatch.setattr(webapp, "urlopen", _boom)
    out = webapp._fetch_rss_feed({"name": "Test", "url": "https://example.com/rss"})

    assert out == []
    # Exactly one attempt, and it never supplied an unverified context. The
    # context it does supply adds certifi's CAs (macOS python.org builds ship
    # an empty store) and still requires a valid certificate and hostname.
    assert len(calls) == 1
    ctx = calls[0]
    assert ctx is None or (ctx.verify_mode == _ssl.CERT_REQUIRED and ctx.check_hostname)


# ---------------------------------------------------------------------------
# Credentials never reach the logs
# ---------------------------------------------------------------------------

def test_url_redaction_strips_query_credentials():
    redacted = api_manager._redact_url(SECRET_URL)

    assert "SUPERSECRETKEY123" not in redacted
    assert "api.example.com" in redacted
    assert "/v3/quote/AAPL" in redacted


def test_url_redaction_handles_a_url_with_no_query():
    assert api_manager._redact_url("https://api.example.com/v3/x") == (
        "https://api.example.com/v3/x"
    )


def test_redaction_of_non_url_text_is_safe():
    assert api_manager._redact_url("") == ""
    assert api_manager._redact_url(None) == ""


def test_request_failure_does_not_emit_the_url(capsys, monkeypatch):
    mgr = api_manager.EnhancedAPIManager.__new__(api_manager.EnhancedAPIManager)
    mgr.api_health = {"FMP": True}

    class _Session:
        def get(self, url, timeout=None):
            raise requests.exceptions.ConnectionError(
                f"Failed to establish a new connection to {SECRET_URL}"
            )

    mgr.session = _Session()
    assert mgr._safe_request(SECRET_URL, "FMP") is None

    captured = capsys.readouterr()
    assert "SUPERSECRETKEY123" not in captured.out
    assert "SUPERSECRETKEY123" not in captured.err
    assert mgr.api_health["FMP"] is False


def test_request_failure_still_records_the_provider_and_endpoint(caplog):
    mgr = api_manager.EnhancedAPIManager.__new__(api_manager.EnhancedAPIManager)
    mgr.api_health = {"FMP": True}

    class _Session:
        def get(self, url, timeout=None):
            raise requests.exceptions.Timeout("timed out")

    mgr.session = _Session()
    with caplog.at_level("WARNING"):
        mgr._safe_request(SECRET_URL, "FMP")

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "FMP" in joined
    assert "SUPERSECRETKEY123" not in joined


# ---------------------------------------------------------------------------
# The on-disk provider cache is not executable
# ---------------------------------------------------------------------------

def test_disk_cache_does_not_load_pickle():
    """Comments may mention pickle; nothing may import or call it."""
    src = Path(webapp.__file__).read_text(encoding="utf-8")

    assert "import pickle" not in src
    assert "pickle.load" not in src
    assert "_pickle." not in src


def test_history_frames_survive_a_json_cache_round_trip():
    import pandas as pd

    frame = pd.DataFrame(
        {"Open": [1.0, 2.0], "Close": [1.5, 2.5]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )
    restored = webapp._yf_frame_from_json(webapp._yf_frame_to_json(frame))

    pd.testing.assert_frame_equal(restored, frame, check_freq=False)
