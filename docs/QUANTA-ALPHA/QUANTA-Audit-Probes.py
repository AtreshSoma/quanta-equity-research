"""Offline, isolated reproductions against the reviewed source snapshot."""
import ast
import __future__
import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import numpy as np

ROOT = Path('PATH_TO_REVIEWED_QUANTA_CHECKOUT')  # Set before running
sys.path.insert(0, str(ROOT / 'core'))
source = ROOT / 'core/TradingAnalysis_ELITE.py'
tree = ast.parse(source.read_text())
node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SignalTracker')
ns = dict(threading=threading, time=time, datetime=datetime, timedelta=timedelta,
          logger=logging.getLogger('probe'), np=np,
          Config=SimpleNamespace(ACTIONABLE_WIN_PCT=2, HOLD_CORRECT_BAND_PCT=3))
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec',
             flags=__future__.annotations.compiler_flag), ns)
tracker = ns['SignalTracker'].__new__(ns['SignalTracker'])
tracker._outcomes_lock = threading.Lock()
tracker._outcomes_last_run = 0
tracker._outcomes_running = False
tracker._save_history = lambda: None
calls = []
tracker._get_price = lambda ticker: calls.append(ticker) or 120.0
tracker.history = [dict(ticker='TEST', signal='BUY', price=100.,
    timestamp=(datetime.now()-timedelta(days=11)).isoformat(),
    outcome_5d='WIN', price_5d=105., outcome_10d=None, price_10d=None, outcome_30d=None)]
tracker.update_outcomes(force=True)
assert tracker.history[0]['price_10d'] == 105. and not calls
reuse = dict(recorded_5d_price=105., available_current_price=120.,
             recorded_10d_price=tracker.history[0]['price_10d'], fetch_count=len(calls))
tracker.history.append(dict(ticker='PENDING', signal='BUY', price=100.,
    timestamp=datetime.now().isoformat(), outcome_5d=None, outcome_10d=None))
stats = tracker.get_statistics(days=90)
assert stats['overall_win_rate'] == 50.

from quant import risk_model
rng = np.random.default_rng(0)
returns = rng.normal(size=(200,4)) * np.array([.001,.02,.02,.02])
risk_model.HAS_SCIPY = False
opt = risk_model.RobustOptimizer(use_shrinkage=False).optimize(returns, upper_cap=.4)
assert max(opt.weights) > .4

from engine import DecisionInputs, resolve_final_action
decision = resolve_final_action(DecisionInputs(ticker='TEST', composite_score=80,
    current_price=100, stop_loss=90, target_price=130, risk_reward_ratio=3,
    risk_grade='A', has_existing_position=False))
assert decision.allocation_eligible
out = {
    'signal_tracker_horizon_reuse': reuse,
    'unmatured_denominator': {'resolved_buy_signals':1, 'resolved_wins':1,
                            'unresolved_buy_signals':1, 'reported_win_rate_pct':stats['overall_win_rate']},
    'disconnected_optimizer_fallback': {'requested_cap':.4, 'reported_cap':opt.upper_cap_used,
                                     'weights':opt.as_list(), 'method':opt.method},
    'decision_core_has_no_validation_artifact_gate': {'action':decision.final_action.value,
                                                   'allocation_eligible':decision.allocation_eligible},
    'scope':'Synthetic offline class/function probes, not a live-data backtest or production run.'
}
print(json.dumps(out, indent=2))
