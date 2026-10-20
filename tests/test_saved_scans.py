"""Saved Picks scans: kept for a day, one per setting (webapp/saved_scans.py).

An injected clock drives retention; nothing here sleeps or touches the network.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

import saved_scans as S
from engine.thresholds import SAVED_SCANS

T0 = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)      # Thu 11:00 New York, market open


def params(**kw):
    p = {"market": "US", "capital": 2500.0, "scan_type": "standard", "max_positions": 5,
         "diversify": True, "risk_profile": "balanced", "filters": {}, "custom_tickers": None, "safe_mode": False}
    p.update(kw)
    return p


def result(n_buy=1, n_watch=2, **kw):
    rows = [{"ticker": f"B{i}", "call": "buy"} for i in range(n_buy)] + \
           [{"ticker": f"W{i}", "call": "watchlist"} for i in range(n_watch)]
    r = {"stock_picks": rows, "total_scanned": 250, "funded_count": n_buy, "regime": {"type": "BULL"}}
    r.update(kw)
    return r


def test_each_distinct_setting_is_kept_side_by_side(tmp_path):
    S.save(tmp_path, params(scan_type="standard"), result(), now=T0)
    S.save(tmp_path, params(scan_type="focused_growth"), result(n_buy=0), now=T0 + timedelta(minutes=5))
    S.save(tmp_path, params(scan_type="focused_etf"), result(), now=T0 + timedelta(minutes=9))
    got = S.list_entries(tmp_path, "US", now=T0 + timedelta(minutes=10))
    assert [e["scan_type"] for e in got] == ["focused_etf", "focused_growth", "standard"]   # newest first
    assert got[1]["buy_accumulate"] == 0 and got[1]["watchlist"] == 2 and got[1]["scanned"] == 250


def test_rerunning_the_same_setting_replaces_it(tmp_path):
    S.save(tmp_path, params(), result(n_buy=1), now=T0)
    S.save(tmp_path, params(safe_mode=True), result(n_buy=3), now=T0 + timedelta(hours=1))
    got = S.list_entries(tmp_path, "US", now=T0 + timedelta(hours=1))
    assert len(got) == 1 and got[0]["buy_accumulate"] == 3, "safe mode is the same view, not a new one"


@pytest.mark.parametrize("change", [{"capital": 5000.0}, {"risk_profile": "conservative"}, {"max_positions": 3},
                                     {"diversify": False}, {"filters": {"sectors": ["Energy"]}}])
def test_every_input_that_changes_the_sizes_is_a_different_scan(tmp_path, change):
    assert S.scan_id(params()) != S.scan_id(params(**change))


def test_custom_lists_match_regardless_of_order_and_case():
    assert S.scan_id(params(scan_type="custom", custom_tickers=["AAPL", "msft"])) == \
        S.scan_id(params(scan_type="custom", custom_tickers=["MSFT", "aapl"]))


def test_a_scan_expires_after_the_retention_window(tmp_path):
    S.save(tmp_path, params(scan_type="standard"), result(), now=T0)
    S.save(tmp_path, params(scan_type="focused_growth"), result(), now=T0 + timedelta(hours=20))
    hours = float(SAVED_SCANS.RETENTION_HOURS)
    assert len(S.list_entries(tmp_path, "US", now=T0 + timedelta(hours=hours - 0.1))) == 2
    after = S.list_entries(tmp_path, "US", now=T0 + timedelta(hours=hours + 0.1))
    assert [e["scan_type"] for e in after] == ["focused_growth"]
    sid = S.scan_id(params(scan_type="standard"))
    assert S.load(tmp_path, "US", sid, now=T0 + timedelta(hours=hours + 0.1)) is None
    assert not list((tmp_path / "recent" / "US").glob(f"{sid}*")), "the expired file is removed, not just hidden"


def test_only_the_newest_scans_are_kept(tmp_path):
    cap = int(SAVED_SCANS.MAX_SAVED)
    for i in range(cap + 3):
        S.save(tmp_path, params(capital=1000.0 + i), result(), now=T0 + timedelta(minutes=i))
    got = S.list_entries(tmp_path, "US", now=T0 + timedelta(minutes=cap + 4))
    assert len(got) == cap
    assert got[0]["capital"] == 1000.0 + cap + 2 and got[-1]["capital"] == 1003.0     # the oldest three went


def test_markets_are_kept_apart(tmp_path):
    S.save(tmp_path, params(), result(), now=T0)
    S.save(tmp_path, params(market="IN", scan_type="india_quick", capital=200000.0), result(), now=T0)
    assert [e["scan_type"] for e in S.list_entries(tmp_path, "US", now=T0)] == ["standard"]
    assert [e["scan_type"] for e in S.list_entries(tmp_path, "IN", now=T0)] == ["india_quick"]
    assert S.market_code("India") == "IN" and S.market_code(None) == "US"


def test_load_returns_the_whole_scan_and_newest_opens_first(tmp_path):
    sid = S.save(tmp_path, params(), result(n_buy=2), now=T0)
    S.save(tmp_path, params(scan_type="focused_etf"), result(), now=T0 + timedelta(minutes=1))
    full = S.load(tmp_path, "US", sid, now=T0 + timedelta(minutes=2))
    assert full["id"] == sid and full["params"]["scan_type"] == "standard"
    assert len(full["result"]["stock_picks"]) == 4          # 2 buy + the default 2 watchlist rows
    assert S.newest(tmp_path, "US", now=T0 + timedelta(minutes=2))["params"]["scan_type"] == "focused_etf"
    assert S.newest(tmp_path / "nothing", "US", now=T0) is None


@pytest.mark.parametrize("bad", ["../../etc/passwd", "..", "x" * 12, "ABCDEF012345", "abc", "", None, "0" * 13])
def test_an_id_is_never_turned_into_a_path(tmp_path, bad):
    S.save(tmp_path, params(), result(), now=T0)
    assert S.load(tmp_path, "US", bad, now=T0) is None


def test_a_corrupt_file_is_removed_and_the_rest_still_load(tmp_path):
    S.save(tmp_path, params(scan_type="standard"), result(), now=T0)
    folder = tmp_path / "recent" / "US"
    (folder / "deadbeef0000.json.gz").write_bytes(b"not gzip at all")
    got = S.list_entries(tmp_path, "US", now=T0)
    assert [e["scan_type"] for e in got] == ["standard"]
    assert not (folder / "deadbeef0000.json.gz").exists()


def test_the_single_scan_file_this_replaced_is_adopted_once(tmp_path):
    legacy = {"saved_at": (T0 - timedelta(hours=3)).isoformat(), "params": params(scan_type="focused_value"),
              "result": result()}
    (tmp_path / "US.json.gz").write_bytes(gzip.compress(json.dumps(legacy).encode()))
    got = S.list_entries(tmp_path, "US", now=T0)
    assert [e["scan_type"] for e in got] == ["focused_value"]
    assert got[0]["saved_at"] == legacy["saved_at"], "its own timestamp is kept, so it expires on schedule"
    assert not (tmp_path / "US.json.gz").exists()
    assert len(S.list_entries(tmp_path, "US", now=T0)) == 1


def test_a_single_scan_file_past_retention_is_not_adopted(tmp_path):
    legacy = {"saved_at": (T0 - timedelta(hours=30)).isoformat(), "params": params(), "result": result()}
    (tmp_path / "US.json.gz").write_bytes(gzip.compress(json.dumps(legacy).encode()))
    assert S.list_entries(tmp_path, "US", now=T0) == []
    assert not (tmp_path / "US.json.gz").exists()


# --- the market-close note ---------------------------------------------------

def test_a_scan_from_before_the_close_says_the_market_has_closed_since(tmp_path):
    sid = S.save(tmp_path, params(), result(), now=T0)                 # Thu 11:00 NY: Wed's close is the last final bar
    later = T0 + timedelta(hours=7)                                      # Thu 18:00 NY: Thursday has closed
    e = S.list_entries(tmp_path, "US", now=later)[0]
    assert e["as_of_session"] == "2026-10-14" and e["closed_since"] is True and e["sessions_since"] == 1
    assert S.load(tmp_path, "US", sid, now=later)["closed_since"] is True


def test_a_scan_from_after_the_close_is_current_until_the_next_one(tmp_path):
    S.save(tmp_path, params(), result(), now=T0 + timedelta(hours=7))   # Thu 18:00 NY
    e = S.list_entries(tmp_path, "US", now=T0 + timedelta(hours=9))[0]   # Thu 20:00 NY
    assert e["as_of_session"] == "2026-10-15" and e["closed_since"] is False and e["sessions_since"] == 0


def test_a_weekend_does_not_count_as_a_close(tmp_path):
    fri_evening = datetime(2026, 10, 16, 22, 0, tzinfo=timezone.utc)    # Fri 18:00 NY
    S.save(tmp_path, params(), result(), now=fri_evening)
    saturday = fri_evening + timedelta(hours=16)        # inside retention; a Friday scan is gone by Sunday
    e = S.list_entries(tmp_path, "US", now=saturday)[0]
    assert e["as_of_session"] == "2026-10-16" and e["closed_since"] is False


def test_india_makes_no_market_close_claim(tmp_path):
    S.save(tmp_path, params(market="IN", scan_type="india_quick"), result(), now=T0)
    e = S.list_entries(tmp_path, "IN", now=T0 + timedelta(hours=7))[0]
    assert "closed_since" not in e and "as_of_session" not in e


def test_two_saves_in_one_clock_tick_still_order_newest_first(tmp_path):
    """Windows ticks about every 15 ms: back-to-back saves get the same ``now``."""
    S.save(tmp_path, params(scan_type="standard"), result(), now=T0)
    S.save(tmp_path, params(scan_type="focused_etf"), result(), now=T0)      # the same instant
    got = S.list_entries(tmp_path, "US", now=T0 + timedelta(seconds=1))
    assert [e["scan_type"] for e in got] == ["focused_etf", "standard"]
    assert got[0]["saved_at"] > got[1]["saved_at"]
    assert S.newest(tmp_path, "US", now=T0 + timedelta(seconds=1))["params"]["scan_type"] == "focused_etf"
