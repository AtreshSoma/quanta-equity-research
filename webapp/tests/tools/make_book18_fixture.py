"""Build tests/fixtures/my_portfolio_view_18.json: an 18-holding book from the REAL build_view.

Synthetic holdings shaped like a real 2026-09-29 screenshot of the page (a 22% position,
five risk-grade F trims, seven holdings on hold for new money, two funds, one holding
with no analysis), so visual checks see grouped guidance, a crowded allocation and a
long holdings list. Test data only; never shown as a real account.

    python webapp/tests/tools/make_book18_fixture.py
    BOOK=18 python webapp/tests/tools/make_insights_fixture.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(_ENGINE / "core"), str(_ENGINE / "modules"), str(_ENGINE / "tests")]
sys.path.insert(0, str(Path(__file__).resolve().parent))
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "my_portfolio_view_18.json"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from make_my_portfolio_fixture import res  # noqa: E402
from portfolio import analytics as an  # noqa: E402
from portfolio.models import AccountSummary, Portfolio, Position, TaxLot  # noqa: E402
from portfolio.service import build_view  # noqa: E402
from test_my_portfolio import decision  # noqa: E402

TOTAL = 37_686.45
CASH = 371.64
blk = lambda code, text, remedy: {"code": code, "text": text, "remedy": remedy}  # noqa: E731
RR = blk("LOW_REWARD_RISK", "Reward/risk to the horizon target is below the 1.5:1 minimum",
         "Reward/risk of at least 1.5:1 (a better entry or a higher target)")
NO_TARGET = blk("INVALID_TARGET", "No valuation target: no analyst consensus or valid DCF for this instrument",
                "An analyst consensus target or a valid DCF")
D_CAP = blk("RISK_GRADE_D_CAP", "Risk grade D caps a fresh buy at Accumulate", "Risk grade improving to C or better")
D_HOLD = blk("RISK_GRADE_D_HOLD", "Risk grade D limits new money to a reduced Accumulate size", "Risk grade improving to C or better")
F = blk("RISK_GRADE_F", "Risk grade F (very high volatility/drawdown) allows no new money", "Risk grade improving to D or better")

# symbol, name, weight, return on cost, session %, sector, beta, vol, decision kwargs
BOOK = [
    ("AAPL", "Apple", 0.2200, 1.557, 0.32, "Technology", 1.2, 0.27, dict(action="BUY", score=66)),
    ("MSFT", "Microsoft", 0.1090, 0.847, 0.23, "Technology", 0.9, 0.24, dict(action="BUY", score=58, new_money="WATCH", blockers=[RR])),
    ("VOO", "Vanguard S&P 500 ETF", 0.1010, 0.261, 0.22, "", 1.0, 0.16, dict(action="BUY", score=55, new_money="WATCH", blockers=[NO_TARGET])),
    ("GOOGL", "Alphabet", 0.0910, 0.724, 0.06, "Communication Services", 1.05, 0.29, dict(action="BUY", score=64)),
    ("JNJ", "Johnson & Johnson", 0.0720, 0.470, -0.02, "Healthcare", 0.5, 0.17, dict(action="BUY", score=57, new_money="WATCH", blockers=[RR])),
    ("AMZN", "Amazon", 0.0550, 0.235, 0.14, "Consumer Cyclical", 1.25, 0.33, dict(action="BUY", score=63)),
    ("META", "Meta Platforms", 0.0390, 0.633, 0.69, "Communication Services", 1.3, 0.38, dict(action="ACCUMULATE", score=60, blockers=[D_CAP], risk="D")),
    ("TSM", "Taiwan Semiconductor", 0.0360, 0.071, 0.07, "Technology", 1.35, 0.46, dict(action="WATCHLIST", score=62, blockers=[F], risk="F")),
    ("NVDA", "NVIDIA", 0.0358, 0.912, 1.05, "Technology", 1.8, 0.55, dict(action="WATCHLIST", score=68, blockers=[F], risk="F")),
    ("LRCX", "Lam Research", 0.0350, 0.388, -0.41, "Technology", 1.6, 0.52, dict(action="WATCHLIST", score=59, blockers=[F], risk="F")),
    ("LLY", "Eli Lilly", 0.0300, 0.214, 0.35, "Healthcare", 0.45, 0.31, dict(action="ACCUMULATE", score=61, blockers=[D_CAP], risk="D")),
    ("COST", "Costco", 0.0300, 0.402, 0.11, "Consumer Defensive", 0.75, 0.22, dict(action="BUY", score=56, new_money="WATCH", blockers=[RR])),
    ("ORCL", "Oracle", 0.0300, 0.518, -0.27, "Technology", 1.1, 0.35, dict(action="WATCHLIST", score=52)),
    ("MU", "Micron Technology", 0.0280, -0.064, -0.88, "Technology", 1.7, 0.58, dict(action="WATCHLIST", score=55, blockers=[F], risk="F")),
    ("AVGO", "Broadcom", 0.0280, 1.204, 0.52, "Technology", 1.45, 0.44, dict(action="WATCHLIST", score=63, blockers=[F], risk="F")),
    ("NBIX", "Neurocrine Biosciences", 0.0240, 0.118, -0.35, "Healthcare", 0.6, 0.34, dict(action="ACCUMULATE", score=60, blockers=[D_HOLD], risk="D")),
    ("QQQM", "Invesco Nasdaq 100 ETF", 0.0230, 0.196, 0.28, "", 1.1, 0.21, dict(action="WATCHLIST", score=53)),
    ("RVII", "Rise Vision II", 0.0030, -0.215, 1.9, "Miscellaneous", 1.0, 0.8, None),
]
assert abs(sum(b[2] for b in BOOK) + CASH / TOTAL - 1) < 0.002, sum(b[2] for b in BOOK)

acct = AccountSummary("5a7c1e9b3d2f4a60", "Individual", "individual", portfolio_value=TOTAL, cash=CASH,
                      buying_power=CASH, realized_pnl=30.34, realized_pnl_period="3month", can_trade_via_agent=False)
q = "2026-09-29T20:30:00+00:00"
positions, analysis = [], {}
invested = TOTAL - CASH
scale = invested / sum(b[2] * TOTAL for b in BOOK)
for i, (sym, name, w, ret, day, sector, beta, vol, dk) in enumerate(BOOK):
    value = w * TOTAL * scale
    px = round(40 + (i * 37.3) % 480, 2)
    qty = round(value / px, 6)
    avg = round(px / (1 + ret), 2)
    prev = round(px / (1 + day / 100), 4)
    lot_day = f"2026-0{1 + i % 8}-{10 + i:02d}"
    positions.append(Position(sym, qty, name=name, average_cost=avg, last_price=px, previous_close=prev,
                              quote_as_of=q, quote_source="broker", sector=sector,
                              tax_lots=[TaxLot(qty, avg, lot_day)],
                              earnings_date="2026-10-02" if sym in ("GOOGL", "AMZN") else None))
    if dk is None:
        analysis[sym] = {"state": "error", "error": {"status": "insufficient_data", "title": "Too little price history for a Decision",
                                                     "retryable": False}}
        continue
    risk = dk.pop("risk", "B")
    dec = decision(stop=round(px * 0.955, 2), **dk)
    dec["risk_grade"] = risk
    analysis[sym] = {"state": "done", "result": res(dec, sector, beta, name=name, vol=vol, is_etf=not sector)}

pf = Portfolio("robinhood", acct, positions, "2026-09-29T20:27:00+00:00", [])
status = {"provider": "robinhood", "provider_name": "Robinhood", "state": "connected", "state_label": "Connected (read-only)",
          "read_only": True, "execution_enabled": False,
          "analysis": {"state": "complete", "done": 18, "total": 18, "finished_at": "2026-09-29T20:29:00+00:00"}}
rng = np.random.default_rng(11)
mkt = rng.normal(0, 0.01, 250)
rets = pd.DataFrame({b[0]: mkt * b[6] + rng.normal(0, b[7] / 16, 250) for b in BOOK})
NOW = datetime(2026, 9, 30, 0, 30, tzinfo=timezone.utc)      # 8:30 PM New York: market closed


def build():
    view = build_view(pf, analysis, rets, "", an.PortfolioPolicy(), status, NOW)
    return json.loads(json.dumps(view, default=str))


if __name__ == "__main__":
    v = build()
    FIXTURE.write_text(json.dumps(v, indent=1) + "\n")
    print({r["symbol"]: r["guidance"]["action"] for r in v["portfolio"]["positions"]})
    print(v["portfolio"]["overview"]["risk"])
