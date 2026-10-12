"""QUANTA Research Desk — professional Streamlit decision-support interface."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from streamlit_support import (
    CACHE_MAX_ENTRIES,
    RESOURCE_TTL_SECONDS,
    SCAN_CACHE_TTL_SECONDS,
    STOCK_CACHE_TTL_SECONDS,
    GuardedResource,
    UncacheableResult,
    cache_fingerprint,
    compute_market_report,
    compute_stock_report,
    esc,
    market_scan_csv,
    scan_cache_key,
    stock_cache_key,
)


st.set_page_config(
    page_title="QUANTA Research Desk",
    page_icon="Q",
    layout="wide",
    initial_sidebar_state="collapsed",
)


DESIGN_SYSTEM = """
<style>
:root {
    --ink: #111820;
    --navy: #102a43;
    --forest: #0e5a46;
    --paper: #f7f5f0;
    --card: #fffefa;
    --rule: #d9d5cc;
    --muted: #65717d;
    --gold: #a87922;
    --positive: #11735a;
    --negative: #a43a31;
}

.stApp {
    background:
        radial-gradient(circle at 78% -12%, rgba(168, 121, 34, .08), transparent 28rem),
        var(--paper);
    color: var(--ink);
}

html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
}

.block-container { max-width: 1380px; padding-top: 1.1rem; padding-bottom: 4rem; }

h1, h2, h3 {
    color: var(--ink);
    letter-spacing: -.025em;
}

h1, .editorial-title {
    font-family: "Iowan Old Style", Charter, Georgia, serif;
}

[data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none !important; }

.masthead {
    align-items: end;
    border-bottom: 3px double var(--ink);
    display: flex;
    justify-content: space-between;
    margin-bottom: 1.15rem;
    padding: .25rem 0 .9rem;
}

.brand-lockup { display: flex; align-items: center; gap: .8rem; }
.brand-mark {
    align-items: center; background: var(--ink); color: #fff; display: flex;
    font-family: Georgia, serif; font-size: 1.2rem; height: 2.6rem;
    justify-content: center; width: 2.6rem;
}
.brand-name { font-family: Georgia, serif; font-size: 1.65rem; font-weight: 700; line-height: 1; }
.brand-deck { color: var(--muted); font-size: .66rem; letter-spacing: .16em; margin-top: .35rem; text-transform: uppercase; }
.edition { color: var(--muted); font-size: .72rem; letter-spacing: .08em; text-align: right; text-transform: uppercase; }

.eyebrow {
    color: var(--gold); font-size: .68rem; font-weight: 750; letter-spacing: .16em;
    margin-bottom: .45rem; text-transform: uppercase;
}

.editorial-title { font-size: clamp(2.2rem, 4vw, 4.2rem); line-height: .98; margin: 0; max-width: 850px; }
.editorial-deck { color: #46525e; font-size: 1.05rem; line-height: 1.65; margin: 1rem 0 1.4rem; max-width: 850px; }

.parameter-strip, .meta-strip { display: flex; flex-wrap: wrap; gap: .5rem; margin: .7rem 0 1.4rem; }
.parameter-chip, .meta-chip {
    background: rgba(255,255,255,.56); border: 1px solid var(--rule); color: #3d4852;
    font-size: .72rem; letter-spacing: .025em; padding: .42rem .65rem;
}

.control-intro {
    color: var(--muted); font-size: .82rem; line-height: 1.55; margin: -.15rem 0 .8rem;
}

.st-key-market_controls {
    background: rgba(255,255,255,.62); border: 1px solid var(--rule);
    margin: .35rem 0 1.25rem; padding: 1rem 1.1rem .3rem;
}

.section-rule {
    border-top: 1px solid var(--ink); margin: 2.2rem 0 1rem; padding-top: .6rem;
    color: var(--ink); font-size: .7rem; font-weight: 750; letter-spacing: .15em; text-transform: uppercase;
}

.decision-banner {
    background: var(--navy); border-left: 6px solid var(--gold); color: white;
    margin: .5rem 0 1.2rem; padding: 1.35rem 1.45rem;
}
.decision-banner .kicker { color: #d9bd82; font-size: .66rem; font-weight: 750; letter-spacing: .16em; text-transform: uppercase; }
.decision-banner .headline { font-family: Georgia, serif; font-size: clamp(1.35rem, 2.5vw, 2.1rem); line-height: 1.18; margin: .45rem 0; }
.decision-banner .copy { color: #d8e2ea; font-size: .93rem; line-height: 1.55; max-width: 1000px; }

[data-testid="stMetric"] {
    background: rgba(255,255,255,.68); border: 1px solid var(--rule); min-height: 108px;
    padding: .9rem 1rem;
}
[data-testid="stMetricLabel"] { color: #65717d; font-size: .7rem; letter-spacing: .07em; text-transform: uppercase; }
[data-testid="stMetricValue"] { color: var(--ink); font-family: Georgia, serif; font-size: clamp(1.55rem, 2.2vw, 2.25rem); }
[data-testid="stMetricLabel"] p,
[data-testid="stMetricValue"] > div,
[data-testid="stMetricDelta"] > div {
    overflow: visible !important; text-overflow: clip !important; white-space: normal !important;
}
[data-testid="stMetricDelta"] { font-size: .72rem; }

.stButton > button, [data-testid="stFormSubmitButton"] > button {
    background: var(--ink); border: 1px solid var(--ink); border-radius: 0; color: white;
    font-weight: 700; min-height: 3rem; transition: background .15s ease, transform .15s ease;
    width: 100%;
}
.stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover {
    background: var(--forest); border-color: var(--forest); color: white; transform: translateY(-1px);
}
.stButton > button:focus-visible, [data-testid="stFormSubmitButton"] > button:focus-visible,
input:focus-visible, button:focus-visible { outline: 3px solid rgba(168,121,34,.45) !important; outline-offset: 2px; }

[data-baseweb="tab-list"] { border-bottom: 1px solid var(--rule); gap: 1.5rem; }
[data-baseweb="tab"] { color: #586570; font-weight: 650; padding-left: 0; padding-right: 0; }
[aria-selected="true"][data-baseweb="tab"] { color: var(--ink); }
[data-baseweb="tab-highlight"] { background-color: var(--gold); }

[data-testid="stDataFrame"] { border: 1px solid var(--rule); }
[data-testid="stExpander"] { background: rgba(255,255,255,.52); border: 1px solid var(--rule); border-radius: 0; }
[data-testid="stAlert"] { border-radius: 0; }
[data-testid="stForm"] { background: rgba(255,255,255,.5); border: 1px solid var(--rule); border-radius: 0; padding: 1.2rem; }

.footer-note { border-top: 3px double var(--ink); color: var(--muted); font-size: .74rem; line-height: 1.55; margin-top: 3rem; padding-top: 1rem; }

@media (max-width: 780px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; }
    .masthead { align-items: flex-start; gap: .8rem; }
    .edition { display: none; }
    .editorial-title { font-size: 2.35rem; }
    [data-testid="stMetric"] { min-height: 92px; }
}

@media print {
    @page { size: letter portrait; margin: .42in; }
    html, body, .stApp { background: white !important; }
    [data-testid="stSidebar"], [data-testid="collapsedControl"],
    [data-testid="stHeader"], [data-testid="stToolbar"],
    [data-testid="stStatusWidget"], .st-key-market_controls,
    [data-testid="stForm"], [data-testid="stDownloadButton"],
    [data-testid="stButton"] { display: none !important; }
    .block-container { max-width: none !important; padding: 0 !important; }
    .masthead { margin-bottom: .16in; }
    .editorial-title { font-size: 30pt !important; }
    .editorial-deck { font-size: 10pt !important; margin: .08in 0 .14in; }
    [data-testid="stHorizontalBlock"] {
        display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: .12in !important;
    }
    [data-testid="column"] {
        flex: none !important; min-width: 0 !important; width: auto !important;
    }
    [data-testid="stMetric"] {
        break-inside: avoid; min-height: 0 !important; padding: .09in .1in !important;
    }
    [data-testid="stMetricValue"] { font-size: 18pt !important; }
    [data-testid="stMetricLabel"], [data-testid="stMetricDelta"] { font-size: 7.5pt !important; }
    [data-testid="stDataFrame"], [data-testid="stTable"] {
        max-width: 100% !important; overflow: visible !important;
    }
    [data-testid="stExpander"] { break-inside: auto !important; overflow: visible !important; }
    [data-testid="stExpanderDetails"] { overflow: visible !important; }
    .decision-banner, .section-rule, .footer-note { break-inside: avoid; }
    .decision-banner { padding: .14in .16in; }
    .section-rule { margin-top: .22in; }
    .parameter-strip { display: none; }
}
</style>
"""

st.markdown(DESIGN_SYSTEM, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Caching. Engine objects are shared resources (each behind a lock); results
# are cached per normalized input key. Anything that is not a clean success
# raises inside the cached function, so Streamlit never stores it.
# ---------------------------------------------------------------------------

@st.cache_resource(ttl=RESOURCE_TTL_SECONDS, max_entries=8, show_spinner=False)
def _market_system(market_code: str, capital: float) -> GuardedResource:
    from SP_ELITE_PRO import QuantaEliteTradingSystem

    return GuardedResource(QuantaEliteTradingSystem(capital=capital, market=market_code))


@st.cache_resource(ttl=RESOURCE_TTL_SECONDS, max_entries=1, show_spinner=False)
def _deep_analyzer() -> GuardedResource:
    import TradingAnalysis_ELITE as deep_engine

    return GuardedResource(deep_engine.QuantaEliteAnalyzer())


@st.cache_data(ttl=SCAN_CACHE_TTL_SECONDS, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def _cached_market_report(
    market_code: str,
    capital: float,
    scan_code: str,
    max_positions: int,
    diversify: bool,
    scan_label: str,
    currency_symbol: str,
) -> dict:
    return compute_market_report(
        _market_system(market_code, capital),
        scan_code=scan_code,
        max_positions=max_positions,
        diversify=diversify,
        capital=capital,
        currency_symbol=currency_symbol,
        scan_label=scan_label,
    )


@st.cache_data(ttl=STOCK_CACHE_TTL_SECONDS, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def _cached_stock_report(ticker: str, capital: float) -> dict:
    return compute_stock_report(_deep_analyzer(), ticker, capital)


def clear_cached_results() -> None:
    _cached_market_report.clear()
    _cached_stock_report.clear()
    _market_system.clear()
    _deep_analyzer.clear()


def render_evidence_banner(banner: dict) -> None:
    """The one evidence statement per results view."""
    if banner.get("permits_recommendation"):
        st.info(banner["text"])
    else:
        st.warning(banner["text"])


def render_freshness(freshness: dict) -> None:
    if freshness.get("is_stale"):
        st.warning(" ".join([freshness["text"], *freshness.get("notes", [])]))
    else:
        st.caption(freshness.get("text", ""))


def render_bullets(items: list[str]) -> None:
    for item in items:
        st.markdown(f"- {item}")


def section_label(text: str) -> None:
    st.markdown(f'<div class="section-rule">{esc(text)}</div>', unsafe_allow_html=True)


def render_masthead() -> None:
    now = datetime.now().astimezone()
    st.markdown(
        f"""
        <div class="masthead">
          <div class="brand-lockup">
            <div class="brand-mark">Q</div>
            <div><div class="brand-name">QUANTA</div><div class="brand-deck">Research Desk · Decision Intelligence</div></div>
          </div>
          <div class="edition">{now.strftime('%A, %B %d, %Y')}<br>Independent model research</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_scan_detail(detail: dict, currency: str, rank: int, validated: bool) -> None:
    record = detail["record"]
    title = f"{rank:02d}  {record['Ticker']} — {record['Company']}"
    with st.expander(title, expanded=True):
        st.caption(
            f"{record['Action']} · {record['Sector']} · {record['Setup']} setup · "
            f"Risk grade {record['Risk grade']} · {record['Confidence']} heuristic confidence"
        )
        if detail.get("caveat"):
            st.caption(detail["caveat"])
        level_cols = st.columns(3)
        level_cols[0].metric("Entry reference", f"{currency}{record['Entry']:,.2f}")
        level_cols[1].metric(
            "Model target",
            f"{currency}{record['Target']:,.2f}",
            f"{record['Upside %']:+.1f}%" if record["Upside %"] is not None else None,
        )
        level_cols[2].metric("Risk reference", f"{currency}{record['Stop']:,.2f}")
        render_freshness(detail.get("freshness") or {})
        sizing_cols = st.columns(2)
        sizing_cols[0].metric(
            "Model allocation" if validated else "Risk-budget size (not advice)",
            f"{currency}{record['Allocation']:,.2f}",
            f"{record['Portfolio %']:.2f}% of capital · {record['Shares']:g} shares",
        )
        sizing_cols[1].metric(
            "Risk at reference stop",
            f"{currency}{record['Risk at stop']:,.2f}",
            "before gaps, slippage, fees, and taxes",
        )

        if isinstance(detail.get("earnings_days"), int) and 0 <= detail["earnings_days"] <= 14:
            st.warning(
                f"Earnings are expected in {detail['earnings_days']} days. Treat these levels as research references; event gaps can bypass stops."
            )

        st.markdown("##### Investment committee view")
        render_bullets(detail["reasons"])

        st.markdown("##### Factor scorecard")
        scorecard = pd.DataFrame(detail["scorecard"])
        st.dataframe(
            scorecard,
            hide_index=True,
            width="stretch",
            height=min(38 * (len(scorecard) + 1), 310),
            column_config={"Score": st.column_config.NumberColumn(format="%.1f")},
        )

        st.markdown("##### Technical evidence")
        render_bullets(detail["technical_points"])
        st.markdown("##### Fundamental evidence")
        render_bullets(detail["fundamental_points"])
        st.markdown("##### Risk evidence")
        render_bullets(detail["risk_points"])

        st.caption(
            f"Provider: {detail['data_provider']} · Quality: {detail['data_quality']} · "
            f"Adjustment: {detail['adjustment_status']} · Market date: {detail['market_timestamp'] or 'unavailable'} · "
            f"Retrieved: {detail['retrieval_timestamp'] or 'unavailable'}"
        )


def render_market_scan(report: dict) -> None:
    currency = report["currency_symbol"]
    regime = report["regime"]
    timestamp = report.get("analysis_timestamp", "")
    evidence = report.get("evidence") or {}
    validated = bool(evidence.get("permits_recommendation"))
    st.markdown(
        f"""
        <div class="decision-banner">
          <div class="kicker">Market brief · {esc(report['health'])} data path</div>
          <div class="headline">{int(report['selected_count'])} published ideas from {int(report['universe_count'])} securities</div>
          <div class="copy">{int(report['qualified_count'])} names cleared the scoring pipeline before final ranking and portfolio controls. The market is {esc(str(regime['type']).lower())} with {esc(str(regime['volatility']).lower())} volatility; published sizes deploy {report['deployment_pct']:.2f}% of capital and place approximately {report['risk_at_stop_pct']:.2f}% at risk at the reference stops.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if evidence:
        render_evidence_banner(evidence)
    st.caption(
        f"Run: {report['scan_label']} · As of {timestamp or 'unavailable'} · "
        f"Identical settings reuse this result for {SCAN_CACHE_TTL_SECONDS // 60} minutes; "
        "use Refresh data to force a new run"
    )

    section_label("Opportunity funnel")
    funnel_primary = st.columns(3)
    funnel_primary[0].metric("Universe scanned", f"{report['universe_count']:,}")
    funnel_primary[1].metric("Passed scoring pipeline", f"{report['qualified_count']:,}")
    funnel_primary[2].metric("Published after final controls", f"{report['selected_count']:,}")
    funnel_secondary = st.columns(2)
    funnel_secondary[0].metric("Filtered by model or policy", f"{report['filtered_count']:,}")
    funnel_secondary[1].metric("Provider / data failures", f"{report['failed_count']:,}")

    section_label("Regime and portfolio plan")
    regime_cols = st.columns(3)
    regime_cols[0].metric("Market regime", regime["type"])
    regime_cols[1].metric(
        "Volatility regime",
        regime["volatility"],
        f"VIX {regime['vix']:.1f}" if regime["vix"] else "VIX unavailable",
    )
    regime_cols[2].metric(
        "Regime exposure ceiling",
        f"{regime['exposure']:.0%}",
        f"Model cash guide {regime['cash']:.0%}",
    )
    portfolio_cols = st.columns(3)
    portfolio_cols[0].metric(
        "Capital deployed",
        f"{currency}{report['deployment']:,.2f}",
        f"{report['deployment_pct']:.2f}% of capital",
    )
    portfolio_cols[1].metric(
        "Cash unassigned",
        f"{currency}{report['cash_remaining']:,.2f}",
        f"{100 - report['deployment_pct']:.2f}% of capital",
    )
    portfolio_cols[2].metric(
        "Risk at reference stops",
        f"{currency}{report['risk_at_stop']:,.2f}",
        f"{report['risk_at_stop_pct']:.2f}% before gaps / costs",
    )
    st.caption(
        "The exposure ceiling is a guardrail, not a target. Stop-risk excludes overnight gaps, "
        "slippage, taxes, commissions, and correlation spikes."
    )

    if report["records"]:
        section_label("Published recommendations" if validated else "Published research candidates")
        results_df = pd.DataFrame(report["records"])
        table_height = min(38 * (len(results_df) + 1), 640)

        st.markdown("##### Decision ranking")
        st.dataframe(
            results_df[["Ticker", "Company", "Sector", "Action", "Score", "Confidence", "Risk grade"]],
            hide_index=True,
            width="stretch",
            height=table_height,
            column_config={
                "Ticker": st.column_config.TextColumn(width="small"),
                "Company": st.column_config.TextColumn(width="large"),
                "Sector": st.column_config.TextColumn(width="medium"),
                "Score": st.column_config.NumberColumn(format="%.1f"),
            },
        )

        st.markdown("##### Price and risk levels")
        st.dataframe(
            results_df[["Ticker", "Entry", "Target", "Stop", "Upside %", "Risk / reward"]],
            hide_index=True,
            width="stretch",
            height=table_height,
            column_config={
                "Entry": st.column_config.NumberColumn(format=f"{currency} %.2f"),
                "Target": st.column_config.NumberColumn(format=f"{currency} %.2f"),
                "Stop": st.column_config.NumberColumn(format=f"{currency} %.2f"),
                "Upside %": st.column_config.NumberColumn(format="%.1f%%"),
                "Risk / reward": st.column_config.NumberColumn(format="%.1f : 1"),
            },
        )

        st.markdown("##### Position sizing and portfolio risk")
        st.dataframe(
            results_df[["Ticker", "Setup", "Allocation", "Portfolio %", "Shares", "Risk at stop"]],
            hide_index=True,
            width="stretch",
            height=table_height,
            column_config={
                "Allocation": st.column_config.NumberColumn(format=f"{currency} %.0f"),
                "Portfolio %": st.column_config.NumberColumn(format="%.1f%%"),
                "Shares": st.column_config.NumberColumn(format="%.3g"),
                "Risk at stop": st.column_config.NumberColumn(format=f"{currency} %.2f"),
            },
        )
        st.caption(
            "WATCHLIST, HOLD, and AVOID decisions should carry zero new-money allocation. "
            "All price levels are model references, not live orders."
        )
        st.download_button(
            "Export the complete decision table (.csv)",
            data=market_scan_csv(report),
            file_name=f"quanta_{report['scan_label'].lower().replace(' ', '_')}_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
            key="scan_export",
        )

        if report["sector_rows"]:
            section_label("Diversification and concentration")
            sector_df = pd.DataFrame(report["sector_rows"])
            if len(sector_df) == 1:
                sector_name = sector_df.iloc[0]["Sector"]
                st.warning(
                    f"All published capital is concentrated in {sector_name}. This is concentration within "
                    "the model's deployed sleeve; unassigned cash remains outside that sector exposure."
                )
            else:
                st.bar_chart(
                    sector_df.set_index("Sector")["Portfolio %"],
                    color="#0e5a46",
                    horizontal=True,
                    height=min(90 + 42 * len(sector_df), 360),
                )
            st.dataframe(
                sector_df,
                hide_index=True,
                width="stretch",
                height=min(38 * (len(sector_df) + 1), 360),
                column_config={
                    "Allocation": st.column_config.NumberColumn(format=f"{currency} %.2f"),
                    "Portfolio %": st.column_config.NumberColumn(format="%.2f%%"),
                },
            )

        section_label("Research briefs")
        for rank, detail in enumerate(report["details"], 1):
            render_scan_detail(detail, currency, rank, validated)
    else:
        st.info(
            "No security cleared every model and portfolio gate. Holding cash is a valid outcome; consider a broader universe or rerun when conditions change."
        )

    section_label("Scan integrity and methodology")
    st.write(report["methodology_note"])
    error_summary = report.get("error_summary") or {}
    integrity_cols = st.columns(3)
    integrity_cols[0].metric("Data path", report["health"])
    integrity_cols[1].metric(
        "Provider exception rate",
        f"{float(error_summary.get('exception_rate', 0)):.2%}",
    )
    integrity_cols[2].metric(
        "Rate-limited records",
        f"{int(error_summary.get('provider_rate_limited_count', 0)):,}",
    )
    st.markdown(
        f"**Failed symbols:** {', '.join(report['failed_tickers']) if report['failed_tickers'] else 'None'}"
    )

    if report["filter_rows"]:
        st.markdown("##### Model and policy exclusion reasons")
        filter_df = pd.DataFrame(report["filter_rows"])
        st.dataframe(
            filter_df,
            hide_index=True,
            width="stretch",
            height=min(38 * (len(filter_df) + 1), 520),
        )
    if report["gate_rows"]:
        st.markdown("##### Final decision-gate outcomes")
        gate_df = pd.DataFrame(report["gate_rows"])
        st.dataframe(
            gate_df,
            hide_index=True,
            width="stretch",
            height=min(38 * (len(gate_df) + 1), 420),
        )
    if not report["filter_rows"] and not report["gate_rows"]:
        st.caption("The engine did not expose filter counters for this run.")

    if report["chase_alerts"]:
        st.markdown("##### Too extended to chase")
        st.warning(
            "These symbols were excluded because the move was already extended. "
            "They are observations, not entry recommendations."
        )
        st.dataframe(pd.DataFrame(report["chase_alerts"]), hide_index=True, width="stretch")

    provider_status = error_summary.get("provider_status")
    if provider_status:
        with st.expander("Raw provider telemetry"):
            st.json(provider_status)


def render_advisor_analysis(report: dict) -> None:
    symbol = report["currency_symbol"]
    st.markdown(
        f"""
        <div class="decision-banner">
          <div class="kicker">Advisor brief · {esc(report['ticker'])} · {esc(report['sector'])}</div>
          <div class="headline">{esc(report['headline'])}</div>
          <div class="copy">{esc(report['bottom_line'])}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if report.get("evidence"):
        render_evidence_banner(report["evidence"])

    primary_cols = st.columns(3)
    primary_cols[0].metric("Action", report["recommendation_label"])
    primary_cols[1].metric(
        "Current price",
        f"{symbol}{report['current_price']:,.2f}",
        f"{report['day_change_pct']:+.2f}% today",
    )
    primary_cols[2].metric("Composite score", f"{report['score']:.0f}/100")
    render_freshness(report.get("freshness") or {})
    secondary_cols = st.columns(2)
    secondary_cols[0].metric(
        "Evidence strength",
        report["evidence_label"],
        f"{report['evidence_score']:.0f}/100 model agreement",
    )
    secondary_cols[1].metric("Risk grade", report["risk_grade"])
    st.caption("Evidence strength describes agreement among model inputs; it is not a probability of profit.")

    if isinstance(report.get("earnings_days"), (int, float)) and 0 <= report["earnings_days"] <= 14:
        date_text = f" on {report['earnings_date']}" if report.get("earnings_date") else ""
        st.warning(
            f"Earnings are expected in {int(report['earnings_days'])} days{date_text}. Results and guidance can reset valuation and trend, and a gap can bypass a stop."
        )

    st.markdown("### Advisor Summary")
    section_label("The decision for your situation")
    new_col, holder_col = st.columns(2)
    with new_col:
        st.markdown("##### Considering a new position")
        st.warning(report["new_buyer"])
    with holder_col:
        st.markdown("##### Already own the stock")
        st.info(report["existing_holder"])

    section_label("Evidence behind the call")
    st.markdown("##### Why the model reached this conclusion")
    render_bullets(report["decision_reasons"])
    bull_col, bear_col = st.columns(2)
    with bull_col:
        st.markdown("##### What supports the stock")
        render_bullets(report["bull_case"] or ["No high-confidence bullish factor was confirmed."])
    with bear_col:
        st.markdown("##### What could break the thesis")
        render_bullets(report["bear_case"] or ["Normal market and company-specific risks still apply."])

    section_label("Multi-factor scorecard")
    if report["scorecard"]:
        st.dataframe(pd.DataFrame(report["scorecard"]), width="stretch", hide_index=True)

    with st.expander("Technical setup", expanded=True):
        render_bullets(report["technical_points"])
    with st.expander("Business quality and fundamentals", expanded=True):
        render_bullets(report["fundamental_points"])
    with st.expander("Valuation and price scenarios", expanded=True):
        render_bullets(report["valuation_points"] or ["No reliable valuation estimate was available."])
    with st.expander("Risk assessment", expanded=True):
        render_bullets(report["risk_points"])

    section_label("Reference levels — not automatic orders")
    level_items = list(report["levels"].items())
    level_cols = st.columns(2)
    for column, (label, value) in zip(level_cols, level_items[:2]):
        column.metric(label, f"{symbol}{value:,.2f}" if value else "N/A")
    level_cols = st.columns(2)
    for column, (label, value) in zip(level_cols, level_items[2:]):
        column.metric(label, f"{symbol}{value:,.2f}" if value else "N/A")
    freshness_text = (report.get("freshness") or {}).get("text", "")
    st.caption(
        f"{freshness_text} Levels can change after earnings or a material price move. Recalculate before acting."
    )

    section_label("Action plan")
    new_plan, holder_plan = st.columns(2)
    with new_plan:
        st.markdown("##### New buyer")
        for index, item in enumerate(report["new_buyer_plan"], 1):
            st.markdown(f"{index}. {item}")
    with holder_plan:
        st.markdown("##### Existing holder")
        for index, item in enumerate(report["existing_holder_plan"], 1):
            st.markdown(f"{index}. {item}")

    if report["catalysts"]:
        st.markdown("##### Upcoming catalysts")
        render_bullets(report["catalysts"])

    section_label("Provenance and limitations")
    with st.expander("Data quality, sources, and important limitations", expanded=True):
        st.markdown(f"- Provider: **{report['provider']}**")
        st.markdown(f"- Data quality: **{report['data_quality']}**")
        st.markdown(f"- Market data date: **{report['market_timestamp'] or 'Unavailable'}**")
        st.markdown(f"- Analysis retrieved: **{report['retrieval_timestamp'] or 'Unavailable'}**")
        render_bullets(report["disclosures"])


for key, default in (
    ("market_report", None),
    ("market_report_fingerprint", ""),
    ("market_error", ""),
    ("market_notice", ""),
    ("single_report", None),
    ("single_error", ""),
):
    if key not in st.session_state:
        st.session_state[key] = default


render_masthead()
refresh_col, refresh_note_col = st.columns([1, 3])
with refresh_col:
    if st.button("Refresh data", key="refresh_cache", help="Clear cached scans, briefs, and engine objects."):
        clear_cached_results()
        st.session_state.cache_cleared = True
with refresh_note_col:
    if st.session_state.pop("cache_cleared", False):
        st.caption("Cached results cleared. The next scan or brief fetches fresh data.")
    else:
        st.caption(
            "Scans and briefs are cached for identical inputs; Refresh data forces a fresh run."
        )
market_tab, stock_tab, method_tab = st.tabs(["MARKET SCAN", "SINGLE STOCK", "METHOD & SAFETY"])

with market_tab:
    st.markdown('<div class="eyebrow">Cross-sectional opportunity research</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="editorial-title">Find the signal.<br>See the risk first.</h1>', unsafe_allow_html=True)
    st.markdown(
        '<div class="editorial-deck">A disciplined market screen that explains not only what passed, but what failed, how much capital the model would deploy, and where the portfolio can lose money.</div>',
        unsafe_allow_html=True,
    )

    with st.container(key="market_controls"):
        st.markdown("#### Configure this scan")
        st.markdown(
            '<div class="control-intro">These controls apply only to Market Scan. Changing tabs no longer leaves an unrelated control panel on screen.</div>',
            unsafe_allow_html=True,
        )
        market_col, capital_col = st.columns(2)
        with market_col:
            market = st.selectbox(
                "Market region",
                ["US · NYSE / Nasdaq", "India · NSE / BSE"],
                key="scan_market",
                help="Sets the security universe, benchmark, currency, and volatility index.",
            )
        is_india = market.startswith("India")
        currency = "₹" if is_india else "$"
        with capital_col:
            capital = st.number_input(
                f"Portfolio capital ({currency})",
                min_value=1_000,
                max_value=10_000_000,
                value=250_000 if is_india else 10_000,
                step=1_000,
                key="scan_capital_india" if is_india else "scan_capital_us",
                help="Used for model position sizing and portfolio-risk calculations.",
            )

        if is_india:
            scan_options = {
                "Nifty 50 · Quick": "INDIA_QUICK",
                "Standard · Recommended": "INDIA_STANDARD",
                "Sector focus": "INDIA_SECTORAL",
                "Comprehensive": "INDIA_COMPREHENSIVE",
                "Full coverage": "INDIA_FULL",
            }
        else:
            scan_options = {
                "Quick · 30 stocks": "QUICK",
                "Standard · Recommended": "STANDARD",
                "Comprehensive": "COMPREHENSIVE",
                "Full coverage · 200 stocks": "FULL",
            }

        universe_col, count_col = st.columns([1.4, 1])
        with universe_col:
            scan_type = st.selectbox(
                "Research universe",
                list(scan_options),
                index=1,
                key="scan_universe_india" if is_india else "scan_universe_us",
                help="Larger universes take longer and increase provider load.",
            )
        with count_col:
            max_positions = st.slider(
                "Maximum published ideas",
                min_value=1,
                max_value=15,
                value=5,
                key="scan_max_positions",
                help="This is a ceiling; the model may publish fewer or none.",
            )

        option_col, action_col = st.columns([2.2, 1])
        with option_col:
            diversify = st.checkbox(
                "Enforce sector diversification",
                value=True,
                key="scan_diversify",
                help="Reduces concentration when multiple candidates have similar scores.",
            )
            st.caption(
                "Confidence is heuristic—not a win probability. Event-risk and final-action controls can reduce an allocation to zero."
            )
        with action_col:
            scan_clicked = st.button("Run market scan", key="scan_button", type="primary")

    market_code = "INDIA" if is_india else "US"
    scan_code = scan_options[scan_type]
    scan_key = scan_cache_key(market_code, capital, scan_code, max_positions, diversify)
    fingerprint = cache_fingerprint(scan_key)
    st.markdown(
        f'<div class="parameter-strip"><span class="parameter-chip">{esc(market)}</span><span class="parameter-chip">{esc(scan_type)}</span><span class="parameter-chip">Capital {esc(currency)}{capital:,.0f}</span><span class="parameter-chip">Up to {int(max_positions)} ideas</span><span class="parameter-chip">Sector guardrail: {"On" if diversify else "Off"}</span></div>',
        unsafe_allow_html=True,
    )

    if scan_clicked:
        st.session_state.market_error = ""
        st.session_state.market_notice = ""
        st.session_state.market_report = None
        with st.spinner("Building the universe, validating data, and applying portfolio gates…"):
            report = None
            try:
                report = _cached_market_report(*scan_key, scan_label=scan_type, currency_symbol=currency)
            except UncacheableResult as exc:
                # Degraded run: shown with a warning, never cached.
                report = exc.payload
                if report is None:
                    st.session_state.market_error = exc.message
                else:
                    st.session_state.market_notice = exc.message
            except Exception as exc:
                st.session_state.market_error = f"{type(exc).__name__}: {exc}"
            if report:
                st.session_state.market_report = report
                st.session_state.market_report_fingerprint = fingerprint
                st.success(
                    f"Scan complete · Found {report['selected_count']} opportunities "
                    f"from {report['universe_count']} securities."
                )

    if st.session_state.market_notice:
        st.warning(st.session_state.market_notice)

    if st.session_state.market_error:
        st.error("The scan could not complete. No partial result is being presented as a recommendation.")
        with st.expander("Technical details"):
            st.code(st.session_state.market_error)

    if st.session_state.market_report:
        if st.session_state.market_report_fingerprint != fingerprint:
            st.warning("These saved results use different controls than the current scan setup. Run the scan again to refresh them.")
        render_market_scan(st.session_state.market_report)
    else:
        st.info("No scan has been run in this session. Choose a universe and run the model to create the market brief.")


with stock_tab:
    st.markdown('<div class="eyebrow">Company research</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="editorial-title">One stock.<br>A complete decision brief.</h1>', unsafe_allow_html=True)
    st.markdown(
        '<div class="editorial-deck">A deep review of price action, business quality, valuation, catalysts, evidence strength, and downside—translated into separate guidance for a new buyer and an existing holder.</div>',
        unsafe_allow_html=True,
    )
    with st.form("single_stock_form", clear_on_submit=False):
        input_col, capital_col = st.columns([1.2, .8])
        with input_col:
            ticker_input = st.text_input(
                "Ticker symbol",
                placeholder="AAPL or RELIANCE.NS",
                help="Use .NS for NSE or .BO for BSE listings when needed.",
            )
        with capital_col:
            single_capital = st.number_input(
                "Account capital (listing currency)",
                min_value=1_000,
                max_value=10_000_000,
                value=10_000,
                step=1_000,
                key="single_capital",
                help="Used only for this stock brief. Enter capital in the listing's currency.",
            )
        analyze_clicked = st.form_submit_button("Build stock brief", type="primary", key="stock_submit")

    if analyze_clicked:
        normalized_ticker, normalized_capital = stock_cache_key(ticker_input, single_capital)
        st.session_state.single_error = ""
        st.session_state.single_report = None
        if not normalized_ticker:
            st.session_state.single_error = "Enter a ticker symbol before running the analysis."
        else:
            with st.spinner(f"Validating {normalized_ticker}, retrieving market data, and building the decision brief…"):
                try:
                    # Invalid / no-data outcomes raise UncacheableResult inside the
                    # cached call, so only a usable brief is ever cached.
                    st.session_state.single_report = _cached_stock_report(normalized_ticker, normalized_capital)
                    st.success(f"Analysis complete for {normalized_ticker}.")
                except UncacheableResult as exc:
                    st.session_state.single_error = exc.message
                except Exception as exc:
                    st.session_state.single_error = f"{type(exc).__name__}: {exc}"

    if st.session_state.single_error:
        st.error(st.session_state.single_error)
    if st.session_state.single_report:
        render_advisor_analysis(st.session_state.single_report)
    else:
        st.info("Enter a symbol to create a full advisor-style research brief. The most recent result will remain available during this session.")


with method_tab:
    st.markdown('<div class="eyebrow">Model governance</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="editorial-title">Useful research<br>requires honest limits.</h1>', unsafe_allow_html=True)
    st.markdown(
        '<div class="editorial-deck">The dashboard is designed to make uncertainty visible. A score is a comparison tool, not a promise; a target is a scenario, not a guarantee; and a stop is a risk reference that can be bypassed by a price gap.</div>',
        unsafe_allow_html=True,
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("#### What the model evaluates")
        render_bullets([
            "Price trend, momentum, participation, and technical structure",
            "Growth, profitability, balance-sheet quality, and valuation",
            "Volatility, drawdown, beta, liquidity, and event risk",
            "Position sizing, sector concentration, and market regime",
        ])
    with col2:
        st.markdown("#### What it does not know")
        render_bullets([
            "Your cost basis, taxes, liabilities, income needs, or legal restrictions",
            "Non-public information or future earnings surprises",
            "The exact fill price, slippage, or size available in a fast market",
            "Whether an analyst target or model scenario will be achieved",
        ])
    with col3:
        st.markdown("#### Built-in safeguards")
        render_bullets([
            "Fail-closed handling when required data cannot be validated",
            "Event-risk and final-action controls can force zero allocation",
            "Split/dividend-adjusted history and explicit data provenance",
            "Separate guidance for new capital and existing positions",
        ])


st.markdown(
    """
    <div class="footer-note"><strong>QUANTA Research Desk</strong> · Independent quantitative decision support.<br>
    Educational research only—not individualized investment, tax, or legal advice. Market data may be delayed or incomplete. Verify prices, filings, corporate actions, earnings dates, and suitability with authoritative sources before making a financial decision.</div>
    """,
    unsafe_allow_html=True,
)
