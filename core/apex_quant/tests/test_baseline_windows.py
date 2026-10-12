"""Random entry must draw the comparison sample from the held-out fold."""
import pandas as pd

from core.apex_quant.baselines import random_entry_signals
from core.apex_quant.strategy import Signal, StrategyParams


def test_random_entries_keep_signal_count_and_next_bar_inside_test_window():
    index = pd.bdate_range("2010-01-01", periods=3000)
    features = pd.DataFrame({"close": 100.0, "rsi14": 50.0, "atr_pct": 0.02,
                             "mom63": 0.1, "dollar_vol20": 1e8}, index=index)
    start, end = index[2800], index[2900]
    actual = [Signal("X", index[i], i, .04, .08, .02, 1e8)
              for i in range(2810, 2830)]
    random = random_entry_signals(actual, {"X": features}, StrategyParams(),
                                  seed=7, start=start, end=end)
    assert len(random) == len(actual)
    assert len({s.entry_index for s in random}) == len(actual)
    assert all(start <= s.signal_date < index[s.entry_index + 1] < end for s in random)
    assert random == random_entry_signals(actual, {"X": features}, StrategyParams(),
                                          seed=7, start=start, end=end)
