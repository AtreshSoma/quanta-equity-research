"""Every urllib request uses the shared verifying context (system trust + certifi).

python.org's macOS Python ships with an empty OpenSSL trust store, so a bare
urlopen to SEC EDGAR failed CERTIFICATE_VERIFY_FAILED; the corporate-event check
then read "network error" and no name could get a Buy call.
"""
from __future__ import annotations

import io
import ssl

from _paths import ensure_paths

ensure_paths()

from engine import tls  # noqa: E402
from portfolio import sources  # noqa: E402


def test_the_shared_context_verifies_and_carries_certifis_bundle():
    import certifi
    ctx = tls.SSL_CONTEXT
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 0
    assert certifi.where()


def test_sec_requests_pass_the_shared_context(monkeypatch):
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        seen.append(context)
        return Resp(b'{"ok": 1}')

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sources, "_pace", lambda: None)
    monkeypatch.setenv("QUANTA_SEC_CONTACT", "test@example.com")
    assert sources.http_get_json("https://data.sec.gov/x.json") == {"ok": 1}
    sources.http_get_text("https://www.sec.gov/doc.htm", 100)
    assert seen == [tls.SSL_CONTEXT, tls.SSL_CONTEXT]
