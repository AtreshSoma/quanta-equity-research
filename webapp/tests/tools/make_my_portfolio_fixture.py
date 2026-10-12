"""Build tests/fixtures/my_portfolio_view.json from the REAL build_view.

The holdings are synthetic (test data, never shown as a real account); the view
assembly, analytics and guidance are the production code. The JS tests render
this file, and tests/test_my_portfolio.py fails if it drifts from build_view.

    python webapp/tests/tools/make_my_portfolio_fixture.py
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path
_ENGINE = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(_ENGINE / "core"), str(_ENGINE / "modules"), str(_ENGINE / "tests")]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "my_portfolio_view.json"
from portfolio import analytics as an
from portfolio.models import AccountSummary, Portfolio, Position, TaxLot, DataIssue
from portfolio.service import build_view
from test_my_portfolio import decision

def res(dec, sector, beta, **kw):
    base = {"decision": dec, "sector": sector, "industry": kw.pop("industry", ""), "company_name": kw.pop("name", ""),
            "risk": {"beta": beta, "beta_status": "measured", "annualized_volatility": kw.pop("vol", 0.28),
                     "max_drawdown": -0.22, "risk_grade": dec.get("risk_grade")},
            "analysis_timestamp": "2026-09-26T14:02:00+00:00", "data_provider": "yfinance",
            "thesis": {"summary": kw.pop("summary", "Durable franchise; valuation is the debate."),
                       "bull_case": ["Services mix lifts margins", "Buybacks shrink the share count"],
                       "bear_case": ["Hardware cycle is maturing"]},
            "fundamental": {"roe": 148.7, "net_margin": 24.3, "revenue_growth_yoy": 6.1, "debt_to_equity": 1.8,
                            "pe_ratio": 31.2, "forward_pe": 28.4, "dividend_yield": 0.0045, "quality_score": 71},
            "valuation": {"dcf_status": "measured", "dcf_upside": -8.5, "valuation_label": "fairly_valued", "target_base": 238.0},
            "market_intel": {"target_mean": 245.0, "analyst_count": 38},
            "technical": {"trend_direction": "UP", "rsi": 58, "adx": 24, "support_level": 212.0, "resistance_level": 236.0,
                          "week_52_low": 164.1, "week_52_high": 237.5},
            "catalysts": {"last_earnings_result": "beat", "last_earnings_surprise_pct": 3.2},
            "account_context": {"note": "Sized against a $128,400 account."}}
    base.update(kw)
    return base

blk = lambda code, text, remedy: {"code": code, "text": text, "remedy": remedy}
acct = AccountSummary("0f3c9a1b2d4e5f60", "Individual", "individual", portfolio_value=90_576.0, cash=9_650.0,
                      buying_power=9_650.0, realized_pnl=2_314.55, realized_pnl_period="YTD", can_trade_via_agent=False)
q = "2026-09-25T20:00:00+00:00"
P = lambda s, n, qty, avg, px, prev, **kw: Position(s, qty, name=n, average_cost=avg, last_price=px, previous_close=prev,
                                                    quote_as_of=q, quote_source="broker", **kw)
positions = [
    P("AAPL", "Apple", 60, 142.10, 228.40, 226.9, tax_lots=[TaxLot(40, 120.0, "2023-05-02"), TaxLot(20, 186.3, "2025-11-03")],
      earnings_date="2026-10-29", earnings_verified=True, earnings_timing="pm", eps_estimate=1.87),
    P("NVDA", "NVIDIA", 180, 61.20, 176.30, 174.1, tax_lots=[TaxLot(180, 61.2, "2023-01-10")], earnings_date="2026-11-19"),
    P("MSFT", "Microsoft", 20, 402.00, 431.50, 433.0, tax_lots=[],
      earnings_date="2026-10-28", earnings_verified=True, earnings_timing="pm", eps_estimate=3.12),
    P("KO", "Coca-Cola", 110, 58.40, 69.20, 69.0, tax_lots=[],
      ex_dividend_date="2026-09-30", payable_date="2026-10-15", dividend_per_share=0.51, distribution_frequency="quarterly"),
    P("XOM", "Exxon Mobil", 70, 118.00, 109.60, 110.9, tax_lots=[TaxLot(70, 118.0, "2025-12-15")],
      earnings_date="2026-10-30", earnings_verified=False),
    P("RIVN", "Rivian", 300, 16.50, 11.80, 12.1, tax_lots=[TaxLot(300, 16.5, "2025-10-20")]),
    P("BRK.B", "Berkshire Hathaway", 12, None, 468.00, 466.2),
    P("JNJ", "Johnson & Johnson", 15, 148.00, 161.20, 160.4, tax_lots=[TaxLot(15, 148.0, "2024-08-01")]),
]
positions[6].missing.append("cost_basis")
issues = [DataIssue("COST_BASIS_MISSING", "BRK.B: Robinhood did not report a cost basis; gain/loss is not shown.", "BRK.B")]
analysis = {
    "AAPL": {"state": "done", "result": res(decision("BUY", 67, stop=209.0), "Technology", 1.2, name="Apple")},
    "NVDA": {"state": "done", "result": res(decision("STRONG_BUY", 77, stop=158.0), "Technology", 1.7, name="NVIDIA", vol=0.52)},
    "MSFT": {"state": "done", "result": res(decision("ACCUMULATE", 58, stop=398.0), "Technology", 0.9, name="Microsoft")},
    "KO": {"state": "done", "result": res(decision("WATCHLIST", 51, blockers=[blk("HOLD_NOT_OWNED", "Overall score is in the neutral band (45–55)", "Overall score reaching 55 or higher")], stop=None), "Consumer Defensive", 0.6, name="Coca-Cola")},
    "XOM": {"state": "done", "result": res(decision("AVOID", 41, holder="TRIM", blockers=[blk("SCORE_BEARISH", "Overall score is below 45 (bearish)", "Overall score recovering above 45")], stop=None), "Energy", 0.9, name="Exxon Mobil")},
    "RIVN": {"state": "done", "result": res(decision("AVOID", 29, holder="EXIT", blockers=[blk("RISK_GRADE_F", "Risk grade F (very high volatility/drawdown) allows no new money", "Risk grade improving to D or better"), blk("TARGET_BELOW_ENTRY", "12-month valuation target is at or below the entry", "A valuation target above the entry price")], stop=None), "Consumer Cyclical", 1.9, name="Rivian", vol=0.71)},
    "JNJ": {"state": "done", "result": res(decision("BUY", 66, stop=150.0), "Healthcare", 0.5, name="Johnson & Johnson")},
    "BRK.B": {"state": "error", "error": {"status": "provider_rate_limited", "title": "Market data provider rate-limited the request", "suggested_action": "Retry in a few minutes."}},
}
pf = Portfolio("robinhood", acct, positions, "2026-09-26T14:05:11+00:00", issues)
status = {"provider": "robinhood", "provider_name": "Robinhood", "state": "connected", "state_label": "Connected (read-only)",
          "read_only": True, "execution_enabled": False,
          "analysis": {"state": "complete", "done": 8, "total": 8, "finished_at": "2026-09-26T14:09:40+00:00"}}
import numpy as np, pandas as pd
rng = np.random.default_rng(3); mkt = rng.normal(0, 0.01, 250)
rets = pd.DataFrame({s: mkt * b + rng.normal(0, 0.012, 250) for s, b in
                     [("AAPL", 1.2), ("NVDA", 1.9), ("MSFT", 1.1), ("KO", 0.4), ("XOM", 0.6), ("RIVN", 1.5), ("BRK.B", 0.8), ("JNJ", 0.5)]})
rets["MSFT"] = rets["AAPL"] * 0.9 + rng.normal(0, 0.003, 250)
NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


def build():
    view = build_view(pf, analysis, rets, "", an.PortfolioPolicy(), status, NOW)
    return json.loads(json.dumps(view, default=str))


if __name__ == "__main__":
    v = build()
    FIXTURE.write_text(json.dumps(v, indent=1) + "\n")
    print({r["symbol"]: r["guidance"]["action"] for r in v["portfolio"]["positions"]})
