"""The daily My list briefing (webapp/briefing.py)."""
from __future__ import annotations

from datetime import date

from _paths import ensure_paths

ensure_paths(webapp=True)
import briefing as bf  # noqa: E402
from my_list_store import MyListStore  # noqa: E402


def _store(tmp_path):
    s = MyListStore(tmp_path / "list.sqlite3")
    s.add_symbol("AAA")
    s.add_symbol("BBB")
    s.add_alert("AAA", "below", 90.0, label="Stop")
    return s


def test_each_name_gets_its_move_call_alerts_and_report(tmp_path):
    store = _store(tmp_path)
    quote = {"AAA": {"price": 100.0, "previous_close": 98.0}, "BBB": None}.get
    calls = {"AAA": {"label": "Accumulate", "engine": "Research", "as_of": "2026-10-20", "price": 95.0}}.get
    reports = {"AAA": date(2026, 10, 30), "BBB": date(2026, 12, 30)}.get
    b = bf.build_briefing(store, quote, calls, reports, today=date(2026, 10, 23))
    aaa, bbb = b["rows"]
    assert round(aaa["change_pct"], 3) == round((100 / 98 - 1) * 100, 3)
    assert round(aaa["since_call_pct"], 3) == round((100 / 95 - 1) * 100, 3)
    assert abs(aaa["alerts"][0]["away_pct"] + 10.0) < 1e-9
    assert aaa["earnings"] == "2026-10-30"
    assert bbb["price"] is None and bbb["earnings"] is None, "a report two months out is not news"
    text = bf.format_text(b)
    assert "**AAA** 100.00 (+2.04% today)" in text
    assert "Last call: Accumulate (Research, 2026-10-20), +5.26% since" in text
    assert "Alert at or below 90.00 (Stop), 10.0% away" in text
    assert "Earnings report due 2026-10-30" in text
    assert "**BBB** (no price today)" in text
    assert "not investment advice" in text


def test_fired_alerts_lead_and_a_failing_source_does_not_stop_it(tmp_path):
    store = _store(tmp_path)
    store.claim(store.alerts(active_only=True)[0]["id"], 89.5)

    def boom(sym):
        raise RuntimeError("provider down")

    b = bf.build_briefing(store, boom, boom, boom, today=date(2026, 10, 23))
    text = bf.format_text(b)
    assert text.index("## Fired alerts") < text.index("## My list")
    assert "AAA fell to 89.50 (Stop at 90.00)" in text


def test_an_empty_list_says_how_to_fill_it(tmp_path):
    b = bf.build_briefing(MyListStore(tmp_path / "e.sqlite3"), lambda s: None, lambda s: None, lambda s: None)
    assert "My list is empty" in bf.format_text(b)


def test_ledger_codes_read_as_the_page_names_them(monkeypatch, tmp_path):
    import tracking.ledger as ledger

    class Fake:
        def rows(self, ticker=None):
            return [{"engine": "elite", "label": "STRONG_BUY", "as_of_date": "2026-10-21", "reference_price": 10.0}]

    monkeypatch.setattr(ledger, "SignalLedger", Fake)
    call = bf.ledger_call("AAA")
    assert call["label"] == "Strong Buy" and call["engine"] == "Research"


def test_email_goes_out_through_starttls_when_smtp_is_configured(monkeypatch):
    import my_list

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["server"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(my_list.smtplib, "SMTP", FakeSMTP)
    assert my_list.send_email("subject", "body") is False, "nothing is sent without SMTP settings"
    monkeypatch.setenv("QUANTA_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("QUANTA_ALERT_EMAIL", "me@example.com")
    monkeypatch.setenv("QUANTA_SMTP_USER", "me@example.com")
    monkeypatch.setenv("QUANTA_SMTP_PASSWORD", "app-password")
    assert my_list.send_email("QUANTA briefing", "# body") is True
    assert sent["server"] == ("smtp.example.com", 587) and sent["tls"]
    assert sent["msg"]["To"] == "me@example.com" and "not investment advice" in sent["msg"].get_content()
