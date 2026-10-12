"""Production audit (October 2026) A-42 / A-44: text that reaches the page.

* A market-wide headline is not described as "not confirmed by the company".
* Broker record messages name the record in plain words, never the tool.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _paths import ensure_paths

ensure_paths()
from portfolio import events as ev  # noqa: E402

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def test_macro_headlines_point_to_the_official_source_not_the_company():
    articles = [{"title": "Treasury yields edge higher before payrolls", "category": "macro",
                 "published": (NOW - timedelta(hours=2)).isoformat(), "source": "Wire", "url": "https://example.com/a"}]
    items = ev._news_items(articles, [], {}, NOW)
    assert len(items) == 1
    assert "company" not in items[0]["watch"]
    assert "official source" in items[0]["watch"]


def test_broker_history_messages_carry_no_tool_names():
    from brokers import robinhood as rh

    class _Spec:
        usable = False
        unfillable = ("account_number",)

    provider = rh.RobinhoodProvider.__new__(rh.RobinhoodProvider)
    provider._tools = {rh.READ_TOOLS["orders"]: _Spec()}           # trade history not offered at all
    provider._account_number = lambda handle: "acct"
    out = provider.load_history("handle")
    messages = " ".join(i["message"] for i in out["issues"])
    assert out["orders"] is None and out["trades"] is None
    for tool in rh.READ_TOOLS.values():
        assert tool not in messages, tool
    assert "order history" in messages and "closed-trade history" in messages
