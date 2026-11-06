"""P1-4: Fama-French five-factor + momentum exposure of the book.

Offline: the factor files are built in memory. Checked on real data on
2026-11-06 (not part of the suite): SPY market beta 0.99 with R-squared 0.99,
IWM size loading +0.87, QQQ market beta 1.14 with a negative size loading.
"""
from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from portfolio import factor_attribution as fa

DAYS = pd.bdate_range("2025-01-02", periods=300)


def _factors(seed=0):
    rng = np.random.default_rng(seed)
    data = rng.normal(0, 0.01, (len(DAYS), 6))
    frame = pd.DataFrame(data, index=DAYS, columns=list(fa.FACTORS))
    frame["RF"] = 0.0001
    return frame


def _book(factors, betas, alpha=0.0, noise=0.002, seed=1):
    rng = np.random.default_rng(seed)
    r = alpha + factors["RF"] + sum(b * factors[f] for f, b in betas.items())
    return r + rng.normal(0, noise, len(factors))


BETAS = {"Mkt-RF": 1.2, "SMB": 0.4, "HML": -0.3, "RMW": 0.1, "CMA": 0.0, "Mom": 0.25}


def test_known_loadings_are_recovered_within_their_intervals():
    f = _factors()
    returns = pd.DataFrame({"AAA": _book(f, BETAS)})
    out = fa.attribute({"AAA": 10_000.0}, returns, f)
    assert out["available"] and out["days"] == fa._PF.FACTOR_WINDOW_DAYS
    for row in out["factors"]:
        lo, hi = row["ci95"]
        assert lo <= BETAS[row["factor"]] <= hi, row
    assert out["r2"] > 0.95
    # No alpha was built in: the estimate sits within noise (daily noise 0.2% over a year).
    assert abs(out["alpha_annual"]) < 0.10
    lo, hi = out["alpha_annual_ci95"]
    assert lo < out["alpha_annual"] < hi


def test_two_holdings_are_weighted_by_value():
    f = _factors()
    returns = pd.DataFrame({"A": _book(f, {"Mkt-RF": 1.5}, seed=2), "B": _book(f, {"Mkt-RF": 0.5}, seed=3)})
    out = fa.attribute({"A": 3000.0, "B": 1000.0}, returns, f)
    mkt = next(r for r in out["factors"] if r["factor"] == "Mkt-RF")
    assert mkt["beta"] == pytest.approx(0.75 * 1.5 + 0.25 * 0.5, abs=0.05)


def test_indian_listings_are_left_out_and_counted():
    f = _factors()
    returns = pd.DataFrame({"AAA": _book(f, BETAS), "RELIANCE.NS": _book(f, {}, seed=5)})
    out = fa.attribute({"AAA": 3000.0, "RELIANCE.NS": 1000.0}, returns, f)
    assert out["excluded_non_us"] == ["RELIANCE.NS"]
    assert out["coverage"] == pytest.approx(0.75)


def test_too_little_overlap_says_why():
    f = _factors()
    returns = pd.DataFrame({"AAA": _book(f, BETAS)}).iloc[:60]
    out = fa.attribute({"AAA": 1.0}, returns, f)
    assert not out["available"] and "126" in out["reason"]
    assert fa.attribute({"AAA": 1.0}, returns, None)["reason"] == "Factor data unavailable."


def test_newey_west_matches_white_errors_at_zero_lags():
    rng = np.random.default_rng(4)
    X = np.column_stack([np.ones(500), rng.normal(size=(500, 2))])
    y = X @ np.array([0.1, 1.0, -0.5]) + rng.normal(size=500) * (1 + np.abs(X[:, 1]))
    fit = fa.ols_hac(y, X, lags=0)
    resid = y - X @ fit["beta"]
    inv = np.linalg.inv(X.T @ X)
    white = inv @ (X * resid[:, None]).T @ (X * resid[:, None]) @ inv * (500 / 497)
    assert np.allclose(fit["se"], np.sqrt(np.diag(white)))


# --------------------------------------------------------------- data file
FF5_TEXT = """This file was created by using the 202609 CRSP database.

,Mkt-RF,SMB,HML,RMW,CMA,RF
20260929,   -0.26,    0.10,   -0.88,   -0.35,    0.19,    0.01
20260930,    1.00,  -99.99,    0.50,    0.25,   -0.10,    0.01

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""
MOM_TEXT = """Missing data are indicated by -99.99 or -999.

,Mom
20260929,  -0.31
20260930,   0.32

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""


def _zip(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("file.csv", text)
    return buf.getvalue()


def test_parser_reads_percent_rows_and_marks_missing():
    frame = fa.parse_french_csv(FF5_TEXT)
    assert list(frame.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
    assert frame.loc["2026-09-29", "Mkt-RF"] == pytest.approx(-0.0026)
    assert np.isnan(frame.loc["2026-09-30", "SMB"])


def test_load_caches_and_serves_the_last_good_copy_when_a_refresh_fails(tmp_path):
    blobs = {fa.FILES["ff5"]: _zip(FF5_TEXT), fa.FILES["mom"]: _zip(MOM_TEXT)}
    calls = []

    def fetch(url):
        calls.append(url)
        return blobs[url.rsplit("/", 1)[1]]

    path = tmp_path / "ff.csv"
    first = fa.load_factors(fetch=fetch, cache_path=path, now=1_000.0)
    assert first["factors"] is not None and not first["from_cache"] and first["as_of"] == "2026-09-29"
    assert list(first["factors"].columns) == list(fa.FACTORS[:5]) + ["RF", "Mom"]
    again = fa.load_factors(fetch=fetch, cache_path=path, now=path.stat().st_mtime + 60)
    assert again["from_cache"] and len(calls) == 2

    def broken(url):
        raise OSError("offline")
    stale = fa.load_factors(fetch=broken, cache_path=path,
                            now=path.stat().st_mtime + fa.CACHE_TTL_SECONDS + 1)
    assert stale["stale"] and stale["factors"] is not None and "refresh failed" in stale["error"]
    none = fa.load_factors(fetch=broken, cache_path=tmp_path / "missing.csv")
    assert none["factors"] is None and "unavailable" in none["error"]


def test_exposure_view_is_json_safe_and_names_its_source():
    f = _factors()
    out = fa.exposure_view({"AAA": 1.0}, pd.DataFrame({"AAA": _book(f, BETAS)}),
                           {"factors": f, "as_of": "2026-03-04", "stale": False})
    assert out["available"] and out["factors_as_of"] == "2026-03-04" and "French" in out["source"]
    missing = fa.exposure_view({"AAA": 1.0}, None, {"factors": None, "error": "factor data unavailable (OSError)"})
    assert not missing["available"] and "OSError" in missing["reason"]
