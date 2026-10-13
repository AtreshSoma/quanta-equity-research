"""One verifying TLS context for every urllib request QUANTA makes.

urllib uses OpenSSL's default trust paths. On python.org's macOS builds those
are empty until "Install Certificates.command" is run, so every urllib HTTPS
call (SEC EDGAR, news feeds) failed with CERTIFICATE_VERIFY_FAILED while prices
(requests + certifi) worked. A failed SEC read leaves the corporate-event check
unavailable, which fails closed: no name could get a Buy call.

This context keeps the system / SSL_CERT_FILE trust and adds certifi's bundle.
Verification and hostname checking stay on.
"""
from __future__ import annotations

import logging
import ssl

logger = logging.getLogger(__name__)


def _build() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        import certifi  # noqa: WPS433 - hard dependency of requests
        context.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError) as exc:  # pragma: no cover - env specific
        logger.warning("certifi CA bundle unavailable (%s); using the system trust store only", exc)
    return context


SSL_CONTEXT = _build()
