"""Audit F1: the corporate-event gate.

A name under a signed cash acquisition trades at a spread to the deal price. Its
volatility, structural highs and analyst targets no longer describe its upside,
so the scanner must not publish levels for it as a directional idea.

The filing texts below are SHORT SYNTHETIC EXCERPTS written in the standard
language of merger agreements and merger proxies. They are not copies of real
filings; the EDGAR submissions payloads are built in the same shape the SEC API
returns. Nothing here touches the network.
"""
from __future__ import annotations

from datetime import date

import pytest

from _paths import ensure_paths

ensure_paths()

from engine import corporate_events as ce  # noqa: E402
from engine import thresholds as T  # noqa: E402
from engine.blockers import holder_rule  # noqa: E402
from engine.types import FinalAction  # noqa: E402
from portfolio.sources import EdgarClient, SecContactMissing  # noqa: E402

TODAY = date(2026, 10, 1)

MERGER_8K = """
<html><body><p>Item 1.01 Entry into a Material Definitive Agreement.</p>
<p>On August 3, 2026, Example Diagnostics, Inc. (the "Company") entered into an Agreement and
Plan of Merger (the "Merger Agreement") with Parent Holdings Ltd. ("Parent") and Merger Sub, Inc.,
a wholly owned subsidiary of Parent ("Merger Sub"). Merger Sub will merge with and into the Company,
with the Company surviving as a wholly owned subsidiary of Parent (the "Merger").</p>
<p>At the effective time, each share of Company common stock will be converted into the right to
receive (i) $102.50 in cash, without interest, plus (ii) one non-transferable contingent value right
("CVR") representing the right to receive up to $12.00 in cash upon achievement of milestones.</p>
</body></html>
"""

CREDIT_8K = """
<html><body><p>Item 1.01 Entry into a Material Definitive Agreement.</p>
<p>On July 9, 2026, Example Diagnostics, Inc. (the "Company") entered into a Credit Agreement with
a syndicate of lenders providing for a $500 million revolving credit facility.</p></body></html>
"""

TERMINATION_8K = """
<html><body><p>Item 1.02 Termination of a Material Definitive Agreement.</p>
<p>On September 20, 2026, the Company and Parent mutually agreed to terminate the Merger Agreement.</p>
</body></html>
"""

ACQUIRER_8K = """
<html><body><p>Item 1.01. On August 3, 2026, Acquirer Corp. (the "Company") entered into an Agreement
and Plan of Merger with Target Inc. ("Target") and a wholly owned subsidiary of the Company
("Merger Sub"). Merger Sub will merge with and into Target, with Target surviving as a wholly owned
subsidiary of the Company. Each Target share will be converted into the right to receive $40.00 in
cash.</p></body></html>
"""


def submissions(*rows, name="Example Diagnostics, Inc."):
    """An EDGAR submissions payload (registrant ``name``) with (form, filed, items, doc) rows."""
    rows = list(rows)
    return {"name": name, "filings": {"recent": {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "items": [r[2] for r in rows],
        "accessionNumber": [f"0001-26-{i:06d}" for i, _ in enumerate(rows)],
        "primaryDocument": [r[3] for r in rows],
        "acceptanceDateTime": [r[1] + "T16:00:00.000Z" for r in rows],
    }}}


class FakeSec:
    """Stands in for the SEC JSON API and the archive (documents by name)."""

    def __init__(self, subs, docs=None, cik=1521036, fail=None):
        self.subs, self.docs, self.cik, self.fail = subs, docs or {}, cik, fail
        self.calls = []

    def get_json(self, url):
        self.calls.append(url)
        if self.fail:
            raise self.fail
        if url.endswith("company_tickers.json"):
            return {"0": {"ticker": "EXDX", "cik_str": self.cik}}
        return self.subs

    def get_text(self, url, max_bytes):
        self.calls.append(url)
        for name, text in self.docs.items():
            if url.endswith(name):
                return text
        raise FileNotFoundError(url)


def check(fake, price=None, **kw):
    edgar = EdgarClient(get_json=fake.get_json)
    return ce.check("EXDX", edgar, TODAY, price=price, fetch_text=fake.get_text, **kw)


# ── detection ────────────────────────────────────────────────────────────────

def test_merger_proxy_flags_pending_acquisition_with_terms_from_the_filing():
    fake = FakeSec(submissions(("DEFM14A", "2026-09-10", "", "defm14a.htm"),
                               ("8-K", "2026-08-04", "1.01,9.01", "merger8k.htm")),
                   docs={"defm14a.htm": MERGER_8K, "merger8k.htm": MERGER_8K})
    ev = check(fake, price=99.65)
    assert ev.status == "flagged" and ev.code == "PENDING_ACQUISITION"
    assert ev.deal_price == pytest.approx(102.50)
    assert ev.agreement_date == "2026-08-03"
    # The reason is built from the data, and says what the deal does to the price.
    assert "Pending cash acquisition at $102.50" in ev.text
    assert "merger agreement 2026-08-03" in ev.text and "DEFM14A filed 2026-09-10" in ev.text
    assert "anchored to the deal terms" in ev.text
    assert "non-transferable" in ev.text            # the filing says so
    assert "cannot be reached" not in ev.text
    assert ev.spread_pct == pytest.approx((102.50 - 99.65) / 99.65, rel=1e-6)


def test_merger_8k_alone_flags_when_the_company_is_the_target():
    fake = FakeSec(submissions(("8-K", "2026-08-04", "1.01,9.01", "merger8k.htm")),
                   docs={"merger8k.htm": MERGER_8K})
    ev = check(fake, price=99.65)
    assert ev.status == "flagged" and ev.code == "PENDING_ACQUISITION"
    assert ev.source.startswith("SEC EDGAR 8-K")


def test_credit_agreement_8k_is_not_a_deal():
    fake = FakeSec(submissions(("8-K", "2026-07-10", "1.01", "credit8k.htm")), docs={"credit8k.htm": CREDIT_8K})
    assert check(fake).status == "clear"


def test_the_acquirer_side_of_a_merger_is_not_flagged():
    fake = FakeSec(submissions(("8-K", "2026-08-04", "1.01", "acq8k.htm")), docs={"acq8k.htm": ACQUIRER_8K})
    assert check(fake).status == "clear"


def test_a_later_termination_of_the_merger_agreement_clears_the_flag():
    fake = FakeSec(submissions(("8-K", "2026-09-21", "1.02", "term8k.htm"),
                               ("8-K", "2026-08-04", "1.01", "merger8k.htm")),
                   docs={"term8k.htm": TERMINATION_8K, "merger8k.htm": MERGER_8K})
    ev = check(fake)
    assert ev.status == "clear"
    assert "terminated" in ev.note


def form25(security_class):
    """A Form 25 (notification of removal from listing), raw XML as EDGAR serves it."""
    return ("<?xml version=\"1.0\"?><notificationOfRemoval><schemaVersion>X0203</schemaVersion>"
            "<exchange><entityName>Example Exchange LLC</entityName></exchange>"
            "<issuer><entityName>Example Diagnostics, Inc.</entityName></issuer>"
            f"<descriptionClassSecurity>{security_class}</descriptionClassSecurity>"
            "<ruleProvision>17 CFR 240.12d2-2(a)(2)</ruleProvision></notificationOfRemoval>")


FORM_15_12B = """<html><body><p>FORM 15</p><p>CERTIFICATION AND NOTICE OF TERMINATION OF REGISTRATION UNDER
SECTION 12(b) OF THE SECURITIES EXCHANGE ACT OF 1934</p><p>Example Diagnostics, Inc.</p>
<p>{cls}</p><p>(Title of each class of securities covered by this Form)</p>
<p>None</p><p>(Titles of all other classes of securities for which a duty to file reports remains)</p>
</body></html>"""

MINI_TENDER_14D9 = """<html><body><p>SCHEDULE 14D-9</p><p>Example Diagnostics, Inc. (the "Company") recommends
that shareholders reject the unsolicited mini-tender offer by Example Capital Corp. to purchase up to
1,000,000 shares, less than 5% of the outstanding shares, at a price below the market price.</p>
</body></html>"""


def test_delisting_form_avoids():
    # EDGAR lists the styled view (xslF25X02/...); the class is read from the raw XML.
    fake = FakeSec(submissions(("25-NSE", "2026-09-28", "", "xslF25X02/primary_doc.xml")),
                   docs={"primary_doc.xml": form25("Common Stock, $0.01 par value per share")})
    ev = check(fake)
    assert ev.status == "flagged" and ev.code == "DELISTING"
    assert "25-NSE filed 2026-09-28" in ev.text
    assert "Common Stock" in ev.text, "the reason names the class being delisted"


def test_delisting_of_a_bond_is_not_a_delisting_of_the_stock():
    """A Form 25 also removes matured notes (seen live: a large issuer's 0.000% notes
    due 2025). That says nothing about the common stock."""
    fake = FakeSec(submissions(("25-NSE", "2025-11-14", "", "xslF25X02/primary_doc.xml")),
                   docs={"primary_doc.xml": form25("0.000% Notes due 2025")})
    ev = check(fake)
    assert ev.status == "clear", ev.text


@pytest.mark.parametrize("cls,expect", [
    ("Common Stock, par value $0.001 per share", "flagged"),
    ("Ordinary Shares, nominal value $0.01", "flagged"),
    ("Common Shares of Beneficial Interest", "flagged"),
    ("American Depositary Shares, each representing one ordinary share", "flagged"),
    ("1.375% Senior Notes due 2029", "clear"),
    ("Units, each consisting of one share of Class A common stock and one-half of one warrant", "clear"),
    ("Warrants to purchase Class A common stock", "clear"),
    ("Depositary Shares, each representing 1/1000th of a share of 5.75% Series A Preferred Stock", "clear"),
])
def test_the_delisted_class_decides(cls, expect):
    fake = FakeSec(submissions(("15-12B", "2026-09-28", "", "form15.htm")),
                   docs={"form15.htm": FORM_15_12B.format(cls=cls)})
    assert check(fake).status == expect


def test_a_delisting_whose_class_cannot_be_read_is_unavailable_not_clear():
    unreadable = FakeSec(submissions(("25-NSE", "2026-09-28", "", "xslF25X02/primary_doc.xml")))
    assert check(unreadable).status == "unavailable"
    unstated = FakeSec(submissions(("25-NSE", "2026-09-28", "", "xslF25X02/primary_doc.xml")),
                       docs={"primary_doc.xml": form25("")})
    ev = check(unstated)
    assert ev.status == "unavailable" and not ev.fundable


def test_a_mini_tender_response_is_not_a_pending_acquisition():
    """Companies file a 14D-9 to tell holders to reject sub-5% mini-tender offers."""
    fake = FakeSec(submissions(("SC 14D9", "2026-09-15", "", "sc14d9.htm")), docs={"sc14d9.htm": MINI_TENDER_14D9})
    assert check(fake).status == "clear"


def test_deal_price_that_cannot_be_parsed_is_said_not_invented():
    fake = FakeSec(submissions(("DEFM14A", "2026-09-10", "", "defm14a.htm")),
                   docs={"defm14a.htm": "<p>Special meeting to approve the Merger Agreement.</p>"})
    ev = check(fake, price=50.0)
    assert ev.code == "PENDING_ACQUISITION" and ev.deal_price is None
    assert "deal price not stated in the filing text read" in ev.text
    assert ev.spread_pct is None


def test_collapsed_range_without_a_filing_is_a_caution_not_an_avoid():
    fake = FakeSec(submissions())
    ev = check(fake, atr_pct_20=0.004, atr_pct_120=0.020)
    assert ev.status == "flagged" and ev.code == "POSSIBLE_PENDING_DEAL"
    assert not ev.blocks_new_money_outright


# ── who is being bought: the filer's role, not the wording (seen live) ───────
# Each excerpt below is synthetic and follows the shape of a live false positive.

BUYER_PARENT_8K = """<p>Item 1.01. On July 6, 2026, Acquirer Corp., a Delaware corporation (\u201cParent\u201d),
Clark Merger Sub, Inc., a Delaware corporation and a wholly owned subsidiary of Parent
(\u201cMerger Sub\u201d), and Target Therapeutics, Inc., a Delaware corporation (the \u201cCompany\u201d), entered
into an Agreement and Plan of Merger (the \u201cMerger Agreement\u201d). Merger Sub will be merged with and
into the Company, with the Company surviving the Merger as a wholly owned subsidiary of Parent. Each
Share will be converted into the right to receive $85.00 per share in cash.</p>"""

BUYER_ALIAS_8K = """<p>On June 14, 2026, Acquirer Corporation, a Delaware corporation (\u201cACQR\u201d), Falcon Merger
Sub 1, Inc., a Delaware corporation and wholly owned subsidiary of ACQR (\u201cMerger Sub 1\u201d), and
Target Media, Inc., a Delaware corporation (\u201cTarget\u201d or the \u201cCompany\u201d), entered into an Agreement
and Plan of Merger, pursuant to which Merger Sub 1 will merge with and into the Company, with the
Company continuing as the surviving corporation and becoming a wholly owned subsidiary of ACQR.</p>"""

BANK_BUYER_8K = """<p>On July 21, 2026, Acquirer Bancorp, an Ohio corporation (the \u201cCompany\u201d), entered into an
Agreement and Plan of Merger (the \u201cMerger Agreement\u201d) with Seller Bancorp, an Indiana corporation
(\u201cSeller\u201d). Seller would merge with and into the Company (the \u201cMerger\u201d), with the Company
continuing as the surviving corporation. Each share of Seller common stock will be converted into the
right to receive 1.2 shares of Company common stock.</p>"""

BUYER_JOINT_PROXY = """<p>JOINT PROXY STATEMENT/PROSPECTUS. Acquirer Energy Corporation (\u201cAcquirer\u201d) and Target
Oil Company have entered into an Agreement and Plan of Merger. Cubs Merger Sub, Inc. Merger Sub, a direct
wholly owned subsidiary of Acquirer, is a Delaware corporation formed for the purpose of effecting the
merger. Under the merger agreement, Merger Sub will merge with and into Target Oil Company.</p>"""

PARENT_REORG_PROXY = """<p>PROXY STATEMENT. The Unification is the proposed reorganization of the group to remove its
dual listed company structure. In the Unification, Acquirer plc will become a wholly-owned subsidiary
of Acquirer Corporation pursuant to the Scheme of Arrangement.</p>"""

STOCK_DEAL_PROXY = """<p>PROXY STATEMENT. On May 15, 2026, the Company entered into an Agreement and Plan of Merger with
Parent and Merger Sub Corp., a direct wholly owned subsidiary of Parent. Merger Sub Corp. will merge
with and into the Company, with the Company surviving as a wholly owned subsidiary of Parent. Each share
of Company common stock will be converted into the right to receive 0.8138 shares of Parent common
stock and $0.41 per share in cash.</p>"""

SUCCESSOR_8K12B = """<p>FORM 8-K12B. Effective September 10, 2026, the Company completed a holding company
reorganization. The new holding company is the successor issuer pursuant to Rule 12g-3(a), and its
common stock trades under the same ticker symbol.</p>"""


BUYER_CHAIN_PROXY = """<p>PROXY STATEMENT. Cartwheel Group Inc., a Nevada corporation (\u201cCartwheel\u201d) entered into
an Agreement and Plan of Merger by and among Cartwheel, Omaha Intermediate 2, Inc., a Delaware corporation and
wholly -owned subsidiary of Cartwheel (\u201cIntermediate Parent\u201d), Omaha Intermediate, Inc., a Delaware
corporation and wholly -owned subsidiary of Intermediate Parent (\u201cIntermediate\u201d), Omaha Merger Subsidiary,
Inc., a Delaware corporation and wholly -owned subsidiary of Intermediate (\u201cMerger Sub\u201d), and Private
Motors, Inc. Merger Sub will merge with and into Private Motors, and Private Motors will become a direct,
wholly -owned subsidiary of Intermediate and an indirect, wholly -owned subsidiary of Cartwheel.</p>"""


@pytest.mark.parametrize("form,doc", [
    ("8-K", BUYER_PARENT_8K),          # "the Company" is the target; the filer is Parent
    ("8-K", BUYER_ALIAS_8K),           # the filer defined by a short alias; its merger sub buys
    ("8-K", BANK_BUYER_8K),            # the seller merges INTO the filer, which survives as itself
    ("DEFM14A", BUYER_JOINT_PROXY),    # the buyer's proxy: Merger Sub is the filer's subsidiary
    ("DEFM14A", PARENT_REORG_PROXY),   # the filer becomes the parent in its own reorganization
])
def test_the_buyer_side_is_never_flagged(form, doc):
    items = "1.01" if form == "8-K" else ""
    fake = FakeSec(submissions((form, "2026-07-07", items, "doc.htm"), name="ACQUIRER CORP /DE/"),
                   docs={"doc.htm": doc})
    assert check(fake).status == "clear", doc[:80]


# Target-side proxies (long documents that name the buyer many times) seen live.
TARGET_PROXY_COUNTERPARTY_DEFINED = """<p>PROXY STATEMENT. Target Brands Inc. (\u201cTarget\u201d) and Buyer-Co Corp.
(\u201cBuyer-Co\u201d) have entered into a merger agreement providing for the acquisition of Target by Buyer-Co
pursuant to a merger between a wholly owned subsidiary of Buyer-Co and Target, with Target surviving as a
direct wholly owned subsidiary of Buyer-Co. Each share of Target common stock will be converted into the
right to receive $21.00 per share in cash. Target may not repurchase shares (other than acquisitions of
securities of any wholly owned subsidiary of Target by Target).</p>"""

TARGET_PROXY_ANY_SUBSIDIARY = """<p>PROXY STATEMENT. The merger agreement is by and among Buyer Energy, Merger Sub
Corp., a direct wholly owned subsidiary of Buyer Energy, and Target Energy, Inc. Merger Sub Corp. will merge
with and into Target Energy, with Target Energy surviving as a wholly owned subsidiary of Buyer Energy. All
shares (other than shares owned by Buyer Energy, Merger Sub Corp, any other wholly owned subsidiary of Buyer
Energy, Target Energy or any wholly owned subsidiary of Target Energy) will be converted into the right to
receive $40.00 per share in cash.</p>"""


@pytest.mark.parametrize("doc,name", [
    (TARGET_PROXY_COUNTERPARTY_DEFINED, "Target Brands Inc."),
    (TARGET_PROXY_ANY_SUBSIDIARY, "TARGET ENERGY, INC"),
])
def test_a_target_proxy_that_names_the_buyer_throughout_is_flagged(doc, name):
    fake = FakeSec(submissions(("DEFM14A", "2026-07-28", "", "proxy.htm"), name=name), docs={"proxy.htm": doc})
    assert check(fake).code == "PENDING_ACQUISITION"


TARGET_PROXY_WITH_OWN_REORG = """<p>PROXY STATEMENT. Before the merger, Target Media completed a holding company
merger in which Holdco Merger Sub, a Delaware corporation and wholly owned subsidiary of Target Media, merged
with and into Target Media, with Target Media surviving as a wholly owned subsidiary of New Target Media.
Under the merger agreement with Buyer Corp (\u201cBUYR\u201d), Prince Sub Inc., a Delaware corporation and
wholly owned subsidiary of BUYR (\u201cMerger Sub\u201d), will merge with and into Target Media, with Target Media
surviving as a wholly owned subsidiary of BUYR. Each share will be converted into the right to receive
$31.00 in cash.</p>"""

SWAP_8K = """<p>Item 1.01 and Item 1.02. On February 27, 2026, Target Media, Inc. (the \u201cCompany\u201d) terminated
its Agreement and Plan of Merger with First Bidder Inc. and entered into an Agreement and Plan of Merger with
Second Bidder Corp. (\u201cParent\u201d) and Merger Sub, a wholly owned subsidiary of Parent. Merger Sub will merge
with and into the Company, with the Company surviving as a wholly owned subsidiary of Parent.</p>"""


def test_a_target_proxy_that_also_describes_its_own_reorganization_is_flagged():
    fake = FakeSec(submissions(("DEFM14A", "2026-03-26", "", "proxy.htm"), name="Target Media, Inc."),
                   docs={"proxy.htm": TARGET_PROXY_WITH_OWN_REORG})
    ev = check(fake, price=29.0)
    assert ev.code == "PENDING_ACQUISITION" and ev.deal_price == pytest.approx(31.0)


def test_terminating_one_deal_while_signing_another_does_not_clear():
    fake = FakeSec(submissions(("8-K", "2026-02-27", "1.01,1.02", "swap.htm"), name="Target Media, Inc."),
                   docs={"swap.htm": SWAP_8K})
    assert check(fake).code == "PENDING_ACQUISITION"


def test_a_buyer_that_owns_the_merger_sub_through_a_chain_is_not_flagged():
    fake = FakeSec(submissions(("DEFM14A", "2025-10-08", "", "proxy.htm"), name="Cartwheel Group Inc."),
                   docs={"proxy.htm": BUYER_CHAIN_PROXY})
    assert check(fake).status == "clear"


def test_the_target_side_of_the_same_language_is_flagged():
    fake = FakeSec(submissions(("8-K", "2026-07-07", "1.01", "doc.htm"), name="Target Therapeutics, Inc."),
                   docs={"doc.htm": BUYER_PARENT_8K})
    ev = check(fake, price=80.0)
    assert ev.code == "PENDING_ACQUISITION" and ev.deal_price == pytest.approx(85.0)


def test_a_stock_deal_is_flagged_without_a_cash_price():
    fake = FakeSec(submissions(("DEFM14A", "2026-07-28", "", "proxy.htm")), docs={"proxy.htm": STOCK_DEAL_PROXY})
    ev = check(fake, price=60.0)
    assert ev.code == "PENDING_ACQUISITION"
    assert ev.deal_price is None and ev.spread_pct is None, "no single cash price for a stock deal"
    assert "acquirer's stock" in ev.text
    assert "Pending cash acquisition" not in ev.text


def test_a_successor_issuer_reorganization_is_not_a_delisting():
    fake = FakeSec(submissions(("25-NSE", "2026-09-18", "", "xslF25X02/primary_doc.xml"),
                               ("8-K12B", "2026-09-10", "3.03,5.03", "succ.htm")),
                   docs={"primary_doc.xml": form25("Common Stock"), "succ.htm": SUCCESSOR_8K12B})
    ev = check(fake)
    assert ev.status == "clear", ev.text


# ── fail closed ─────────────────────────────────────────────────────────────

def test_no_sec_contact_means_unavailable(monkeypatch):
    monkeypatch.delenv("QUANTA_SEC_CONTACT", raising=False)
    monkeypatch.delenv("QUANTA_SEC_USER_AGENT", raising=False)
    ev = ce.check("EXDX", ce.default_edgar(), TODAY)
    assert ev.status == "unavailable" and ev.code == "EVENT_CHECK_UNAVAILABLE"
    assert "QUANTA_SEC_CONTACT" in ev.text


def test_provider_error_means_unavailable():
    ev = check(FakeSec(None, fail=TimeoutError("read timed out")))
    assert ev.status == "unavailable" and "check unavailable" in ev.text.lower()


@pytest.mark.parametrize("symbol,exchange", [("RELIANCE.NS", "NSE"), ("500325.BO", "BSE")])
def test_india_listings_fail_closed_and_say_why(symbol, exchange):
    """Owner decision: no corporate-event source covers NSE/BSE, so India names are
    never funded, and the row says exactly that. No SEC request is spent on them."""
    fake = FakeSec(submissions())
    ev = ce.check(symbol, EdgarClient(get_json=fake.get_json), TODAY, fetch_text=fake.get_text)
    assert ev.status == "unavailable" and not ev.fundable
    assert ev.code == "EVENT_CHECK_NOT_COVERED"
    from engine.blockers import BLOCKERS
    assert "QUANTA_SEC_CONTACT" not in BLOCKERS[ev.code][2], "no SEC remedy for a listing SEC does not cover"
    assert f"no corporate-event source covers {exchange} listings" in ev.text
    assert "SEC EDGAR covers US registrants only" in ev.text
    assert fake.calls == []


def test_unknown_registrant_is_unavailable_not_clear():
    fake = FakeSec(submissions())
    edgar = EdgarClient(get_json=lambda url: {} if url.endswith("company_tickers.json") else fake.subs)
    ev = ce.check("NOPE", edgar, TODAY, fetch_text=fake.get_text)
    assert ev.status == "unavailable"


def test_sec_contact_is_required_for_event_queries(monkeypatch):
    from portfolio import sources
    monkeypatch.delenv("QUANTA_SEC_CONTACT", raising=False)
    monkeypatch.delenv("QUANTA_SEC_USER_AGENT", raising=False)
    with pytest.raises(SecContactMissing):
        sources._agent(require_contact=True)
    monkeypatch.setenv("QUANTA_SEC_CONTACT", "research@example.com")
    assert "research@example.com" in sources._agent(require_contact=True)


# ── holders ─────────────────────────────────────────────────────────────────

def _flagged(price, deal):
    fake = FakeSec(submissions(("8-K", "2026-08-04", "1.01", "merger8k.htm")),
                   docs={"merger8k.htm": MERGER_8K.replace("$102.50", f"${deal:.2f}")})
    return check(fake, price=price)


def test_holder_sells_when_the_remaining_spread_is_below_the_minimum():
    ev = _flagged(price=101.50, deal=102.50)                 # 0.99% left
    assert ev.spread_pct < T.EVENTS.ARB_SPREAD_MIN
    rule = holder_rule(FinalAction.AVOID, 65.0, ["PENDING_ACQUISITION"], event=ev.to_dict())
    assert rule["action"] == "EXIT" and rule["basis"] == "PENDING_ACQUISITION_SPREAD"


def test_holder_is_not_told_to_sell_when_the_spread_is_unknown_or_wide():
    wide = _flagged(price=90.0, deal=102.50)
    assert holder_rule(FinalAction.AVOID, 65.0, ["PENDING_ACQUISITION"], event=wide.to_dict())["action"] != "EXIT"
    unknown = dict(wide.to_dict(), deal_price=None, spread_pct=None)
    assert holder_rule(FinalAction.AVOID, 65.0, ["PENDING_ACQUISITION"], event=unknown)["action"] != "EXIT"


def test_buyers_merger_proxy_is_not_the_targets_deal():
    fake = FakeSec(submissions(("DEFM14A", "2026-09-10", "", "defm14a.htm")), docs={"defm14a.htm": ACQUIRER_8K})
    assert check(fake).status == "clear"


# ── stale structural targets (audit 5.1) ─────────────────────────────────────

def _targets(high_age_sessions):
    import SP_ELITE_PRO as sp
    ind = sp.TechnicalIndicators()
    ind.current_price, ind.atr = 99.65, 0.59
    ind.atr_percent = ind.atr / ind.current_price
    ind.resistance = 99.65                     # nothing overhead nearby
    ind.high_52w, ind.high_52w_age_sessions = 111.86, high_age_sessions
    ind.support = 80.0
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng.market_regime = sp.MarketRegime(regime_type="BULL")
    return sp.QuantaEliteTradingSystem._calculate_price_targets(
        eng, 99.65, ind, sp.RiskMetrics(volatility=0.30), analyst_target=107.74)


def test_a_52_week_high_older_than_the_horizon_is_not_a_target():
    limit = T.LEVELS.TARGET_STRUCT_MAX_AGE_SESSIONS
    _, fresh_target, _ = _targets(high_age_sessions=limit)
    _, stale_target, _ = _targets(high_age_sessions=limit + 3)       # LNTH: ~66 sessions old
    assert fresh_target == pytest.approx((111.86 + 107.74) / 2, abs=0.01)
    # With no structure overhead the engine's own rule averages the consensus
    # with the ATR measured move (it does not fall back to consensus alone).
    measured_move = 99.65 + 0.59 * T.LEVELS.TARGET_ATR_MULTIPLIER
    assert stale_target == pytest.approx((107.74 + measured_move) / 2, abs=0.01)
    assert stale_target < fresh_target


# ── the gate inside the book (get_top_recommendations) ───────────────────────

def _book(monkeypatch, checks):
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    sysm._event_checker = lambda symbols, **_: {s: checks.get(s, ce.EventCheck("clear")) for s in symbols}
    sysm.scan_results = [_pick("LNTH", "Health", 8.5, seed=1), _pick("HST", "RealEstate", 8.0, seed=2),
                         _pick("GOOGL", "Tech", 7.8, seed=3)]
    return sysm, sysm.get_top_recommendations(max_positions=5)


def test_pending_acquisition_avoid(monkeypatch):
    """F1 / audit 9.2: an 8-K 1.01 / DEFM14A gives AVOID with PENDING_ACQUISITION, levels hidden."""
    deal = _flagged(price=99.65, deal=102.50)
    sysm, out = _book(monkeypatch, {"LNTH": deal})
    assert "LNTH" not in {r.ticker for r in out}, "an AVOID name is never a published pick"
    rej = {r["ticker"]: r for r in sysm._event_rejections}
    assert rej["LNTH"]["final_action"] == "AVOID" and rej["LNTH"]["code"] == "PENDING_ACQUISITION"
    assert rej["LNTH"]["reason"].startswith("Pending cash acquisition at $102.50")
    assert not {"entry_price", "stop_loss", "price_target"} & set(rej["LNTH"]), "no levels for an AVOID"
    assert sysm._gate_stats["event_avoid"] == 1


def test_unavailable_check_is_published_but_never_funded(monkeypatch):
    sysm, out = _book(monkeypatch, {"HST": ce._unavailable("SEC EDGAR did not answer")})
    hst = next(r for r in out if r.ticker == "HST")
    assert hst.allocation == 0 and hst.final_action == "WATCHLIST"
    assert (hst.decision or {}).get("action") == "WATCHLIST"
    assert any("Corporate-event check unavailable" in x for x in hst.final_action_reasons)
    assert hst.event_check["status"] == "unavailable"
    assert any(r.allocation > 0 for r in out if r.ticker != "HST"), "other names are unaffected"


def test_a_scanned_name_that_never_becomes_a_candidate_still_shows_avoid(monkeypatch):
    """Any scanned name with a pending acquisition is published as AVOID with its
    filing's reason, even when an earlier gate (signal, R:R) already dropped it:
    the reader must learn why it is not a pick, not just that it is absent."""
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    deal = _flagged(price=99.65, deal=102.50)
    seen = []

    def checker(symbols, **_):
        seen.extend(symbols)
        return {s: (deal if s == "TGTX" else ce.EventCheck("clear")) for s in symbols}

    sysm._event_checker = checker
    sysm.scan_results = [_pick("TGTX", "Health", 6.0, signal="HOLD", seed=1),
                         _pick("HST", "RealEstate", 8.0, seed=2), _pick("GOOGL", "Tech", 7.8, seed=3)]
    out = sysm.get_top_recommendations(max_positions=5)
    assert "TGTX" not in {r.ticker for r in out}
    rej = {r["ticker"]: r for r in sysm._event_rejections}
    assert rej["TGTX"]["final_action"] == "AVOID" and rej["TGTX"]["code"] == "PENDING_ACQUISITION"
    assert rej["TGTX"]["reason"].startswith("Pending cash acquisition at $102.50")
    assert "TGTX" in seen, "every scanned name is checked, not only candidates"
    assert sysm._gate_stats["event_avoid"] == 1


def test_a_universe_name_dropped_before_analysis_finished_still_shows_avoid(monkeypatch):
    """Names an early stage drops (score, committee) never become analysis rows.
    The gate checks the scanned universe itself, and names the company from its
    SEC registrant record."""
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    deal = _flagged(price=99.65, deal=102.50)
    deal.registrant = "Example Diagnostics, Inc."
    sysm._event_checker = lambda symbols, **_: {s: (deal if s == "EXDX" else ce.EventCheck("clear")) for s in symbols}
    sysm._scanned_universe = ["EXDX", "HST", "GOOGL"]
    sysm.scan_results = [_pick("HST", "RealEstate", 8.0, seed=2), _pick("GOOGL", "Tech", 7.8, seed=3)]
    sysm.get_top_recommendations(max_positions=5)
    rej = {r["ticker"]: r for r in sysm._event_rejections}
    assert rej["EXDX"]["final_action"] == "AVOID" and rej["EXDX"]["code"] == "PENDING_ACQUISITION"
    assert rej["EXDX"]["company"] == "Example Diagnostics, Inc."
    assert rej["EXDX"]["reason"].startswith("Pending cash acquisition at $102.50")


def test_a_scan_with_no_analysed_rows_still_publishes_universe_events(monkeypatch):
    """A03: when every name fails before becoming a row, the event check still
    runs over the scanned universe and publishes the flagged name."""
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    deal = _flagged(price=99.65, deal=102.50)
    seen = []

    def checker(symbols, **_):
        seen.extend(symbols)
        return {s: (deal if s == "EXDX" else ce.EventCheck("clear")) for s in symbols}

    sysm._event_checker = checker
    sysm._scanned_universe = ["EXDX", "HST"]
    sysm.scan_results = []
    assert sysm.get_top_recommendations(max_positions=5) == []
    assert set(seen) == {"EXDX", "HST"}, "the whole scanned universe is checked"
    rej = {r["ticker"]: r for r in sysm._event_rejections}
    assert rej["EXDX"]["code"] == "PENDING_ACQUISITION"
    assert sysm._gate_stats["event_avoid"] == 1


def test_the_gate_does_not_wait_forever_for_the_warmup(monkeypatch):
    """A03: the warm-up join is bounded by EVENTS.WARMUP_JOIN_SECONDS; the gate
    then checks the names itself."""
    import threading
    import time
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    _install_fake_deep(monkeypatch, {})
    monkeypatch.setattr(T.EVENTS, "WARMUP_JOIN_SECONDS", 0.05)
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, daemon=True)
    stuck.start()
    sysm = _bare_system()
    sysm._event_warm = stuck
    sysm._event_checker = lambda symbols, **_: {s: ce.EventCheck("clear") for s in symbols}
    sysm.scan_results = [_pick("HST", "RealEstate", 8.0, seed=2)]
    t0 = time.perf_counter()
    try:
        sysm.get_top_recommendations(max_positions=5)
    finally:
        release.set()
    assert time.perf_counter() - t0 < 5.0
    assert sysm._gate_stats["gate1b_event_clear"] == 1


def test_the_event_check_names_the_registrant():
    fake = FakeSec(submissions(("8-K", "2026-08-04", "1.01", "merger8k.htm")), docs={"merger8k.htm": MERGER_8K})
    assert check(fake).registrant == "Example Diagnostics, Inc."


# ── audit follow-up: fail-open paths (E1, E2), proxies without takeover wording (E4), cache bound (E3) ──

CREDIT_TERMINATION_8K = """
<html><body><p>Item 1.02 Termination of a Material Definitive Agreement.</p>
<p>On September 20, 2026, in connection with the pending merger contemplated by the Merger Agreement, the
Company repaid all amounts outstanding under, and terminated, its Credit Agreement with its lenders.</p>
<p>In connection with the Merger Agreement, the Credit Agreement was terminated.</p></body></html>
"""

CONDITIONAL_TERMINATION_8K = """
<html><body><p>Item 1.02 Termination of a Material Definitive Agreement.</p>
<p>The Company terminated its rights plan. Parent may terminate the Merger Agreement if the merger has not
closed by the outside date; in the event of a termination of the Merger Agreement, a fee is payable.</p>
</body></html>
"""


@pytest.mark.parametrize("doc", [CREDIT_TERMINATION_8K, CONDITIONAL_TERMINATION_8K])
def test_terminating_another_agreement_does_not_clear_a_pending_deal(doc):
    """E1: an 8-K item 1.02 that mentions the merger agreement but terminates
    something else (or only describes a right to terminate) keeps the deal pending."""
    fake = FakeSec(submissions(("8-K", "2026-09-21", "1.02", "term8k.htm"),
                               ("8-K", "2026-08-04", "1.01", "merger8k.htm")),
                   docs={"term8k.htm": doc, "merger8k.htm": MERGER_8K})
    ev = check(fake)
    assert ev.status == "flagged" and ev.code == "PENDING_ACQUISITION"


@pytest.mark.parametrize("text", [
    "the Company and Parent mutually agreed to terminate the Merger Agreement.",
    "the Company delivered notice of termination of the Agreement and Plan of Merger.",
    "the Merger Agreement (as amended), dated June 2, 2026, was terminated.",
])
def test_termination_wording_of_the_deal_itself_is_recognised(text):
    assert ce.terminates_merger(f"<p>{text}</p>")


def test_a_deal_signed_more_than_a_year_ago_is_still_pending():
    """E2: deals can stay pending past a year; the look-back covers that."""
    filed = date(TODAY.year - 1, TODAY.month, TODAY.day).replace(day=1)
    fake = FakeSec(submissions(("DEFM14A", (filed.replace(month=max(1, filed.month - 1))).isoformat(), "", "proxy.htm")),
                   docs={"proxy.htm": MERGER_8K})
    assert T.EVENTS.LOOKBACK_DAYS > 365
    assert check(fake).code == "PENDING_ACQUISITION"


def _paged(recent_rows, pages, name="Example Diagnostics, Inc."):
    subs = submissions(*recent_rows, name=name)
    subs["filings"]["files"] = [{"name": n, "filingFrom": f, "filingTo": t} for n, f, t, _ in pages]
    bodies = {n: submissions(*rows)["filings"]["recent"] for n, _, _, rows in pages}
    return subs, bodies


class PagedSec(FakeSec):
    def __init__(self, subs, bodies, docs=None, fail_pages=False):
        super().__init__(subs, docs)
        self.bodies, self.fail_pages = bodies, fail_pages

    def get_json(self, url):
        name = url.rsplit("/", 1)[-1]
        if name in self.bodies:
            self.calls.append(url)
            if self.fail_pages:
                raise TimeoutError("page")
            return self.bodies[name]
        return super().get_json(url)


def test_a_heavy_filers_older_page_is_read_and_its_deal_found():
    """E2: EDGAR's "recent" block can cover only months for a heavy filer; the
    deal filing on an older page is still found."""
    subs, bodies = _paged([("424B2", "2026-09-30", "", "n1.htm"), ("424B2", "2026-06-01", "", "n2.htm")],
                          [("CIK0001521036-submissions-001.json", "2023-01-01", "2026-05-31",
                            [("DEFM14A", "2026-02-10", "", "proxy.htm")])])
    fake = PagedSec(subs, bodies, docs={"proxy.htm": MERGER_8K})
    assert check(fake).code == "PENDING_ACQUISITION"


def test_a_filing_list_that_does_not_reach_back_is_unavailable_not_clear(monkeypatch):
    monkeypatch.setattr(T.EVENTS, "FILING_PAGES_MAX", 0)
    subs, bodies = _paged([("424B2", "2026-09-30", "", "n1.htm")],
                          [("CIK0001521036-submissions-001.json", "2023-01-01", "2026-09-29", [])])
    ev = check(PagedSec(subs, bodies))
    assert ev.status == "unavailable" and "reaches back only to 2026-09-30" in ev.text


def test_an_older_page_that_fails_to_load_is_unavailable():
    subs, bodies = _paged([("424B2", "2026-09-30", "", "n1.htm")],
                          [("CIK0001521036-submissions-001.json", "2023-01-01", "2026-09-29", [])])
    assert check(PagedSec(subs, bodies, fail_pages=True)).status == "unavailable"


def test_older_pages_that_end_before_the_window_are_not_read():
    subs, bodies = _paged([("424B2", "2026-09-30", "", "n1.htm")],
                          [("CIK0001521036-submissions-001.json", "2010-01-01", "2014-12-31", [])])
    fake = PagedSec(subs, bodies)
    assert check(fake).status == "clear"
    assert not any("submissions-001" in c for c in fake.calls)


ASSET_SALE_PROXY = """<html><body><p>DEFINITIVE PROXY STATEMENT</p><p>Example Diagnostics, Inc. (the "Company")
asks stockholders to approve the sale of its imaging division to Buyer Co. under an Asset Purchase Agreement.
The Company will continue as an independent public company after the sale.</p></body></html>"""


def test_a_merger_form_proxy_without_takeover_wording_is_unavailable_not_avoid():
    """E4: an asset-sale proxy filed as DEFM14A does not say the company is being acquired."""
    fake = FakeSec(submissions(("DEFM14A", "2026-09-10", "", "proxy.htm")), docs={"proxy.htm": ASSET_SALE_PROXY})
    ev = check(fake)
    assert ev.status == "unavailable" and "does not say the company is being acquired" in ev.text


def test_the_document_cache_is_bounded_and_drops_the_least_recently_used(monkeypatch):
    """E3: documents are kept within EVENTS.DOC_CACHE_MAX_BYTES."""
    monkeypatch.setattr(T.EVENTS, "DOC_CACHE_MAX_BYTES", 100)
    ce._DOC_CACHE.clear()
    ce._DOC_CACHE_BYTES[0] = 0
    for i in range(5):
        ce._cache_doc(f"u{i}", "x" * 40)
    assert ce._DOC_CACHE_BYTES[0] <= 100
    assert list(ce._DOC_CACHE) == ["u3", "u4"]
    ce._cache_doc("too-big", "x" * 101)
    assert "too-big" not in ce._DOC_CACHE
    ce._DOC_CACHE.clear()
    ce._DOC_CACHE_BYTES[0] = 0
