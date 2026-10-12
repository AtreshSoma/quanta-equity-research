"""Regression tests for the TradingAnalysis_ELITE fixes.

Each test is named for the finding it locks in, and its docstring records what
the old behaviour was — so a change that reintroduces it fails here with an
explanation rather than a bare assertion error.
"""

from __future__ import annotations

import ast
import importlib
import io
import sys
import tokenize
from contextlib import redirect_stdout
from pathlib import Path

import pytest


CORE = Path(__file__).resolve().parents[1] / "core"
MODULE_PATH = CORE / "TradingAnalysis_ELITE.py"

if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

ta = importlib.import_module("TradingAnalysis_ELITE")


# ── source helpers ──────────────────────────────────────────────────────────

def _code_only(text: str) -> str:
    """Blank comments and docstrings, preserving line/column structure.

    Source assertions must not be satisfiable — or defeatable — by the very
    comments that document a fix.
    """
    lines = text.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except tokenize.TokenError:
        return text

    blanks = []
    prev = tokenize.NEWLINE
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            blanks.append((tok.start, tok.end))
            continue
        # NL (a non-logical newline, e.g. inside brackets) must NOT trigger
        # this: a string on its own line inside a call is an argument, not a
        # docstring, and blanking it hides real code from the assertions.
        if tok.type == tokenize.STRING and prev in (
            tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
        ):
            blanks.append((tok.start, tok.end))
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok.type

    grid = [list(line) for line in lines]
    for (srow, scol), (erow, ecol) in blanks:
        for row in range(srow, erow + 1):
            if row - 1 >= len(grid):
                break
            line = grid[row - 1]
            begin = scol if row == srow else 0
            finish = ecol if row == erow else len(line)
            for col in range(begin, min(finish, len(line))):
                if line[col] != "\n":
                    line[col] = " "
    return "".join("".join(line) for line in grid)


def _source() -> str:
    return _code_only(MODULE_PATH.read_text(encoding="utf-8"))


def _func_code(name: str, cls: str | None = None) -> str:
    """One function's body as code-only text, located via AST not string search.

    *cls* scopes the lookup to a class, which this module needs: eleven
    different classes define a method called ``analyze``, and an unscoped walk
    returns whichever comes first in the file.
    """
    text = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)

    def _extract(node) -> str:
        body = text.splitlines()[node.lineno - 1: node.end_lineno]
        return _code_only("\n".join(body))

    if cls is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and member.name == name:
                        return _extract(member)
        raise AssertionError(f"{cls}.{name} not found in {MODULE_PATH.name}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return _extract(node)
    raise AssertionError(f"function {name!r} not found in {MODULE_PATH.name}")


# ── fixtures ────────────────────────────────────────────────────────────────

def _technical(**kw):
    defaults = dict(atr=7.58, support_level=216.20, resistance_level=227.92,
                    rsi=58.3, adx=15.9, volume_sma=1_000_000.0, volume_ratio=1.0)
    defaults.update(kw)
    return ta.TechnicalIndicators(**defaults)


def _valuation(base=0.0, cons=0.0, aggr=0.0):
    v = ta.ValuationMetrics()
    v.target_base = base
    v.target_conservative = cons
    v.target_aggressive = aggr
    return v


def _gen():
    return ta.TradeSetupGenerator()


# ── T1: the empty TradeSetup ────────────────────────────────────────────────

class TestTradeSetupCoverage:
    def test_hold_and_no_position_get_levels(self):
        """[T1] HOLD, REDUCE and NO_POSITION fell through BOTH branches of
        generate(), so the struct kept its all-zero defaults. Since the
        decision core derives its candidate from composite_score independently
        of the signal, any HOLD scoring >=55 became a buy-tier candidate with
        stop_loss=0.0 — invalid — and was downgraded to WATCHLIST."""
        for signal in (ta.Signal.HOLD, ta.Signal.NO_POSITION):
            ts = _gen().generate(227.43, signal, _technical(), ta.RiskMetrics(), 39.0)
            assert ts.levels_available, f"{signal} still has no levels"
            assert ts.stop_loss > 0
            assert ts.entry_price > 0
            assert ts.stop_loss < ts.entry_price
            # ...but it is not an entry order.
            assert ts.is_actionable_entry is False

    def test_buy_tier_is_marked_actionable(self):
        for signal in (ta.Signal.STRONG_BUY, ta.Signal.BUY, ta.Signal.ACCUMULATE):
            ts = _gen().generate(227.43, signal, _technical(), ta.RiskMetrics(), 60.0)
            assert ts.is_actionable_entry is True
            assert ts.levels_available is True

    def test_exit_stance_publishes_no_long_levels(self):
        for signal in (ta.Signal.SELL, ta.Signal.STRONG_SELL, ta.Signal.REDUCE):
            ts = _gen().generate(227.43, signal, _technical(), ta.RiskMetrics(), 40.0)
            assert ts.levels_available is False
            assert ts.is_actionable_entry is False
            assert ts.stop_loss == 0.0
            assert "exit signal" in ts.setup_basis

    def test_a_hold_still_gets_a_stop(self):
        """[T1] A holder on a HOLD verdict was given no risk management at all."""
        ts = _gen().generate(100.0, ta.Signal.HOLD, _technical(atr=2.0, support_level=90.0),
                             ta.RiskMetrics(), 50.0)
        assert 0 < ts.stop_loss < 100.0
        assert ts.trailing_stop_pct > 0

    def test_no_price_yields_no_levels(self):
        ts = _gen().generate(0.0, ta.Signal.BUY, _technical(), ta.RiskMetrics(), 60.0)
        assert ts.levels_available is False
        assert "no valid price" in ts.setup_basis


# ── T2: the consistency check that never ran ────────────────────────────────

def test_consistency_check_reads_a_field_that_exists():
    """[T2] The pre-render guard read `ts.target_1`, which TradeSetup has never
    had — the fields are target_conservative/base/aggressive. The resulting
    AttributeError was swallowed by a bare `except Exception` and logged at
    DEBUG, so validate_consistency() threw on every ticker and NEVER RAN."""
    assert "target_1" not in _source(), "the phantom field reference is back"
    fields = set(ta.TradeSetup().__dataclass_fields__)
    assert "target_1" not in fields
    assert {"target_conservative", "target_base", "target_aggressive"} <= fields


def test_attribute_errors_in_the_guard_are_not_swallowed_silently():
    """[T2] A missing field is a code defect. It must not hide behind DEBUG."""
    body = _func_code("display_analysis")
    assert "except AttributeError" in body, "AttributeError is lumped in again"


# ── T3: fabricated display levels ───────────────────────────────────────────

class TestNoFabricatedLevels:
    def test_display_sites_no_longer_invent_levels(self):
        """[T3] Three panels each carried their own fallbacks — stop =
        current*0.95, targets = current*1.05/1.10/1.15, rr hardcoded 1.5 in one
        place and 2.0 in another, win_prob = 0.5. So an empty setup rendered a
        -5% stop and a +5/10/15% "profit plan", formatted identically to real
        levels, with the R:R depending on which panel drew it."""
        for fn in ("display_position_deep_dive", "display_what_to_do",
                   "display_reconciled_recommendation"):
            body = _func_code(fn)
            assert "current * 0.95" not in body, f"{fn}: invented stop is back"
            assert "current * 1.05" not in body, f"{fn}: invented target is back"
            assert "current * 1.10" not in body, f"{fn}: invented target is back"
            assert "current * 1.15" not in body, f"{fn}: invented target is back"
            assert "else 2.0" not in body, f"{fn}: invented R:R is back"
            assert "else 1.5" not in body, f"{fn}: invented R:R is back"
            assert "trade_levels(result)" in body, f"{fn}: not reading shared levels"

    def test_trade_levels_reports_absence_rather_than_substituting(self):
        r = ta.AnalysisResult(ticker="X", company_name="X")
        r.current_price = 100.0
        r.trade_setup = ta.TradeSetup()      # never populated
        lv = ta.trade_levels(r)
        assert lv["available"] is False
        for key in ("entry", "stop", "t1", "t2", "t3", "rr"):
            assert lv[key] == 0.0, f"{key} was substituted with a plausible number"

    def test_trade_levels_passes_through_real_values(self):
        r = ta.AnalysisResult(ticker="X", company_name="X")
        r.current_price = 227.43
        r.trade_setup = _gen().generate(
            227.43, ta.Signal.BUY, _technical(), ta.RiskMetrics(), 60.0,
            valuation=_valuation(base=260.0, cons=210.0, aggr=300.0),
        )
        lv = ta.trade_levels(r)
        assert lv["available"] is True
        assert lv["stop"] == pytest.approx(r.trade_setup.stop_loss)
        entry = r.trade_setup.entry_price
        # T2 is the horizon target (12-month upside pro-rated to 63 sessions);
        # the 12-month valuation target is carried separately as context.
        assert lv["t2"] == pytest.approx(entry + (260.0 - entry) * 63 / 252)
        assert lv["valuation_target"] == pytest.approx(260.0)


# ── T4: WATCHLIST addressed as a holder ─────────────────────────────────────

class TestAudienceFraming:
    def _result(self, action, owned=False):
        r = ta.AnalysisResult(ticker="X", company_name="X")
        r.final_action = action
        r.has_existing_position = owned
        r.current_price = 100.0
        return r

    def test_watchlist_is_not_addressed_as_a_holder(self):
        """[T4] The legacy Signal enum has no WATCHLIST member, so both
        WATCHLIST and HOLD_EXISTING map onto Signal.HOLD. Three panels branched
        on `signal` and told a reader who owns nothing to "Keep existing
        positions", "Maintain existing, set stops" and "Hold existing
        position, no new entries"."""
        fr = ta.audience_framing(self._result("WATCHLIST"))
        assert fr["action"] == "WATCHLIST"
        assert "WATCHLIST" in fr["headline"]
        for text in (fr["headline"], fr["one_liner"], fr["new_buyer"]):
            assert "maintain" not in text.lower()
            assert "keep existing" not in text.lower()

    def test_hold_existing_is_addressed_as_a_holder(self):
        fr = ta.audience_framing(self._result("HOLD_EXISTING", owned=True))
        assert "HOLD" in fr["headline"]
        assert "own" in fr["one_liner"].lower() or "keep" in fr["one_liner"].lower()

    def test_every_final_action_has_framing(self):
        for action in ("STRONG_BUY", "BUY", "ACCUMULATE", "HOLD_EXISTING",
                       "WATCHLIST", "AVOID", "INVALID", ""):
            fr = ta.audience_framing(self._result(action))
            assert fr["headline"] and fr["one_liner"]
            assert fr["new_buyer"] and fr["holder"]

    def test_panels_branch_on_final_action_not_the_legacy_signal(self):
        for fn in ("display_decision_snapshot", "display_what_to_do"):
            assert "audience_framing(result)" in _func_code(fn), fn


# ── T5: the decision core as a ratchet ──────────────────────────────────────

def test_decision_core_cannot_upgrade_past_the_signal_generator():
    """[T5] The core's verdict was adopted unconditionally, and
    `_candidate_from_score` maps composite_score alone — so a HOLD scoring 65+
    silently became a BUY without any of the confirmation SignalGenerator
    requires."""
    body = _func_code("analyze", cls="QuantaEliteAnalyzer")
    assert "_ACTION_RANK" in body, "conservatism ranking is not applied"
    assert "_core_rank <= _sig_rank" in body, "the core can still upgrade"


def test_action_rank_orders_conviction():
    r = ta._ACTION_RANK
    assert r["STRONG_BUY"] > r["BUY"] > r["ACCUMULATE"] > r["WATCHLIST"] > r["AVOID"]
    assert r["INVALID"] < r["AVOID"]


# ── T6: the uncalibrated win probability ────────────────────────────────────

class TestNoUncalibratedProbability:
    def test_win_probability_is_not_emitted(self):
        """[T6] `win_probability = 0.4 + confidence/100 * 0.2` was an arbitrary
        figure presented as a probability, and `expected_value` was computed
        from it — so a made-up win rate drove a dollar number on the report."""
        for conf in (10.0, 39.0, 60.0, 95.0):
            ts = _gen().generate(100.0, ta.Signal.BUY, _technical(atr=2.0),
                                 ta.RiskMetrics(), conf)
            assert ts.win_probability == 0.0
            assert ts.expected_value == 0.0

    def test_a_confidence_tier_is_provided_instead(self):
        tiers = {
            conf: _gen().generate(100.0, ta.Signal.BUY, _technical(atr=2.0),
                                  ta.RiskMetrics(), conf).confidence_tier
            for conf in (30.0, 60.0, 80.0)
        }
        assert tiers[30.0] == "LOW"
        assert tiers[60.0] == "MEDIUM"
        assert tiers[80.0] == "HIGH"

    def test_no_panel_prints_a_win_probability(self):
        source = _source()
        assert "Win Prob" not in source
        assert "Win Probability" not in source


# ── T7: one trailing-stop definition ────────────────────────────────────────

class TestSingleTrailingRule:
    def test_hardcoded_trailing_percentages_are_gone(self):
        """[T7] Four incompatible rules coexisted: a hardcoded 7% in one panel,
        `stop_pct + 0.02` (rendering as "9% below peak") in the holder plan,
        an unset `trailing_stop_pct` field, and an ATR multiple elsewhere."""
        source = _source()
        assert "current * 0.93" not in source, "the hardcoded 7% trail is back"
        assert "stop_pct + 0.02" not in source, "the derived 9% trail is back"

    def test_trailing_stop_is_atr_derived(self):
        ts = _gen().generate(100.0, ta.Signal.BUY, _technical(atr=3.0),
                             ta.RiskMetrics(), 60.0)
        expected = ta.TradeSetupGenerator.TRAILING_ATR_MULT * 3.0 / 100.0 * 100
        assert ts.trailing_stop_pct == pytest.approx(expected, abs=0.1)
        assert ts.stop_type == "ATR"

    def test_wider_atr_gives_a_wider_trail(self):
        trails = [
            _gen().generate(100.0, ta.Signal.BUY, _technical(atr=a),
                            ta.RiskMetrics(), 60.0).trailing_stop_pct
            for a in (1.0, 3.0, 6.0)
        ]
        assert trails[0] < trails[1] < trails[2]


# ── T8: the caveat that was truncated away ──────────────────────────────────

def test_price_target_caveat_is_not_in_a_truncatable_title():
    """[T8] The disclaimer lived in the Rich panel TITLE, which is truncated to
    the panel width — it rendered as "Target Confidence: Heuristic (not a va"
    and the caveat was cut off mid-word."""
    source = _source()
    assert "WEIGHTED PRICE TARGETS (12 months) - Target Confidence" not in source
    # The caveat now lives in the panel BODY, where nothing truncates it.
    assert "not a validated probability" in source.lower()


# ── T11 / T12 / T13: targets and the R:R that follows ───────────────────────

class TestTargetsAndRiskReward:
    def test_risk_reward_is_not_constant_by_construction(self):
        """[T11] Targets were pure R-multiples of the stop (1.5R/2.5R/4R),
        which makes risk_reward_ratio a CONSTANT — 2.42:1 for every ticker
        once the round-trip cost is deducted. A ratio that cannot vary carries
        no information, so the 1.5:1 minimum could never reject anything."""
        rrs = set()
        for base in (105.0, 115.0, 140.0, 180.0):
            ts = _gen().generate(
                100.0, ta.Signal.BUY, _technical(atr=2.0, support_level=90.0,
                                                 resistance_level=200.0),
                ta.RiskMetrics(), 60.0, valuation=_valuation(base=base),
            )
            rrs.add(round(ts.risk_reward_ratio, 2))
        assert len(rrs) >= 3, f"R:R barely varies with the valuation target: {rrs}"

    def test_targets_come_from_the_valuation_blend_when_available(self):
        ts = _gen().generate(
            100.0, ta.Signal.BUY, _technical(atr=2.0, support_level=90.0,
                                             resistance_level=200.0),
            ta.RiskMetrics(), 60.0, valuation=_valuation(base=140.0, aggr=175.0),
        )
        assert ts.target_base == pytest.approx(140.0)
        assert "valuation" in ts.setup_basis

    def test_r_multiple_fallback_is_labelled_as_such(self):
        ts = _gen().generate(100.0, ta.Signal.BUY, _technical(atr=2.0),
                             ta.RiskMetrics(), 60.0, valuation=None)
        assert "no valuation blend available" in ts.setup_basis

    def test_a_target_below_spot_does_not_read_as_reward(self):
        """[T12] `reward_amount = abs(target_base - entry_price)` was harmless
        while targets were always above entry. Once they came from the
        valuation blend they could land below spot — and abs() then reported a
        27% OVERVALUATION as 27% of upside, giving R:R 6.15 on a name the
        engine's own DCF called overvalued."""
        ts = _gen().generate(
            100.0, ta.Signal.BUY, _technical(atr=2.0, support_level=90.0),
            ta.RiskMetrics(), 60.0, valuation=_valuation(base=73.0),
        )
        assert ts.has_long_reward is False
        assert ts.risk_reward_ratio == 0.0
        assert ts.reward_amount == 0.0

    def test_no_partial_exit_targets_are_invented_when_there_is_no_reward(self):
        """[T13] `ValuationMetrics.target_conservative` is the BEAR CASE of the
        blend, not a profit-taking level. Used directly it put "Target 1 →
        take a third" BELOW spot, and below the stop."""
        ts = _gen().generate(
            100.0, ta.Signal.BUY, _technical(atr=2.0, support_level=90.0),
            ta.RiskMetrics(), 60.0, valuation=_valuation(base=73.0, cons=60.0),
        )
        assert ts.target_conservative == 0.0
        assert ts.target_aggressive == 0.0

    def test_level_ordering_holds_whenever_there_is_reward(self):
        for base, cons, aggr in ((140.0, 85.0, 180.0), (110.0, 105.0, 120.0),
                                 (130.0, 0.0, 0.0)):
            ts = _gen().generate(
                100.0, ta.Signal.BUY, _technical(atr=2.0, support_level=90.0,
                                                 resistance_level=250.0),
                ta.RiskMetrics(), 60.0, valuation=_valuation(base, cons, aggr),
            )
            assert ts.has_long_reward is True
            assert ts.stop_loss < ts.entry_price <= ts.target_conservative \
                <= ts.target_base <= ts.target_aggressive, (
                f"ordering broken for base={base}: stop={ts.stop_loss:.2f} "
                f"entry={ts.entry_price:.2f} t1={ts.target_conservative:.2f} "
                f"t2={ts.target_base:.2f} t3={ts.target_aggressive:.2f}")

    def test_absurd_valuation_targets_are_rejected(self):
        for base in (5.0, 400.0):     # 0.05x and 4x spot
            ts = _gen().generate(100.0, ta.Signal.BUY, _technical(atr=2.0),
                                 ta.RiskMetrics(), 60.0,
                                 valuation=_valuation(base=base))
            assert "no valuation blend available" in ts.setup_basis

    def test_stop_distance_stays_in_a_tradeable_band(self):
        for atr in (0.01, 2.0, 40.0):
            ts = _gen().generate(100.0, ta.Signal.BUY,
                                 _technical(atr=atr, support_level=1.0),
                                 ta.RiskMetrics(), 60.0)
            distance = (ts.entry_price - ts.stop_loss) / ts.entry_price
            assert 0.004 <= distance <= 0.25, f"atr={atr} gave {distance:.3%}"
            assert ts.stop_loss > 0

    def test_stop_is_never_inside_normal_daily_noise(self):
        """Audit C3: support 0.8 ATR below entry produced a 1.4% stop (JPM)
        and a blanket 5% cap put MSTR's stop inside one day's range."""
        ts = _gen().generate(100.0, ta.Signal.BUY,
                             _technical(atr=2.0, support_level=99.0), ta.RiskMetrics(), 60.0)
        dist = ts.entry_price - ts.stop_loss
        assert dist >= 1.5 * 2.0 - 1e-9
        wide = _gen().generate(100.0, ta.Signal.BUY,
                               _technical(atr=6.0, support_level=0.0), ta.RiskMetrics(), 60.0)
        assert (wide.entry_price - wide.stop_loss) / wide.entry_price > 0.05


# ── T14: factor weights and the missing quality row ─────────────────────────

class TestScoreTransparency:
    def test_weights_and_quality_row_are_displayed(self):
        """[T14] `ScoreBreakdown.weights` carried the real weights and was
        never rendered, so a reader could not tell Technical carries 35% and
        Quality 8%, nor reconcile the sub-scores against the composite. The
        Quality row was missing from the table entirely, hiding one of the six
        weighted inputs."""
        body = _func_code("display_analysis")
        assert "getattr(result.scores, 'weights'" in body
        assert '"Quality", result.scores.quality_score' in body
        assert "Contrib" in body

    def test_weights_are_present_on_a_real_analysis(self):
        weights = ta.ScoreBreakdown().weights
        assert isinstance(weights, dict)


# ── T15: verdict vs entry timing ────────────────────────────────────────────

class TestTimingConsistency:
    def _result(self, action, timing):
        r = ta.AnalysisResult(ticker="X", company_name="X")
        r.final_action = action
        r.current_price = 100.0
        r.technical = _technical(volume_ratio=1.2)
        expl = ta.SignalExplanation()
        expl.timing = timing
        r.signal_explanation = expl
        return r

    def test_unclear_timing_is_carried_into_the_verdict(self):
        """[T15] The snapshot said "Entry is actionable" while the guidance
        panel said "Buy Now: NO / New Buyer: Wait" for the same ticker. A
        reader cannot act on both."""
        fr = ta.audience_framing(self._result("ACCUMULATE", "WAIT / NO CLEAR EDGE"))
        assert "stage" in fr["headline"].lower() or "stage" in fr["one_liner"].lower()
        assert "wait" in fr["new_buyer"].lower()

    def test_clear_timing_reads_as_actionable(self):
        fr = ta.audience_framing(self._result("BUY", "CONFIRMED"))
        assert "actionable" in fr["new_buyer"].lower()

    def test_thin_volume_is_not_a_green_light(self):
        r = self._result("BUY", "CONFIRMED")
        r.technical = _technical(volume_ratio=0.3)
        assert ta._entry_timing_is_clear(r) is False


# ── T10: box alignment ──────────────────────────────────────────────────────

class TestBoxRendering:
    def test_emoji_count_as_two_columns(self):
        """[T10] Panels padded with `:<12` format specs, which count Python
        characters. Emoji occupy two terminal cells, so each one pushed its row
        a column short and every box border drifted."""
        assert ta._display_width("🟢 BUY") == 6      # emoji 2 + space 1 + "BUY" 3
        assert ta._display_width("BUY") == 3
        assert ta._display_width("") == 0

    def test_box_borders_line_up(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ta._box(["🟢 BUY", "plain", "🔵 HOLD — EXISTING POSITION ONLY"], width=50)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        widths = {ta._display_width(ln) for ln in lines}
        assert len(widths) == 1, f"box rows have differing widths: {widths}"

    def test_long_rows_wrap_instead_of_overflowing(self):
        long_text = ("Scale in. Start partial and add on confirmation. Timing is "
                     "not confirmed — stage the entry, do not chase.")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ta._box([long_text], width=60)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert len(lines) > 3, "long row did not wrap"
        widths = {ta._display_width(ln) for ln in lines}
        assert len(widths) == 1, f"wrapped rows broke alignment: {widths}"


# ── the new execution-context panel ─────────────────────────────────────────

class TestExecutionContext:
    def test_panel_exists_and_is_wired_in(self):
        assert hasattr(ta, "display_execution_context")
        assert "display_execution_context(result, sym)" in _source()

    def test_panel_renders_without_a_trade_setup(self):
        r = ta.AnalysisResult(ticker="X", company_name="X")
        r.current_price = 100.0
        r.trade_setup = ta.TradeSetup()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ta.display_execution_context(r)
        out = buf.getvalue()
        assert "EXECUTION CONTEXT" in out
        assert "LIQUIDITY" in out and "EVENT RISK" in out
        assert "DATA PROVENANCE" in out

    def test_missing_earnings_date_is_called_a_data_gap(self):
        r = ta.AnalysisResult(ticker="X", company_name="X")
        r.current_price = 100.0
        r.trade_setup = ta.TradeSetup()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ta.display_execution_context(r)
        assert "not an all-clear" in buf.getvalue()

    def test_liquidity_is_assessed_against_the_proposed_size(self):
        r = ta.AnalysisResult(ticker="X", company_name="X")
        r.current_price = 100.0
        r.technical = _technical(volume_sma=1_000_000.0)
        r.trade_setup = _gen().generate(100.0, ta.Signal.BUY, r.technical,
                                        ta.RiskMetrics(), 60.0)
        ps = ta.PositionSizing()
        ps.recommended_shares = 50
        ps.position_value = 5_000.0
        r.position_sizing = ps
        buf = io.StringIO()
        with redirect_stdout(buf):
            ta.display_execution_context(r)
        out = buf.getvalue()
        assert "of a day's volume" in out
        assert "Round-trip" in out or "round trip" in out.lower()


# ── position sizing surfaced at all ─────────────────────────────────────────

def test_position_size_reaches_the_output():
    """[T9] `account_size` is a required argument and PositionSizer runs on
    every analysis, yet no panel ever displayed shares, allocation or risk."""
    for fn in ("display_decision_snapshot", "display_what_to_do"):
        body = _func_code(fn)
        assert "position_sizing" in body, f"{fn} still shows no size"
        assert "recommended_shares" in body, f"{fn} still shows no share count"
