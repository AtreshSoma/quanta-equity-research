/**
 * Synthetic Picks payloads shaped like the worker's result after _picks_view:
 * every row is a call (Buy, Accumulate or Watchlist) with its capital fit;
 * names that are not picks sit in screened_out / excluded_for_safety.
 */
const base = (ticker, rank, extra = {}) => ({
  rank, ticker, company: `${ticker} Inc.`, sector: 'Technology', price: 100, price_session: '2026-10-02',
  signal: 'ACCUMULATE', entry: 100, entry_low: 98, entry_high: 100, target: 110, upside_pct: 10, stop: 95,
  risk_reward: 2.2, levels_label: 'Research levels (binding)', horizon_days: 63, score: 7 - rank * 0.1,
  risk_grade: 'B', conviction: 4, scores: { technical: 6.1, fundamental: 7, momentum: 5.2, quality: 6.8, value: 4.9 },
  evidence: 'UNTESTED', why: 'Score 69 of 100 is in the BUY band (65 or more)', allocation: 0, shares: 0,
  status: 'not_funded', reason: '', gate: null, capital_needed: null, ...extra,
});

// Buy (sized), Accumulate (one share costs more than the cap allows), two Watchlist names:
// a deep-confirm downgrade and an unreadable corporate-event check with its fix.
const ROWS = () => [
  base('AMZN', 1, { signal: 'BUY', call: 'buy', status: 'funded', allocation: 252.65, shares: 1, price: 252.65, entry: 252.65,
    stop: 244.51, entry_low: 247.22, entry_high: 252.65, target: 272.14, upside_pct: 7.71, risk_reward: 2.28,
    capital: { kind: 'sized', dollars: 252.65, shares: 1 } }),
  base('MSFT', 2, { signal: 'ACCUMULATE', call: 'accumulate', status: 'not_funded', capital_needed: 2900, upside_pct: 12,
    why: 'Score 64 of 100 is in the ACCUMULATE band (55 to 65)',
    reason: 'One share costs $517.53, 20.7% of capital: over the 18% position cap',
    capital: { kind: 'needs', needed: 2900, text: 'Needs about $2,900 for one share within the position cap' } }),
  base('CSCO', 3, { signal: 'WATCHLIST', call: 'watchlist', status: 'watch', upside_pct: -0.2,
    reason: 'Deep review downgraded it: heavy insider selling',
    capital: { kind: 'none', text: 'No new money on a Watchlist call' } }),
  base('JPM', 4, { signal: 'WATCHLIST', call: 'watchlist', status: 'watch', upside_pct: 6, sector: 'Financials',
    reason: 'Corporate-event check unavailable: filing history longer than the pages read. Held at Watchlist until it can be checked.',
    fix: 'Raise EVENTS.FILING_PAGES_MAX in thresholds.py',
    capital: { kind: 'none', text: 'No new money on a Watchlist call' } }),
];

const SCREENED = () => [{ ticker: 'LOWR', company: 'LOWR Inc.', gate: { gate: 'risk_reward', now: 0.8, needed: 1.8, unit: 'net R:R' } }];
const EXCLUDED = () => [{ ticker: 'AVDX', company: 'AVDX Inc.', reason: 'Pending cash acquisition at $102.50.',
  source: 'SEC EDGAR', filed: '2026-09-28', url: 'https://www.sec.gov/Archives/edgar/data/1/x.htm' }];

const payload = (extra = {}) => ({
  scan_status: 'success', currency: '$', capital: 2500, max_positions: 5, scan_type: 'quick',
  regime: { type: 'BULL', vix: 15.6 }, risk_profile: { profile: 'balanced' }, timestamp: '2026-10-05T11:59:00-04:00',
  total_scanned: 100, funded_count: 1, watch_count: 2, stock_picks: ROWS(), screened_out: SCREENED(), excluded_for_safety: EXCLUDED(),
  recommendations: [{ ticker: 'AMZN', is_funded: true, final_action: 'BUY', allocation: 252.65, allocation_after: 252.65, shares: 1, entry_price: 252.65, stop_loss: 244.51 }],
  universe_selection: { method: 'liquidity_ranked', candidates: 518, selected: 100, seconds: 12.8, seconds_measure: 'price_download' },
  accounting: { version: 1, universe: 100, funded: 1, watch: 2, reached_book: 6, could_not_analyse: { count: 0, by_reason: {} },
    rule_rejected: { count: 94, by_reason: { signal_not_actionable: 50, risk_reward_low: 44 } }, book_stages: [] },
  event_check: { checked: 100, clear: 98, flagged: 1, unavailable: 1, reasons: [], contact: { state: 'configured' } },
  ...extra,
});

// The named cases the render tests cover.
const CASES = {
  only_watch: () => payload({ stock_picks: ROWS().filter((r) => r.call === 'watchlist'), recommendations: [], funded_count: 0 }),
  only_buy: () => payload({ stock_picks: ROWS().filter((r) => r.call === 'buy'), watch_count: 0 }),
  cost_limited: () => payload({ stock_picks: ROWS().filter((r) => r.ticker === 'MSFT'), recommendations: [], funded_count: 0, watch_count: 0 }),
  two_classes: () => payload({ stock_picks: [{ ...ROWS()[0], other_classes: [{ ticker: 'AMZN.B', company: 'AMZN Inc.' }] }] }),
  event_unavailable: () => payload({ stock_picks: ROWS().filter((r) => r.ticker === 'JPM'), recommendations: [], funded_count: 0 }),
  pending_acquisition: () => payload({ stock_picks: [], recommendations: [], funded_count: 0, watch_count: 0 }),
  empty: () => payload({ stock_picks: [], screened_out: [], excluded_for_safety: [], recommendations: [], funded_count: 0, watch_count: 0 }),
};

module.exports = { base, ROWS, SCREENED, EXCLUDED, payload, CASES };
