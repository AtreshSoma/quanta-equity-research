"""Legacy scoring modules stay unwired from the live decision path.

The 2026-10-28 comparative audit found about 6,600 lines of older scoring code
that the live engines never import (docs/OPEN_ITEMS.md, "Six unwired modules").
They carry formulas that disagree with the live ones: an SMA-based RSI
(`TechnicalSignalGenerator`), a third DCF with an 8% equity risk premium
(`price_target_calculator`), and a backtester whose Monte Carlo nothing reads
(`robust_backtesting_engine`). OPEN_ITEMS keeps them on purpose until each
roadmap phase is scheduled, so this test does not delete them. It makes sure
nothing on the live path quietly starts importing them, which would bring a
second definition of a number the app already computes.

To wire one of these in deliberately, remove it from LEGACY below in the same
change and say why in the commit.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEGACY = {
    "enhanced_analyzer",
    "enhanced_technical_indicators",
    "TechnicalSignalGenerator",
    "price_target_calculator",
    "Alpha_Picks_Engine",
    "robust_backtesting_engine",
    "realtime_catalyst_integration",
    "entry_optimizer",
}

# Everything that can reach a user: the three engines, the decision core, the
# validation and tracking layers, portfolio, web app and MCP server.
LIVE_GLOBS = [
    "core/SP_ShortTerm.py",
    "core/SP_ELITE_PRO.py",
    "core/TradingAnalysis_ELITE.py",
    "core/sp_shortterm_backtest.py",
    "core/streamlit_app.py",
    "core/streamlit_support.py",
    "core/engine/*.py",
    "core/apex_quant/*.py",
    "core/tracking/*.py",
    "core/portfolio/*.py",
    "core/brokers/*.py",
    "webapp/*.py",
    "mcp_server/*.py",
]

_IMPORT = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"
    r"|_import_optional\(\s*['\"]([\w.]+)['\"]",
    re.MULTILINE,
)


def _imported_names(text: str) -> set[str]:
    names: set[str] = set()
    for m in _IMPORT.finditer(text):
        dotted = next(g for g in m.groups() if g)
        names.update(dotted.split("."))
    return names


def test_live_code_does_not_import_legacy_scoring_modules():
    offenders = []
    for pattern in LIVE_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            hits = _imported_names(path.read_text(encoding="utf-8", errors="replace")) & LEGACY
            if hits:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {sorted(hits)}")
    assert not offenders, (
        "Live code imports legacy scoring modules with conflicting formulas:\n  "
        + "\n  ".join(offenders)
    )


def test_legacy_list_names_files_that_exist():
    # A stale entry would make the guard above pass vacuously.
    present = {p.stem for p in ROOT.glob("core/*.py")} | {p.stem for p in ROOT.glob("modules/*.py")}
    assert LEGACY <= present, f"not found: {sorted(LEGACY - present)}"
