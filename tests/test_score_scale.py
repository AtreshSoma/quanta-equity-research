"""Decision D3 (October 2026): one score scale on the page, out of 100.

The scanner scores 0-10 and the decision ladder reads it times SCORE.TO_100.
The map is linear only while every scanner tier times that factor is the
matching ladder band, so the page can convert at display time.
"""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths()
from engine import thresholds as T  # noqa: E402


def test_scanner_tiers_times_the_factor_are_the_ladder_bands():
    bands = {label: lo for lo, label in T.LADDER.SCORE_BANDS}
    k = T.SCORE.TO_100
    assert k * 10 == 100, "a 0-10 score fills the 0-100 scale"
    assert T.SCORE.ACCUMULATE * k == bands["ACCUMULATE"] == T.LADDER.MIN_SCORE_FOR_BUY
    assert T.SCORE.GOOD * k == bands["BUY"]
    assert T.SCORE.EXCELLENT * k == bands["STRONG_BUY"]


def test_the_engine_and_the_page_use_the_same_factor():
    from pathlib import Path
    core = Path(T.__file__).resolve().parents[1] / "SP_ELITE_PRO.py"
    src = core.read_text(encoding="utf-8")
    assert "overall_score or 0.0) * _T.SCORE.TO_100" in src
    assert "SCORE" in T.as_dict() and T.as_dict()["SCORE"]["TO_100"] == T.SCORE.TO_100
