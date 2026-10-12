"""No decision is keyed to a ticker, a company name or a deal price.

The remediation fixed named cases (a pending acquisition, an R:R mismatch) with
generic rules. This guard keeps them generic:

1. The names, companies and deal figures the audit and its fixtures discuss
   never appear in non-test source, not even in comments, except as members of
   an index-constituent list (a line of several quoted symbols).
2. Decision code never compares a ticker with a literal symbol. Benchmarks and
   indexes used as market data are the only exception.
3. The event and level modules carry no dollar amounts: every price they print
   is read from a filing or computed from levels.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT
WEBAPP = ROOT / "webapp"

SOURCE_DIRS = [ENGINE / "core", ENGINE / "modules"]
SOURCE_FILES = [WEBAPP / "app.py", *sorted((WEBAPP / "static").glob("*.js")), *sorted((WEBAPP / "templates").glob("*.html"))]

DECISION_CODE = [
    ENGINE / "core" / "SP_ELITE_PRO.py",
    ENGINE / "core" / "TradingAnalysis_ELITE.py",
    *sorted((ENGINE / "core" / "engine").glob("*.py")),
    *sorted((ENGINE / "core" / "portfolio").glob("*.py")),
    WEBAPP / "app.py",
    *sorted((WEBAPP / "static").glob("*.js")),
]

# Named in the audit or the F1 fixtures. Word-bounded, case-sensitive for symbols.
AUDIT_NAMES = [r"\bLNTH\b", r"\bHST\b", r"Lantheus", r"Host Hotels", r"\bEXDX\b",
               r"Example Diagnostics", r"Parent Holdings", r"Example Capital"]
DEAL_FIGURES = [r"\b102\.50?\b", r"\b109\.80\b", r"\b111\.86\b"]

# Market data, not picks: benchmark ETFs and index symbols.
BENCHMARKS = {"SPY", "QQQ", "IWM", "DIA", "^VIX", "^GSPC", "^NSEI", "^BSESN", "^IXIC", "^DJI"}

CONSTITUENT_LINE = re.compile(r"""(['"][A-Z^][A-Z0-9.^=\-]{0,11}['"]\s*[,:]\s*){3,}""")
TICKER_COMPARE = re.compile(
    r"""\b(?:ticker|symbol|sym|tkr)\b\s*(?:===?|!==?|\bin\b|\bnot\s+in\b)\s*[\[\(\{]?\s*['"]([A-Z^][A-Z0-9.^=\-]{0,11})['"]""")


def _sources():
    files = list(SOURCE_FILES)
    for d in SOURCE_DIRS:
        files += [p for p in d.rglob("*") if p.suffix in (".py", ".js", ".html")
                  and "tests" not in p.parts and "__pycache__" not in p.parts]
    return [p for p in files if p.is_file()]


def _lines(path):
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def test_audit_names_and_deal_figures_are_not_in_source():
    hits = []
    for path in _sources():
        for i, line in enumerate(_lines(path), 1):
            if CONSTITUENT_LINE.search(line):
                continue
            for pat in AUDIT_NAMES + DEAL_FIGURES:
                if re.search(pat, line):
                    hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, "name or deal literal outside a constituent list:\n" + "\n".join(hits)


def test_no_decision_is_keyed_to_a_ticker_literal():
    hits = []
    for path in DECISION_CODE:
        for i, line in enumerate(_lines(path), 1):
            for m in TICKER_COMPARE.finditer(line):
                if m.group(1) not in BENCHMARKS:
                    hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, "ticker literal in a decision:\n" + "\n".join(hits)


@pytest.mark.parametrize("module", ["corporate_events.py", "levels.py"])
def test_event_and_level_modules_print_no_fixed_prices(module):
    text = (ENGINE / "core" / "engine" / module).read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert not re.findall(r"\$\s?\d", code), f"{module} contains a dollar literal"


def test_the_guard_catches_what_it_is_for():
    assert TICKER_COMPARE.search('if ticker == "LNTH":')
    assert TICKER_COMPARE.search("is_tech = result.ticker in ['NVDA', 'AMD']")
    assert TICKER_COMPARE.search("if (rec.symbol === 'HST') {")
    assert not TICKER_COMPARE.search("if ticker == other_ticker:")
    assert CONSTITUENT_LINE.search("'AVB', 'EQR', 'VTR', 'INVH', 'HST',")
    assert any(re.search(p, "pinned by a $102.50 cash deal") for p in DEAL_FIGURES)
