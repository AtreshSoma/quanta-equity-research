"""Golden-master regression: the engine's output must not change unnoticed.

This is the safety net every later recalibration phase leans on. Each fixture
replays one recorded analysis with the clock frozen and all network blocked,
then diffs the normalised result against a frozen snapshot.

A failure here is not automatically a bug -- most later phases change behaviour
deliberately. It means *the change is visible and has to be acknowledged*. The
workflow is:

    1. Read the printed diff. Confirm every line is a change you intended.
    2. Accept it:  python tests/golden/record.py --expected-only <TICKER>
    3. Commit the updated snapshot alongside the code change, so review sees
       the behavioural delta next to its cause.

Re-recording fixtures from the network (dropping `--expected-only`) is a
different and rarer operation: it changes the *inputs*, which invalidates the
comparison entirely.

Run only these:      pytest tests/test_golden_master.py -v
Skip them:           pytest -m 'not golden'
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "golden"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _paths import ensure_paths  # noqa: E402

ensure_paths()

import cohorts  # noqa: E402
from _normalize import diff, normalize  # noqa: E402
from _recorder import EXPECTED_DIR, available_fixtures, load_recording, replay_io  # noqa: E402

pytestmark = pytest.mark.golden

ACCOUNT_SIZE = 100_000.0
FIXTURES = available_fixtures()


def _expected_path(ticker: str) -> Path:
    return EXPECTED_DIR / f"{ticker.replace('.', '_').replace('^', '_')}.json"


def _run_replay(ticker: str):
    """Replay one fixture and return (snapshot, misses)."""
    import TradingAnalysis_ELITE as elite
    from freezegun import freeze_time

    recording = load_recording(ticker)

    with freeze_time(recording.recorded_at):
        # strict=False so a fixture gap is reported as a miss and asserted on
        # below, rather than aborting before we can say which key was absent.
        with replay_io(recording, strict=False):
            analyzer = elite.QuantaEliteAnalyzer({})
            result = analyzer.analyze(ticker, account_size=ACCOUNT_SIZE)
            payload = elite._mcp_result_to_dict(result)

    return normalize(payload), list(recording.misses)


@pytest.mark.skipif(not FIXTURES, reason="no golden fixtures recorded yet")
@pytest.mark.parametrize("ticker", FIXTURES)
def test_output_matches_snapshot(ticker: str) -> None:
    """The normalised analysis result is byte-identical to the frozen snapshot."""
    expected_file = _expected_path(ticker)
    if not expected_file.is_file():
        pytest.fail(
            f"Fixture exists for {ticker} but no snapshot at {expected_file}. "
            f"Create it: python tests/golden/record.py --expected-only {ticker}"
        )

    expected = json.loads(expected_file.read_text(encoding="utf-8"))
    actual, _ = _run_replay(ticker)

    differences = diff(expected["result"], actual)
    if differences:
        shown = "\n  ".join(differences[:40])
        more = f"\n  ... and {len(differences) - 40} more" if len(differences) > 40 else ""
        why = cohorts.BY_SYMBOL[ticker].why if ticker in cohorts.BY_SYMBOL else ""
        pytest.fail(
            f"{ticker} output changed ({len(differences)} field(s)).\n"
            f"This fixture guards: {why}\n\n  {shown}{more}\n\n"
            f"If intended: python tests/golden/record.py --expected-only {ticker}"
        )


@pytest.mark.skipif(not FIXTURES, reason="no golden fixtures recorded yet")
@pytest.mark.parametrize("ticker", FIXTURES)
def test_replay_needs_no_unrecorded_io(ticker: str) -> None:
    """Every external read the engine makes is covered by the fixture.

    Guards fixture rot: if a later change reads a new yfinance property or a
    new endpoint, the fixture silently serves a neutral default and the
    snapshot comparison could still pass while the code is untested. This
    fails instead.
    """
    _, misses = _run_replay(ticker)
    if misses:
        listed = "\n  ".join(str(m) for m in misses[:15])
        pytest.fail(
            f"{ticker}: {len(misses)} unrecorded external read(s). The fixture "
            f"is incomplete, so these paths ran on defaults:\n  {listed}\n\n"
            f"Re-record: python tests/golden/record.py {ticker}"
        )


@pytest.mark.skipif(not FIXTURES, reason="no golden fixtures recorded yet")
def test_snapshots_are_deterministic() -> None:
    """Replaying twice gives the same answer.

    Catches unseeded randomness reaching the output. The Monte Carlo simulator
    takes an explicit seed, but a future change could introduce an unseeded
    generator, and that would make every other test here flaky rather than
    failing honestly.
    """
    ticker = FIXTURES[0]
    first, _ = _run_replay(ticker)
    second, _ = _run_replay(ticker)
    differences = diff(first, second)
    assert not differences, (
        f"{ticker} is not deterministic across two replays -- unseeded "
        f"randomness reaches the output:\n  " + "\n  ".join(differences[:20])
    )


def test_cohort_coverage_is_reported() -> None:
    """Report which planned branches are not yet guarded.

    Always passes. It exists so the coverage gap is visible in `-v` output
    rather than being something you have to remember to check; the cohort is
    recorded incrementally because recording hits Yahoo hard.
    """
    recorded = set(FIXTURES)
    missing_priority = [s for s in cohorts.PRIORITY if s not in recorded]
    total = len(cohorts.COHORT)

    print(f"\ngolden-master coverage: {len(recorded)}/{total} cohort tickers recorded")
    print(f"priority set: {len(cohorts.PRIORITY) - len(missing_priority)}/{len(cohorts.PRIORITY)}")
    if missing_priority:
        print("unguarded priority branches:")
        for symbol in missing_priority:
            entry = cohorts.BY_SYMBOL.get(symbol)
            print(f"  {symbol:14s} {entry.why if entry else ''}")


@pytest.mark.skipif(not FIXTURES, reason="no golden fixtures recorded yet")
def test_replay_seals_the_http_transport_funnel() -> None:
    """No live HTTP may escape replay, from any module.

    Regression guard for a real defect in the first version of this harness.
    It patched `APIClient.fetch_json`, but `modules/catalyst_scanner.py`,
    `modules/catalyst_intelligence_engine.py` and `modules/sec_edgar_parser.py`
    call `requests.get` directly against SEC EDGAR, FDA and
    ClinicalTrials.gov. Those calls ran LIVE during replay, so `catalyst_score`
    tracked whatever the SEC returned that minute -- and because
    `has_catalyst` switches on jump diffusion in the Monte Carlo simulator, one
    unrecorded HTTP call moved fifteen snapshot fields. The suite passed or
    failed on network weather, and took 160s instead of 4s.

    The seal is now at `requests.sessions.Session.request`, the funnel every
    outbound call passes through (`requests.get` is a one-shot Session). This
    test asserts a direct `requests.get` inside the replay context is
    intercepted rather than dialling out.
    """
    import requests

    recording = load_recording(FIXTURES[0])

    with replay_io(recording, strict=False):
        # An address that would fail slowly if it actually left the process.
        response = requests.get("https://example.invalid/golden-seal-probe", timeout=5)

    assert response.status_code == 503, (
        "a direct requests.get escaped the replay seal; fixtures are not "
        "hermetic and snapshots will drift with network conditions"
    )
    assert response.json() == {}
    assert any(key[0] == "request" for key in recording.misses), (
        "the unrecorded HTTP call was served but not recorded as a miss, so "
        "fixture gaps would go unnoticed"
    )
