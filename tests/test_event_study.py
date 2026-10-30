"""Event study: hand-checkable abnormal returns, window lengths, n<5 suppression, the attribution rule,
and the t-based interval. Synthetic prices only; no network."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from engine import event_study as es
from engine import sec_events as se

START = date(2026, 10, 1)


def _dates(n):
    return [START + timedelta(days=i) for i in range(n)]


def _flat(dates, value):
    return {d: float(value) for d in dates}


# ---------------------------------------------------------------------------
# Abnormal returns, window by window
# ---------------------------------------------------------------------------

def test_abnormal_returns_match_the_hand_computed_values():
    dates = _dates(30)
    stock = _flat(dates, 100.0)
    bench = _flat(dates, 200.0)
    # Reaction session is index 10; the base close is index 9 (stock 100, SPY 200).
    stock[dates[10]] = 110.0   # day 0
    stock[dates[11]] = 111.0   # day +1
    stock[dates[14]] = 105.0   # day +4, the end of the 5-session window
    stock[dates[29]] = 90.0    # day +19, the end of the 20-session window
    bench[dates[10]] = 202.0   # SPY +1%
    bench[dates[14]] = 204.0   # SPY +2% by the 5-session end
    s, b = _arr(dates, stock), _arr(dates, bench)
    ar = es.abnormal_returns(dates, s, b, dates[10])
    assert ar[1] == pytest.approx(0.10 - 0.01)          # 9.0%
    assert ar[2] == pytest.approx(0.11 - 0.00)          # 11.0%
    assert ar[5] == pytest.approx(0.05 - 0.02)          # 3.0%
    assert ar[20] == pytest.approx(-0.10 - 0.0)         # -10.0%


def test_windows_run_out_when_the_history_is_too_short():
    dates = _dates(12)                         # indices 0..11
    flat = _flat(dates, 100.0)
    ar = es.abnormal_returns(dates, _arr(dates, flat), _arr(dates, flat), dates[10])
    assert ar[1] is not None and ar[2] is not None
    assert ar[5] is None and ar[20] is None


def test_no_base_session_gives_no_result():
    dates = _dates(5)
    flat = _flat(dates, 100.0)
    assert es.abnormal_returns(dates, _arr(dates, flat), _arr(dates, flat), dates[0]) is None


def test_reaction_session_without_a_bar_gives_no_result():
    dates = _dates(20)
    flat = _flat(dates, 100.0)
    missing = date(2026, 10, 1) + timedelta(days=100)
    assert es.abnormal_returns(dates, _arr(dates, flat), _arr(dates, flat), missing) is None


def test_only_dates_present_in_both_series_are_used():
    dates = _dates(20)
    stock = _flat(dates[:15], 100.0)
    bench = _flat(dates, 100.0)
    d, s, b = es.aligned(stock, bench)
    assert d == dates[:15]
    assert len(s) == len(b) == 15


def _arr(dates, mapping):
    return np.array([mapping[d] for d in dates], dtype=float)


def test_close_map_reads_tz_aware_bars_and_drops_bad_closes():
    idx = pd.DatetimeIndex(["2026-10-01", "2026-10-02", "2026-10-05"]).tz_localize("America/New_York")
    frame = pd.DataFrame({"Close": [100.0, float("nan"), "x"]}, index=idx)
    assert es.close_map(frame) == {date(2026, 10, 1): 100.0}
    assert es.close_map(None) == {}
    assert es.close_map(pd.DataFrame({"Open": [1.0]})) == {}


# ---------------------------------------------------------------------------
# Statistics and the small-sample rule
# ---------------------------------------------------------------------------

def test_fewer_than_five_filings_show_no_numbers():
    out = es.summarize([0.01, 0.02, 0.03, 0.04])
    assert out["n"] == 4 and out["too_few"] is True
    for key in ("mean_pct", "median_pct", "sd_pct", "ci95_pct", "bootstrap_ci95_pct",
                "share_negative", "mean_abs_pct"):
        assert out[key] is None, key


def test_no_observations_is_too_few_not_zero():
    out = es.summarize([])
    assert out["n"] == 0 and out["too_few"] is True and out["mean_pct"] is None


def test_five_filings_give_the_t_interval_by_hand():
    out = es.summarize([0.01, 0.02, 0.03, 0.04, 0.05])
    assert out["too_few"] is False
    assert out["mean_pct"] == pytest.approx(3.0)
    assert out["median_pct"] == pytest.approx(3.0)
    assert out["sd_pct"] == pytest.approx(math.sqrt(2.5), rel=1e-4)
    # df = 4, t = 2.776; half-width = t * sd / sqrt(n)
    half = 2.776 * math.sqrt(2.5) / math.sqrt(5)
    assert out["ci95_pct"][0] == pytest.approx(3.0 - half, abs=1e-3)
    assert out["ci95_pct"][1] == pytest.approx(3.0 + half, abs=1e-3)
    assert out["mean_abs_pct"] == pytest.approx(3.0)


def test_share_negative_and_mean_absolute_move_use_sign_correctly():
    out = es.summarize([-0.01, 0.02, -0.03, 0.04, -0.05])
    assert out["share_negative"] == pytest.approx(0.6)
    assert out["mean_abs_pct"] == pytest.approx(3.0)
    assert out["mean_pct"] == pytest.approx(-0.6)


def test_t_critical_values():
    assert es.t_critical(4) == pytest.approx(2.776)
    assert es.t_critical(30) == pytest.approx(2.042)
    assert es.t_critical(100) == pytest.approx(1.96)


def test_bootstrap_interval_needs_ten_and_is_deterministic():
    nine = es.summarize([0.001 * i for i in range(9)])
    assert nine["bootstrap_ci95_pct"] is None
    ten_a = es.summarize([0.001 * i for i in range(10)])
    ten_b = es.summarize([0.001 * i for i in range(10)])
    assert ten_a["bootstrap_ci95_pct"] is not None
    assert ten_a["bootstrap_ci95_pct"] == ten_b["bootstrap_ci95_pct"]
    lo, hi = ten_a["bootstrap_ci95_pct"]
    assert lo <= ten_a["mean_pct"] <= hi


# ---------------------------------------------------------------------------
# Confounding: each filing counted once, under its primary item
# ---------------------------------------------------------------------------

def _event(day, codes, reaction):
    items, _ = se.tag_items(codes)
    prim = se.primary_item(items)
    return {"form": "8-K", "date": day, "reaction_session": reaction,
            "primary_item": prim["code"] if prim else None,
            "items": items, "co_filed": [i["code"] for i in items if i["code"] != (prim or {}).get("code")]}


def _study_fixture(n_events=6):
    dates = _dates(60)
    stock = _flat(dates, 100.0)
    bench = _flat(dates, 100.0)
    events = []
    for k in range(n_events):
        r = dates[10 + k * 3]
        stock[r] = 100.0 + (k + 1)          # a rising reaction every time (day 0)
        events.append(_event(f"d{k}", ["2.02", "7.01", "9.01"], r.isoformat()))
    return events, stock, bench


def test_co_filed_items_are_not_counted_again_under_their_other_codes():
    events, stock, bench = _study_fixture(6)
    out = es.study_events(events, stock, bench)
    assert set(out["items"]) == {"2.02"}          # 7.01 and 9.01 never get their own row
    assert out["items"]["2.02"]["filings"] == 6
    assert out["filings_studied"] == 6


def test_a_filing_with_no_items_is_counted_as_unattributed():
    events, stock, bench = _study_fixture(1)
    events.append({"form": "8-K", "date": "x", "reaction_session": "2026-10-11",
                   "primary_item": None, "items": [], "co_filed": []})
    out = es.study_events(events, stock, bench)
    assert out["unattributed"] == 1
    assert out["filings_studied"] == 1


def test_study_gives_numbers_for_an_item_with_five_filings():
    events, stock, bench = _study_fixture(6)
    stat = es.study_events(events, stock, bench)["items"]["2.02"]
    w5 = stat["windows"]["5"]
    assert w5["n"] == 6 and w5["too_few"] is False
    assert w5["share_negative"] == 0.0
    assert stat["headline_window"] == 5
    assert set(stat["windows"]) == {"1", "2", "5", "20"}


def test_study_suppresses_numbers_for_an_item_with_fewer_than_five():
    events, stock, bench = _study_fixture(4)
    stat = es.study_events(events, stock, bench)["items"]["2.02"]
    assert stat["filings"] == 4
    assert stat["windows"]["5"]["too_few"] is True
    assert stat["windows"]["5"]["median_pct"] is None


def test_a_filing_with_a_missing_bar_is_counted_as_no_price_data():
    events, stock, bench = _study_fixture(5)
    events.append(_event("gap", ["8.01"], "2030-01-01"))
    out = es.study_events(events, stock, bench)
    assert out["items"]["8.01"]["no_price_data"] == 1
    assert out["no_price_data"] == 1


def test_study_titles_come_from_the_item_dictionary():
    events, stock, bench = _study_fixture(1)
    assert es.study_events(events, stock, bench)["items"]["2.02"]["title"] == se.tag_item("2.02")["title"]


# ---------------------------------------------------------------------------
# build_report end to end (fake EDGAR, synthetic bars)
# ---------------------------------------------------------------------------

class FakeEdgar:
    def __init__(self, res):
        self.res = res
        self.calls = 0

    def recent_filings(self, symbol, since, forms=None):
        self.calls += 1
        return self.res


def _bars(seed=7):
    idx = pd.bdate_range("2021-01-01", "2026-10-28")
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(idx))))
    return pd.DataFrame({"Close": close}, index=idx)


def _filing(filed, items, accepted, acc="0000320193-26-000042"):
    return {"form": "8-K", "filed": filed, "accepted": accepted, "items": items,
            "accession": acc, "url": None}


TODAY = date(2026, 10, 28)


def test_build_report_lists_recent_filings_and_studies_them():
    rows = [_filing("2026-10-20", ["2.02", "9.01"], "2026-10-20T20:05:00.000Z")]
    rows += [_filing(f"2025-0{m}-15", ["2.02", "9.01"], f"2025-0{m}-15T20:05:00.000Z",
                     acc=f"0000320193-25-00000{m}") for m in range(1, 7)]
    edgar = FakeEdgar({"status": "ok", "cik": 320193, "filings": rows})
    out = es.build_report("AAPL", days=90, today=TODAY, edgar=edgar, bars_loader=lambda s: _bars())
    assert out["status"] == "ok" and out["covered"] is True
    assert [e["date"] for e in out["recent"]] == ["2026-10-20"]
    assert out["recent"][0]["reaction_session"] == "2026-10-21"
    assert out["study_status"] == "ok"
    assert out["study"]["items"]["2.02"]["filings"] == 7
    assert any("not a forecast" in n for n in out["notes"])


def test_build_report_india_is_not_covered_and_makes_no_sec_call():
    edgar = FakeEdgar({"status": "ok", "filings": []})
    out = es.build_report("RELIANCE.NS", today=TODAY, edgar=edgar, bars_loader=lambda s: _bars())
    assert out["status"] == "not_covered" and out["covered"] is False
    assert edgar.calls == 0
    assert out["recent"] == [] and out["study"] == {}


def test_build_report_sec_failure_is_unavailable_with_a_reason():
    edgar = FakeEdgar({"status": "error", "reason": "timeout", "filings": []})
    out = es.build_report("AAPL", today=TODAY, edgar=edgar, bars_loader=lambda s: _bars())
    assert out["status"] == "unavailable"
    assert out["reason"] == "timeout"
    assert out["study"] == {}


def test_build_report_keeps_the_filings_when_prices_fail():
    rows = [_filing("2026-10-20", ["4.02"], "2026-10-20T14:00:00.000Z")]
    edgar = FakeEdgar({"status": "ok", "cik": 320193, "filings": rows})

    def boom(symbol):
        raise RuntimeError("yahoo down")

    out = es.build_report("AAPL", today=TODAY, edgar=edgar, bars_loader=boom)
    assert out["status"] == "ok"
    assert out["recent"][0]["max_severity"] == "HIGH"
    assert out["study_status"] == "unavailable"
    assert "RuntimeError" in out["study_message"]


def test_build_report_marks_a_short_filing_history():
    res = {"status": "ok", "cik": 320193, "filings": [], "coverage_short": True, "covered_from": "2023-02-01"}
    out = es.build_report("AAPL", today=TODAY, edgar=FakeEdgar(res), bars_loader=None)
    assert any("reaches back only to 2023-02-01" in n for n in out["notes"])
    assert out["study_status"] == "not_run"
