"""I1: the SEC contact is resolved the same way everywhere, and every reason the
corporate-event check is unavailable is told apart in the payload.

A server started from a terminal that predates the user-scope variable never
saw it (the 2026-10-04 scan). The resolver reads the process first, then the
user scope, then an optional git-ignored .env file, and reports only whether a
value is configured, missing or malformed, never the value itself.
"""
from __future__ import annotations

import io
import socket
import urllib.error
from datetime import date

import pytest

from _paths import ensure_paths

ensure_paths()

from engine import corporate_events as ce  # noqa: E402
from engine.blockers import blocker_info  # noqa: E402
from portfolio import sources  # noqa: E402
from portfolio.sources import EdgarClient  # noqa: E402

TODAY = date(2026, 10, 1)
SECRET = "owner.contact@example.org"


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.delenv("QUANTA_SEC_CONTACT", raising=False)
    monkeypatch.delenv("QUANTA_SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("QUANTA_SEC_ENV_FALLBACK", "1")
    monkeypatch.setattr(sources, "_user_scope_value", lambda name: None)
    monkeypatch.setattr(sources, "DOTENV_PATH", None)
    return monkeypatch


def _no_secret(obj):
    assert SECRET not in repr(obj) and SECRET not in str(obj)


# ── resolution ──────────────────────────────────────────────────────────────

def test_process_value_is_configured(clean):
    clean.setenv("QUANTA_SEC_CONTACT", SECRET)
    c = sources.resolve_sec_contact()
    assert (c.state, c.source, c.variable) == ("configured", "process", "QUANTA_SEC_CONTACT")
    _no_secret(c)
    _no_secret(c.public())


def test_user_scope_is_the_fallback_when_the_process_lacks_it(clean):
    clean.setattr(sources, "_user_scope_value", lambda name: SECRET if name == "QUANTA_SEC_CONTACT" else None)
    c = sources.resolve_sec_contact()
    assert (c.state, c.source) == ("configured", "user")
    assert SECRET in sources.sec_user_agent()


def test_dotenv_is_the_last_fallback(clean, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# local only\nOTHER=1\nQUANTA_SEC_CONTACT = \"%s\"\n" % SECRET, encoding="utf-8")
    clean.setattr(sources, "DOTENV_PATH", env)
    c = sources.resolve_sec_contact()
    assert (c.state, c.source) == ("configured", "dotenv")


def test_missing_everywhere(clean):
    c = sources.resolve_sec_contact()
    assert (c.state, c.source) == ("missing", None)
    assert sources.sec_user_agent() is None


@pytest.mark.parametrize("bad", ["not an address", "me@", "@example.com", "<script>@x"])
def test_malformed_contact_is_reported_and_never_sent(clean, bad):
    clean.setenv("QUANTA_SEC_CONTACT", bad)
    c = sources.resolve_sec_contact()
    assert c.state == "malformed" and c.source == "process"
    assert sources.sec_user_agent() is None
    with pytest.raises(sources.SecContactMalformed):
        sources._agent(require_contact=True)


def test_a_url_is_an_accepted_contact(clean):
    clean.setenv("QUANTA_SEC_CONTACT", "https://example.org/contact")
    assert sources.resolve_sec_contact().state == "configured"


def test_fallback_switch_off_reads_the_process_only(clean):
    clean.setenv("QUANTA_SEC_ENV_FALLBACK", "0")
    clean.setattr(sources, "_user_scope_value", lambda name: SECRET)
    assert sources.resolve_sec_contact().state == "missing"


def test_startup_puts_a_fallback_value_into_the_process(clean):
    clean.setattr(sources, "_user_scope_value", lambda name: SECRET if name == "QUANTA_SEC_CONTACT" else None)
    status = sources.apply_sec_contact_to_env()
    import os
    assert os.environ.get("QUANTA_SEC_CONTACT") == SECRET
    assert status == {"state": "configured", "source": "user", "variable": "QUANTA_SEC_CONTACT"}


# ── every unavailable reason is distinguishable ─────────────────────────────

def _http(code, msg):
    return urllib.error.HTTPError("https://data.sec.gov/x", code, msg, {}, io.BytesIO(b""))


@pytest.mark.parametrize("exc,reason,status", [
    (_http(429, "Too Many Requests"), "rate_limited", 429),
    (_http(403, "Forbidden"), "http_error", 403),
    (_http(503, "Service Unavailable"), "http_error", 503),
    (urllib.error.URLError(socket.timeout("timed out")), "timeout", None),
    (TimeoutError("read timed out"), "timeout", None),
    (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "network_error", None),
])
def test_provider_failures_carry_a_structured_reason(clean, exc, reason, status):
    clean.setenv("QUANTA_SEC_CONTACT", SECRET)

    def boom(url):
        raise exc
    ev = ce.check("EXDX", EdgarClient(get_json=boom), TODAY)
    assert ev.status == "unavailable" and ev.code == "EVENT_CHECK_UNAVAILABLE"
    assert ev.reason == reason
    assert ev.to_dict()["http_status"] == status
    assert ev.fix
    if reason != "http_error":
        assert "QUANTA_SEC_CONTACT" not in ev.fix
    _no_secret(ev.to_dict())


def test_missing_and_malformed_contact_reasons(clean):
    from functools import partial
    edgar = EdgarClient(get_json=partial(sources.http_get_json, require_contact=True))
    ev = ce.check("EXDX", edgar, TODAY)
    assert ev.reason == "contact_missing"
    assert "QUANTA_SEC_CONTACT" in ev.text and "QUANTA_SEC_CONTACT" in ev.fix
    clean.setenv("QUANTA_SEC_CONTACT", "not an address")
    edgar = EdgarClient(get_json=partial(sources.http_get_json, require_contact=True))
    ev = ce.check("EXDX", edgar, TODAY)
    assert ev.reason == "contact_malformed"
    assert "not an address" not in ev.text and "not an address" not in ev.fix


def test_rate_limit_fix_does_not_tell_the_reader_to_set_the_contact(clean):
    clean.setenv("QUANTA_SEC_CONTACT", SECRET)

    def boom(url):
        raise _http(429, "Too Many Requests")
    ev = ce.check("EXDX", EdgarClient(get_json=boom), TODAY)
    assert "QUANTA_SEC_CONTACT" not in ev.fix


def test_other_unavailable_paths_carry_reasons(clean):
    ev = ce.check("EXDX", None, TODAY)
    assert ev.reason == "no_client"
    edgar = EdgarClient(get_json=lambda url: {})
    assert ce.check("NOPE", edgar, TODAY).reason == "no_registrant"
    assert ce.check("RELIANCE.NS", edgar, TODAY).reason == "not_covered"


def test_the_blocker_remedy_is_the_reason_specific_fix(clean):
    clean.setenv("QUANTA_SEC_CONTACT", SECRET)

    def boom(url):
        raise _http(429, "Too Many Requests")
    ev = ce.check("EXDX", EdgarClient(get_json=boom), TODAY).to_dict()
    info = blocker_info("EVENT_CHECK_UNAVAILABLE", event=ev)
    assert info["remedy"] == ev["fix"]
    assert blocker_info("EVENT_CHECK_UNAVAILABLE")["remedy"]          # generic text still there


# ── server: startup warning and health field ────────────────────────────────

def test_health_reports_the_contact_state_without_the_value(clean, monkeypatch):
    import app as webapp
    monkeypatch.setenv("QUANTA_SEC_CONTACT", SECRET)
    body = webapp.app.test_client().get("/api/health?probe=0").get_json()
    assert body["sec_contact"] == {"state": "configured", "source": "process", "variable": "QUANTA_SEC_CONTACT"}
    _no_secret(body)
    monkeypatch.delenv("QUANTA_SEC_CONTACT")
    body = webapp.app.test_client().get("/api/health?probe=0").get_json()
    assert body["sec_contact"]["state"] == "missing"
    monkeypatch.setenv("QUANTA_SEC_CONTACT", "nope")
    body = webapp.app.test_client().get("/api/health?probe=0").get_json()
    assert body["sec_contact"]["state"] == "malformed"
    assert "nope" not in str(body)


def test_startup_warns_when_the_contact_is_missing(clean, caplog):
    import app as webapp
    with caplog.at_level("WARNING"):
        status = webapp._sec_contact_startup_check()
    assert status["state"] == "missing"
    assert any("QUANTA_SEC_CONTACT" in r.getMessage() for r in caplog.records)


def test_startup_logs_no_value_when_configured(clean, caplog):
    import app as webapp
    clean.setenv("QUANTA_SEC_CONTACT", SECRET)
    with caplog.at_level("INFO"):
        webapp._sec_contact_startup_check()
    assert all(SECRET not in r.getMessage() for r in caplog.records)


def test_start_script_resolves_the_contact_from_the_user_scope_and_dotenv():
    """start.ps1 must not depend on the launching terminal's environment."""
    from pathlib import Path
    ps1 = (Path(__file__).resolve().parents[1] / "start.ps1").read_text(encoding="utf-8")
    assert "GetEnvironmentVariable('QUANTA_SEC_CONTACT', 'User')" in ps1
    assert ".env" in ps1
    # It reports the state only: the value is never written to the console.
    assert "Write-Host $secContact" not in ps1 and "Write-Host \"$secContact" not in ps1


# ── payload: counts and reasons reach the scan result ───────────────────────

def test_the_gate_summarises_unavailable_reasons_with_the_book_names(monkeypatch):
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    sysm._event_checker = lambda symbols, **_: {s: ce._unavailable("", "contact_missing") for s in symbols}
    sysm.scan_results = [_pick("AAA", "Tech", 8.5, seed=1), _pick("BBB", "Health", 8.0, seed=2)]
    sysm.get_top_recommendations(max_positions=5)
    summary = sysm._event_summary
    assert summary["checked"] == 2 and summary["unavailable"] == 2 and summary["clear"] == 0
    (row,) = summary["reasons"]
    assert row["reason"] == "contact_missing" and row["count"] == 2
    assert sorted(row["book_tickers"]) == ["AAA", "BBB"]
    assert "QUANTA_SEC_CONTACT" in row["fix"] and row["label"]


def test_the_worker_publishes_the_event_check_and_contact_state(monkeypatch):
    from test_scan_accounting import _run_worker, _watch
    monkeypatch.setenv("QUANTA_SEC_CONTACT", SECRET)
    res = _run_worker(monkeypatch, [_watch("W1", 1)])
    assert res["event_check"]["contact"] == {"state": "configured", "source": "process",
                                             "variable": "QUANTA_SEC_CONTACT"}
    assert SECRET not in str(res)
    monkeypatch.delenv("QUANTA_SEC_CONTACT")
    contact = _run_worker(monkeypatch, [_watch("W1", 1)])["event_check"]["contact"]
    assert contact["state"] == "missing" and "QUANTA_SEC_CONTACT" in contact["fix"]
