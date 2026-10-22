"""Point-in-time fundamentals: no look-ahead, honest gaps, the engines' own units."""
from __future__ import annotations

import pandas as pd
import pytest

from apex_quant import pit_fundamentals as pf


def _row(val, end, filed, start=None, accn="a"):
    r = {"val": val, "end": end, "filed": filed, "accn": accn, "form": "10-Q"}
    if start:
        r["start"] = start
    return r


def _facts(us_gaap, dei=None):
    return {"facts": {"us-gaap": {k: {"units": u} for k, u in us_gaap.items()},
                      "dei": {k: {"units": u} for k, u in (dei or {}).items()}}}


def _company(revenue_rows, ni_rows=None, shares_rows=None, instants=None):
    g = {"Revenues": {"USD": revenue_rows}}
    if ni_rows:
        g["NetIncomeLoss"] = {"USD": ni_rows}
    if shares_rows:
        g["WeightedAverageNumberOfDilutedSharesOutstanding"] = {"shares": shares_rows}
    for k, rows in (instants or {}).items():
        g[k] = {"USD": rows}
    return _facts(g)


# One fiscal year (Jan-Dec 2024) reported the usual way: Q1 alone, then YTD.
FY24 = [
    _row(100, "2024-03-31", "2024-04-25", "2024-01-01"),
    _row(210, "2024-06-30", "2024-07-25", "2024-01-01"),
    _row(330, "2024-09-30", "2024-10-25", "2024-01-01"),
    _row(460, "2024-12-31", "2025-02-10", "2024-01-01"),
]
FY23_Q = [
    _row(80, "2023-03-31", "2023-04-25", "2023-01-01"),
    _row(170, "2023-06-30", "2023-07-25", "2023-01-01"),
    _row(270, "2023-09-30", "2023-10-25", "2023-01-01"),
    _row(380, "2023-12-31", "2024-02-10", "2023-01-01"),
]


def test_quarters_are_rebuilt_from_year_to_date_figures():
    facts = pf._facts_for(_company(FY24), ("Revenues",), "USD")
    q = pf.quarterly_series(facts)
    assert list(q.values) == [100, 110, 120, 130]  # Q4 = FY - 9M


def test_a_fact_is_unknown_until_the_day_after_it_was_filed():
    p = pf.PITFundamentals("AAA", _company(FY23_Q + FY24), {})
    # 2025-02-10 is the 10-K's filing day: the FY figure is not usable yet.
    before = p.info_at(pd.Timestamp("2025-02-10"), 10.0)
    after = p.info_at(pd.Timestamp("2025-02-11"), 10.0)
    assert before["totalRevenue"] == (380 - 270) + 100 + 110 + 120   # TTM to Q3 2024
    assert after["totalRevenue"] == 460
    # Same quarter a year earlier: 130 vs 110.
    assert after["revenueGrowth"] == pytest.approx(130 / 110 - 1)


def test_a_restatement_counts_only_after_it_was_filed():
    restated = _row(90, "2024-03-31", "2024-08-01", "2024-01-01", accn="r")
    p = pf.PITFundamentals("AAA", _company(FY23_Q + FY24[:2] + [restated]), {})
    before = p.info_at(pd.Timestamp("2024-07-30"), 10.0)
    after = p.info_at(pd.Timestamp("2024-08-02"), 10.0)
    # TTM to Q2 2024 = Q3'23 + Q4'23 + Q1 + Q2 = 100 + 110 + 100 + 110 either way: with Q1
    # restated to 90, Q2 (= H1 - Q1) becomes 120, because H1 itself was not restated.
    assert before["totalRevenue"] == after["totalRevenue"] == 420
    # The quarter-on-year-ago growth does move: Q2 is 110 before, 120 after (vs 90).
    assert before["revenueGrowth"] == pytest.approx(110 / 90 - 1)
    assert after["revenueGrowth"] == pytest.approx(120 / 90 - 1)


def test_old_figures_go_stale_instead_of_carrying_forward():
    p = pf.PITFundamentals("AAA", _company(FY23_Q), {})
    assert p.info_at(pd.Timestamp("2024-03-01"), 10.0)
    assert p.info_at(pd.Timestamp("2025-01-15"), 10.0) == {}


def test_nothing_known_before_the_first_filing():
    p = pf.PITFundamentals("AAA", _company(FY24), {})
    assert p.info_at(pd.Timestamp("2024-01-15"), 10.0) == {}


def test_shares_convert_to_split_adjusted_units_by_filing_date():
    # 4-for-1 split on 2024-08-30. A share count filed before it is in old
    # units; a 10-K filed after it restates prior periods in new units.
    pre = pf.Fact(pd.Timestamp("2024-04-01"), pd.Timestamp("2024-06-30"), 1_000_000_000,
                  pd.Timestamp("2024-07-25"), 0)
    restated = pf.Fact(pd.Timestamp("2024-04-01"), pd.Timestamp("2024-06-30"), 4_000_000_000,
                       pd.Timestamp("2025-02-10"), 0)
    adj = pf.SplitAdjuster({"2024-08-30": 4.0})
    assert adj.shares(pre) == adj.shares(restated) == 4_000_000_000


def test_info_uses_yfinance_units_and_skips_what_was_not_reported():
    ni = [_row(v * 0.2, r["end"], r["filed"], r["start"]) for v, r in
          zip([80, 170, 270, 380, 100, 210, 330, 460], FY23_Q + FY24)]
    shares = [_row(1e9, "2024-12-31", "2025-02-10", "2024-10-01")]
    inst = {"StockholdersEquity": [_row(400, "2024-12-31", "2025-02-10")],
            "AssetsCurrent": [_row(300, "2024-12-31", "2025-02-10")],
            "LiabilitiesCurrent": [_row(150, "2024-12-31", "2025-02-10")],
            "LongTermDebtNoncurrent": [_row(200, "2024-12-31", "2025-02-10")]}
    p = pf.PITFundamentals("AAA", _company(FY23_Q + FY24, ni, shares, inst), {})
    info = p.info_at(pd.Timestamp("2025-03-01"), 2e-7)
    assert info["profitMargins"] == pytest.approx(0.2)          # fraction, like yfinance
    assert info["returnOnEquity"] == pytest.approx(92 / 400)
    assert info["debtToEquity"] == pytest.approx(50.0)          # percent, like yfinance
    assert info["currentRatio"] == pytest.approx(2.0)
    assert info["trailingEps"] == pytest.approx(92 / 1e9)
    assert "grossMargins" not in info and "freeCashflow" not in info


def test_negative_equity_leaves_roe_and_leverage_unreported():
    inst = {"StockholdersEquity": [_row(-50, "2024-12-31", "2025-02-10")],
            "LongTermDebtNoncurrent": [_row(200, "2024-12-31", "2025-02-10")]}
    ni = [_row(v * 0.2, r["end"], r["filed"], r["start"]) for v, r in zip([100, 210, 330, 460], FY24)]
    p = pf.PITFundamentals("AAA", _company(FY23_Q + FY24, ni, instants=inst), {})
    info = p.info_at(pd.Timestamp("2025-03-01"), 10.0)
    assert "returnOnEquity" not in info and "debtToEquity" not in info


def test_misscaled_share_counts_are_ignored():
    facts = _company(FY24, shares_rows=[_row(711.1, "2024-12-31", "2025-02-10", "2024-10-01")])
    p = pf.PITFundamentals("AAA", facts, {})
    assert "sharesOutstanding" not in p.info_at(pd.Timestamp("2025-03-01"), 10.0)


def test_elite_fundamental_runs_the_live_analyzer_on_the_rebuilt_payload():
    ni = [_row(v * 0.25, r["end"], r["filed"], r["start"]) for v, r in
          zip([80, 170, 270, 380, 100, 210, 330, 460], FY23_Q + FY24)]
    inst = {"StockholdersEquity": [_row(400, "2024-12-31", "2025-02-10")]}
    p = pf.PITFundamentals("AAA", _company(FY23_Q + FY24, ni, instants=inst), {},
                           sector="Technology", industry="Software")
    f = pf.elite_fundamental(p, pd.Timestamp("2025-03-01"), 10.0)
    assert f is not None
    assert f.net_margin == pytest.approx(25.0)      # the engine's own percent scaling
    assert f.has("roe") and f.has("net_margin") and not f.has("current_ratio")
    assert f._sector == "Technology"
    assert pf.elite_fundamental(p, pd.Timestamp("2023-01-15"), 10.0) is None


def test_predecessor_facts_merge_concept_by_concept():
    a = _company(FY24)
    b = _company(FY23_Q)
    merged = pf.merge_companyfacts(a, b)
    rows = merged["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    assert len(rows) == len(FY24) + len(FY23_Q)
