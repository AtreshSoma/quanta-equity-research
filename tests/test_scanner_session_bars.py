"""The scanner scores completed candles and still validates older bad bars."""

import pandas as pd
import pytest

from SP_ELITE_PRO import DataValidator


def _bars():
    index = pd.bdate_range(end="2026-09-08", periods=70)
    return pd.DataFrame({
        "Open": 100.0, "High": 101.0, "Low": 99.0,
        "Close": 100.0, "Volume": 100_000,
    }, index=index)


@pytest.mark.parametrize("market,now", [
    ("US", "2026-09-08 09:40:00-04:00"),
    ("US", "2026-09-08 13:40:00+00:00"),
    ("IN", "2026-09-08 10:00:00+05:30"),
])
def test_in_progress_bar_is_excluded(market, now):
    bars = _bars()
    bars.loc[bars.index[-1], ["Open", "High", "Low", "Close"]] = [110, 111, 112, 110]
    completed = DataValidator.completed_daily_history(bars, market, pd.Timestamp(now))
    assert len(completed) == len(bars) - 1
    assert DataValidator.validate_history(completed).is_valid
    assert not DataValidator.validate_history(bars).is_valid


@pytest.mark.parametrize("market,now", [
    # Settled: the close plus the settle window (thresholds.RESEARCH.BAR_SETTLE_MINUTES).
    ("US", "2026-09-08 16:30:00-04:00"),
    ("IN", "2026-09-08 15:50:00+05:30"),
])
def test_completed_bar_is_still_validated(market, now):
    bars = _bars()
    bars.loc[bars.index[-1], ["Open", "High", "Low", "Close"]] = [110, 111, 112, 110]
    completed = DataValidator.completed_daily_history(bars, market, pd.Timestamp(now))
    assert len(completed) == len(bars)
    assert not DataValidator.validate_history(completed).is_valid


def test_older_bad_bar_is_never_hidden():
    bars = _bars()
    bars.iloc[-2, bars.columns.get_loc("Close")] = 0
    completed = DataValidator.completed_daily_history(
        bars, now=pd.Timestamp("2026-09-08 10:00:00-04:00")
    )
    assert not DataValidator.validate_history(completed).is_valid


def test_an_invalid_bar_is_named_with_its_prices_and_size():
    bars = _bars()
    bars.loc[bars.index[-3], ["Open", "High", "Low", "Close"]] = [100.0, 100.0, 99.0, 100.5]
    result = DataValidator.validate_history(bars, "X")
    assert not result.is_valid
    text = result.errors[0]
    assert text.startswith("X: 1 rows with invalid OHLC relationships")
    assert bars.index[-3].date().isoformat() in text
    assert "H 100.0000" in text and "C 100.5000" in text and "off by 0.4975%" in text
