"""A requested diversification mandate stays binding.

Audit finding F08: when four or fewer candidates survived the quality gates,
`get_top_recommendations` raised the sector cap to 100% even though the caller
had asked for `diversify_sectors=True`. A `sector_cap_relaxed` flag recorded it,
so the change was not hidden — but the user's constraint was no longer enforced,
and a thin opportunity set silently became a single-sector portfolio.

The corrected contract: a constraint the caller asked for binds. Concentrating a
thin book is a scenario the caller may opt into explicitly
(`allow_sector_cap_relaxation=True`), never a default the engine grants itself.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
MODULES = Path(__file__).resolve().parents[1] / "modules"
for _p in (CORE, MODULES):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

sp = importlib.import_module("SP_ELITE_PRO")


def test_relaxation_is_an_explicit_opt_in_parameter():
    sig = inspect.signature(sp.QuantaEliteTradingSystem.get_top_recommendations)
    assert "allow_sector_cap_relaxation" in sig.parameters


def test_relaxation_is_off_by_default():
    """Off by default, but reachable from configuration.

    The parameter default is now None ("follow configuration") rather than
    False, so that the opt-in can be set without editing source. What must not
    change is the EFFECTIVE default: concentration stays off unless someone
    asks for it, so assert the resolved behaviour rather than the literal.
    """
    sig = inspect.signature(sp.QuantaEliteTradingSystem.get_top_recommendations)
    assert sig.parameters["allow_sector_cap_relaxation"].default is None
    assert sp.TradingConfig.ALLOW_SECTOR_CONCENTRATION is False
    assert _resolve(3, True, sp.TradingConfig.ALLOW_SECTOR_CONCENTRATION) == (0.25, False)


def _resolve(pool_size, diversify, allow_relax):
    return sp.QuantaEliteTradingSystem._resolve_sector_cap(
        base_cap=0.25,
        qualified_count=pool_size,
        diversify_sectors=diversify,
        allow_relaxation=allow_relax,
    )


def test_thin_pool_keeps_the_cap_when_diversification_was_requested():
    cap, relaxed = _resolve(pool_size=3, diversify=True, allow_relax=False)
    assert cap == 0.25
    assert relaxed is False


def test_thin_pool_relaxes_only_when_the_caller_opted_in():
    cap, relaxed = _resolve(pool_size=3, diversify=True, allow_relax=True)
    assert cap == 1.0
    assert relaxed is True


def test_large_pool_is_never_relaxed_even_when_opted_in():
    cap, relaxed = _resolve(pool_size=12, diversify=True, allow_relax=True)
    assert cap == 0.25
    assert relaxed is False


def test_cap_is_irrelevant_when_diversification_was_not_requested():
    """No mandate to enforce, so nothing is being relaxed against the user."""
    cap, relaxed = _resolve(pool_size=3, diversify=False, allow_relax=False)
    assert relaxed is False
