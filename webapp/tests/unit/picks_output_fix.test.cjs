/**
 * Picks output fix (I1-I8, 2026-10-04 scan): what the page shows when the
 * corporate-event check could not run, the held names, the finished progress
 * panel, the minimum position, filled gaps and the coverage line.
 * Synthetic payloads only.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/picks_output_fix.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'templates', 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');

const text = (h) => String(h || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

const FIX = 'Set QUANTA_SEC_CONTACT to your email address, then restart the server';
const missingEvent = () => ({
  checked: 100, clear: 0, flagged: 0, unavailable: 100,
  reasons: [{ reason: 'contact_missing', code: 'EVENT_CHECK_UNAVAILABLE', label: 'no SEC contact configured',
    fix: FIX, count: 100, book_tickers: ['CSCO', 'GOOGL', 'MSFT', 'GOOG', 'AMZN'] }],
  contact: { state: 'missing', source: null, variable: 'QUANTA_SEC_CONTACT', label: 'no SEC contact configured', fix: FIX },
});

// ── I1 ──────────────────────────────────────────────────────────────────────

test('I1: a missing SEC contact shows a banner with the variable, the cause and the fix', () => {
  const t = text(app.eventCheckBannerHtml({ event_check: missingEvent() }));
  assert.match(t, /Corporate-event check unavailable for 100 of 100 names checked/);
  assert.match(t, /no SEC contact configured/);
  assert.ok(t.includes(FIX), 'the fix comes from the payload');
  assert.match(t, /CSCO, GOOGL, MSFT, GOOG, AMZN/);
});

test('I1: each unavailable reason is listed with its own fix', () => {
  const ev = { checked: 10, clear: 7, flagged: 0, unavailable: 3, contact: { state: 'configured' },
    reasons: [{ reason: 'rate_limited', label: 'rate-limited by SEC EDGAR', fix: 'Wait a few minutes, then run the scan again', count: 2, book_tickers: [] },
      { reason: 'timeout', label: 'SEC EDGAR timed out', fix: 'Run the scan again', count: 1, book_tickers: ['AAA'] }] };
  const t = text(app.eventCheckBannerHtml({ event_check: ev }));
  assert.match(t, /3 of 10 names checked/);
  assert.match(t, /rate-limited by SEC EDGAR · 2 names\. Fix: Wait a few minutes/);
  assert.match(t, /SEC EDGAR timed out · 1 name \(AAA reached the book\)\. Fix: Run the scan again/);
  assert.doesNotMatch(t, /QUANTA_SEC_CONTACT/);
});

test('I1: no banner when the contact is configured and every check answered', () => {
  assert.equal(app.eventCheckBannerHtml({ event_check: { checked: 5, clear: 5, unavailable: 0, reasons: [], contact: { state: 'configured' } } }), '');
  assert.equal(app.eventCheckBannerHtml({}), '');
});

test('I1: before a scan, the health record drives a notice naming the variable and the fix', () => {
  assert.equal(app.secContactNoticeText({ sec_contact: { state: 'configured' } }), '');
  const t = app.secContactNoticeText({ sec_contact: missingEvent().contact });
  assert.match(t, /no SEC contact configured/);
  assert.ok(t.includes(FIX));
});

const renderEmpty = (data) => {
  global.window = {};          // the empty path asks the optional Pro layer for extras
  try { app.renderPicks(data); } finally { global.window = undefined; }
};

test('I1: the banner is rendered in an empty result', () => {
  renderEmpty({ scan_status: 'no_picks', recommendations: [], event_check: missingEvent(),
    accounting: { version: 1, funded: 0, watch: 0, universe: 100, could_not_analyse: { count: 0 } } });
  const banner = document.querySelector('#picks-results [data-event-check-banner]');
  assert.ok(banner, 'banner present');
});

// ── I2 ──────────────────────────────────────────────────────────────────────

const heldRow = (t) => ({ ticker: t, company: `Synthetic ${t}`, sector: 'Technology', signal: 'BUY',
  final_action: 'WATCHLIST', display_action: 'WATCHLIST', allocation: 0, allocation_after: 0, shares: 0, is_funded: false,
  entry_price: 100, stop_loss: 95, price_target: 115, risk_reward_ratio: 3, overall_score: 7.9,
  why: 'Corporate-event check unavailable: no SEC contact is configured for the server. Held at Watchlist until it can be checked.',
  event_check: { status: 'unavailable', code: 'EVENT_CHECK_UNAVAILABLE', reason: 'contact_missing', fix: FIX },
  decision: { action: 'WATCHLIST', blocking_codes: ['EVENT_CHECK_UNAVAILABLE'],
    primary_blocker: { code: 'EVENT_CHECK_UNAVAILABLE', category: 'missing_data', text: 'The corporate-event check could not be completed', remedy: FIX } } });
const HELD = ['CSCO', 'GOOGL', 'MSFT', 'GOOG', 'AMZN'];
const heldAccounting = () => ({ version: 1, universe: 100, funded: 0, watch: 5, reached_book: 5,
  could_not_analyse: { count: 1, by_reason: { data_validation_failed: 1 } }, rule_rejected: { count: 94, by_reason: {} },
  book_stages: [{ key: 'actionable', entered: 5, passed: 5, evaluated: true },
    { key: 'event', entered: 5, passed: 0, evaluated: true, held: { count: 5, tickers: HELD }, avoided: { count: 0, tickers: [] } }] });

test('I2: a held row says what clears it, from its own primary blocker, with the rule code', () => {
  app.renderPicks({ scan_status: 'no_picks', currency: '$', capital: 2500, max_positions: 5, regime: { type: 'BULL' },
    risk_profile: {}, recommendations: HELD.map(heldRow), funded_count: 0, watch_count: 5,
    accounting: heldAccounting(), event_check: missingEvent() });
  const t = document.querySelector('#picks-results').textContent.replace(/\s+/g, ' ');
  assert.doesNotMatch(t, /The blocking rule was not reported/);
  const n = t.split(FIX).length - 1;
  assert.ok(n >= 5, `fix shown on every held row (found ${n})`);
  assert.match(t, /EVENT_CHECK_UNAVAILABLE/);
  for (const tk of HELD) assert.ok(t.includes(tk), tk);
});

test('I2: the event stage says how many names it held at Watch, and which', () => {
  const html = require('../../static/elite-pro.js').stageTableCard({ accounting: heldAccounting() });
  const t = text(html);
  assert.match(t, /0 of 5 5 held at Watch: CSCO, GOOGL, MSFT, GOOG, AMZN/);
});

// ── I3 ──────────────────────────────────────────────────────────────────────

const diag = (t, fails = []) => ({ ticker: t, company: `Synthetic ${t}`, verdict: fails.length ? 'also_fails' : 'event_only',
  also_fails: fails, not_assessed: ['selection', 'final_review'], diagnostic_only: true, funded: false,
  held_by: { code: 'EVENT_CHECK_UNAVAILABLE', reason: 'contact_missing', text: 'x', fix: FIX } });

test('I3: held names are diagnosed as diagnostic only, never funded', () => {
  const t = text(app.eventHeldDiagnosticsHtml({ event_held_diagnostics: [diag('CSCO'),
    diag('MSFT', [{ gate: 'risk_reward', now: 1.5, needed: 1.8, unit: 'net R:R' }])] }));
  assert.match(t, /diagnostic only, they stay on the Watchlist/i);
  assert.match(t, /CSCO .*Blocked only by the event check/);
  assert.match(t, /MSFT .*Also fails: Reward to risk 1\.50:1, needs 1\.80:1/);
  assert.match(t, /position, sector, correlation and stop-risk limits and the Research check were not assessed/i);
  assert.equal(app.eventHeldDiagnosticsHtml({}), '');
});

test('I3: a long diagnostic list shows the configured count and a Show all control', () => {
  const T = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'thresholds.json'), 'utf8'));
  const n = T.UI.NAME_LIST_SHOWN + 2;
  const html = app.eventHeldDiagnosticsHtml({ event_held_diagnostics: Array.from({ length: n }, (_, i) => diag(`T${i}`)) });
  const d = new JSDOM(`<div>${html}</div>`).window.document;
  assert.equal(d.querySelectorAll('[data-diag-ticker]').length, n, 'every name is in the DOM');
  const more = d.querySelector('details.name-list-more');
  assert.ok(more, 'overflow sits behind a Show all control');
  assert.match(more.querySelector('summary').textContent, new RegExp(`Show all ${n}`));
  assert.equal(more.querySelectorAll('[data-diag-ticker]').length, 2);
});

// ── I4 ──────────────────────────────────────────────────────────────────────

test('I4: after Complete no stage is current, and each says its final count or why it was skipped', () => {
  app.renderScanStages({ key: 'complete', label: 'Complete', terminal: true, stages: [
    { key: 'selecting', label: 'Universe', state: 'done', done: 0, total: 0, note: '' },
    { key: 'analysing', label: 'Analysis', state: 'done', done: 100, total: 100, note: '' },
    { key: 'book', label: 'Book rules', state: 'done', done: 0, total: 5, note: '5 in, 0 Buy or Accumulate, 5 Watchlist' },
    { key: 'research_check', label: 'Research check', state: 'skipped', done: null, total: null, note: 'skipped: no name was selected for it' }] });
  const items = [...document.querySelectorAll('#sp-stages li')];
  assert.equal(items.length, 4);
  assert.equal(document.querySelectorAll('#sp-stages [data-state="current"], #sp-stages [aria-current]').length, 0);
  const t = items.map((li) => `${li.dataset.state}|${li.textContent}`);
  assert.deepEqual(t, ['done|Universe', 'done|Analysis: 100 of 100', 'done|Book rules: 5 in, 0 Buy or Accumulate, 5 Watchlist',
    'skipped|Research check: skipped: no name was selected for it']);
  assert.doesNotMatch(document.querySelector('#sp-stages').textContent, /Applying the book rules: 0 of 5|Running the Research check/);
});

test('I4/P1: a failed scan marks the failed stage with why', () => {
  app.renderScanStages({ key: 'failed', terminal: true, stages: [
    { key: 'analysing', label: 'Analysis', state: 'failed', done: 2, total: 8, note: 'failed: the scan failed at 2 of 8' },
    { key: 'book', label: 'Book rules', state: 'skipped', note: 'skipped: the scan failed' }] });
  const t = [...document.querySelectorAll('#sp-stages li')].map((li) => `${li.dataset.state}|${li.textContent}`);
  assert.deepEqual(t, ['failed|Analysis: failed: the scan failed at 2 of 8', 'skipped|Book rules: skipped: the scan failed']);
});

test('P1: stage markers are drawn icons, never glyphs or raw control characters', () => {
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  const rules = [...css.matchAll(/\.scan-stages li\[data-state="(\w+)"\]::before \{ content: "([^"]*)"/g)];
  const states = rules.map((m) => m[1]);
  for (const st of ['done', 'skipped', 'failed']) assert.ok(states.includes(st), `marker for ${st}`);
  for (const [, st, content] of rules) {
    assert.doesNotMatch(content, /[\u0000-\u001f\u007f-\u009f]/, `${st} marker has a control character`);
    assert.equal(content, '', `${st} marker draws its icon, it does not print a glyph`);
    const rule = css.slice(css.indexOf(`.scan-stages li[data-state="${st}"]::before`)).split('\n')[0];
    assert.match(rule, /mask: url\("data:image\/svg\+xml/, `${st} marker is an SVG mask`);
  }
});

// ── I5 ──────────────────────────────────────────────────────────────────────

test('I5: the zero-allocation label states the minimum the scan applied at its capital', () => {
  renderEmpty({ scan_status: 'no_picks', recommendations: [], capital: 2500, min_position_effective: 50,
    accounting: { version: 1, funded: 0, watch: 0, universe: 1, could_not_analyse: { count: 0 } } });
  const t = app.filterWords('zero_allocation');
  assert.match(t, /under the \$50 minimum position/);
  assert.doesNotMatch(t, /\$100/);
});

test('I5: the guide calculator and FAQ use the capital-scaled minimum, not the large-account cap', () => {
  const T = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'thresholds.json'), 'utf8'));
  const eff = Math.min(T.SIZING.MIN_POSITION_SIZE, Math.max(T.SIZING.MIN_POSITION_FLOOR, T.SIZING.MIN_POSITION_PCT * 2500));
  assert.equal(app.guideMinTrade(2500), eff);
  assert.equal(app.guideMinTrade(1e6), T.SIZING.MIN_POSITION_SIZE);
  app.applyThresholds(document);
  const faq = [...document.querySelectorAll('details')].find((d) => /how many shares/i.test(d.textContent));
  const f = faq.textContent.replace(/\s+/g, ' ');
  assert.match(f, new RegExp(`minimum trade of ${+(T.SIZING.MIN_POSITION_PCT * 100).toFixed(2)}% of capital`));
});

// ── I6 ──────────────────────────────────────────────────────────────────────

test('I6: a partly filled gap and a fading run say so in the trigger', () => {
  const part = app.chaseTriggerText({ trigger: 'gap', move_pct: 17.78, threshold_pct: 5, state: 'gap_partly_filled',
    gap_fill_pct: 52.4, fade_since_gap_close_pct: -6.31 });
  assert.match(part, /Gap \+17\.8% at the open \(over 5%\) · partly filled 52%, -6\.3% since the gap-day close/);
  const run = app.chaseTriggerText({ trigger: 'run_5d', move_pct: 16.3, threshold_pct: 10, off_peak_pct: -3.97, peak_date: '2026-09-30' });
  assert.match(run, /5-day run \+16\.3% \(over 10%\) · 4\.0% below its 2026-09-30 peak/);
  assert.doesNotMatch(app.chaseTriggerText({ trigger: 'gap', move_pct: 6, threshold_pct: 5, gap_fill_pct: 0 }), /filled/);
});

test('I6: filled gaps are listed apart as informational, never in the active chase table', () => {
  const active = { ticker: 'ACN', trigger: 'gap', move_pct: 17.78, threshold_pct: 5, bar_date: '2026-10-01', active: true };
  const filled = { ticker: 'IT', trigger: 'gap', move_pct: 5.17, threshold_pct: 5, bar_date: '2026-10-01', active: false,
    state: 'gap_filled', reason: 'Gap filled: IT opened with a 5.2% gap above the prior close on 2026-10-01, and closed 184.80 on 2026-10-02, at or below the pre-gap close 186.54. Informational, not a chase alert.' };
  const html = app.renderPicksBuckets([active], [], [filled]);
  const d = new JSDOM(`<div>${html}</div>`).window.document;
  assert.deepEqual([...d.querySelectorAll('[data-chase]')].map((r) => r.dataset.chase), ['ACN']);
  const box = d.querySelector('[data-chase-filled]');
  assert.ok(box, 'filled gaps have their own block');
  assert.match(box.textContent.replace(/\s+/g, ' '), /Gap filled · informational, not chase alerts \(1\)/);
  assert.match(box.textContent, /IT/);
  assert.match(box.textContent, /at or below the pre-gap close 186\.54/);
});

// ── I7 ──────────────────────────────────────────────────────────────────────

test('I7: the selection stage names a share class skipped as the same issuer', () => {
  const acc = { version: 1, universe: 3, funded: 2, watch: 0, reached_book: 3,
    could_not_analyse: { count: 0 }, rule_rejected: { count: 0 },
    book_stages: [{ key: 'selection', entered: 3, passed: 2, evaluated: true,
      same_issuer: { count: 1, tickers: ['GOOGL'], as: { GOOGL: 'GOOG' } } }] };
  const t = text(require('../../static/elite-pro.js').stageTableCard({ accounting: acc }));
  assert.match(t, /2 of 3 1 skipped as the same issuer: GOOGL \(as GOOG\)/);
});

// ── I8 ──────────────────────────────────────────────────────────────────────

const focused = () => ({ method: 'profile_ranked', candidates: 204, selected: 100, seconds: 5.9, seconds_measure: 'price_download',
  universe_total: 806, pool_size: 204,
  pool_label: 'S&P 500 names in Communication Services, Consumer Discretionary, Health Care, Information Technology' });

test('I8: the coverage line says what "of N" counts and that the seconds are the price download', () => {
  const t = new JSDOM(app.coverageLineHtml({ universe_selection: focused() })).window.document.body.textContent;
  assert.match(t, /Universe: 100 of 204 S&P 500 names in Communication Services, Consumer Discretionary, Health Care, Information Technology \(806 names in the full universe\), ranked on the scan's price and volume profile; price download 5\.9 s\./);
  assert.doesNotMatch(t, /ranked in|in 5\.9 s/);
});

test('I8: the coverage line states the corporate-event check status', () => {
  const missing = text(app.coverageLineHtml({ universe_selection: focused(), event_check: missingEvent() }));
  assert.match(missing, /Corporate-event check: unavailable for 100 of 100 names \(no SEC contact configured\)\./);
  const ok = text(app.coverageLineHtml({ universe_selection: focused(),
    event_check: { checked: 100, clear: 98, flagged: 2, unavailable: 0, reasons: [], contact: { state: 'configured' } } }));
  assert.match(ok, /Corporate-event check: 100 of 100 names checked, 2 flagged\./);
});

test('I8: the outcome table names the tickers that could not be analysed, with Show all for long lists', () => {
  const pro = require('../../static/elite-pro.js');
  const acc = { version: 1, universe: 100, funded: 0, watch: 5, reached_book: 5, rule_rejected: { count: 94 },
    could_not_analyse: { count: 1, by_reason: { data_validation_failed: 1 }, tickers: { data_validation_failed: ['ZZZ'] } },
    book_stages: [] };
  assert.match(text(pro.stageTableCard({ accounting: acc })), /data validation failed 1: ZZZ/);
  const T = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'thresholds.json'), 'utf8'));
  const many = Array.from({ length: T.UI.NAME_LIST_SHOWN + 3 }, (_, i) => `N${i}`);
  acc.could_not_analyse = { count: many.length, by_reason: { insufficient_data: many.length }, tickers: { insufficient_data: many } };
  const d = new JSDOM(`<div>${pro.stageTableCard({ accounting: acc })}</div>`).window.document;
  const more = d.querySelector('details.name-list-more');
  assert.ok(more && /Show all/.test(more.textContent), 'long list behind Show all');
  for (const tk of many) assert.ok(d.body.textContent.includes(tk), tk);
});

test('I6 follow-up: the chase table scroll box is a focusable, labelled region (axe scrollable-region-focusable)', () => {
  const html = app.renderPicksBuckets([{ ticker: 'ACN', trigger: 'gap', move_pct: 17.78, threshold_pct: 5, bar_date: '2026-10-01' }], [], []);
  const d = new JSDOM(`<div>${html}</div>`).window.document;
  const box = d.querySelector('.bucket-card.chase .table-scroll');
  assert.equal(box.getAttribute('tabindex'), '0');
  assert.equal(box.getAttribute('role'), 'region');
  assert.ok(box.getAttribute('aria-label'));
});

// ── P2 ──────────────────────────────────────────────────────────────────────

test('P2: the banner names every ticker the event check could not read, with the reason and action', () => {
  const fix = 'Not funded this scan. To check these names, raise EVENTS.FILING_PAGES_MAX so more of their filing history is read';
  const ev = { checked: 100, clear: 94, flagged: 1, unavailable: 5, contact: { state: 'configured' },
    reasons: [{ reason: 'coverage_short', label: 'filing history longer than the pages read', fix, count: 5,
      tickers: ['JPM', 'GS', 'MS', 'C', 'BAC'], book_tickers: [] }] };
  const t = text(app.eventCheckBannerHtml({ event_check: ev }));
  assert.match(t, /filing history longer than the pages read · 5 names: JPM, GS, MS, C, BAC\. Fix: Not funded this scan/);
  assert.doesNotMatch(t, /None until/);
});

// ── P3 ──────────────────────────────────────────────────────────────────────

test('P3: names that passed the rules but were not funded are listed with one plain reason', () => {
  const rows = [
    { ticker: 'MSFT', company: 'Microsoft', price: 517.53, reason: 'one_share_over_cap',
      text: 'One share costs $517.53, 20.7% of capital: over the 18% position cap or the per-trade risk ceiling', capital_needed: 2900 },
    { ticker: 'GOOGL', company: 'Alphabet', price: 245.1, reason: 'same_issuer', text: 'Same company as GOOG, already in the book' }];
  const html = app.passedNotFundedHtml({ passed_not_funded: rows, currency: '$' });
  const d = new JSDOM(`<div>${html}</div>`).window.document;
  const t = d.body.textContent.replace(/\s+/g, ' ');
  assert.match(t, /Passed the rules, no position in this book \(2\)/);
  assert.match(t, /MSFT .*One share costs \$517\.53, 20\.7% of capital.*Capital needed \$2,900/);
  assert.match(t, /GOOGL .*Same company as GOOG, already in the book/);
  assert.doesNotMatch(t, /GOOGL[^·]*Capital needed/);
  assert.equal(app.passedNotFundedHtml({}), '');
});

test('P3: the list is on both the funded and the empty result', () => {
  const pnf = [{ ticker: 'MSFT', reason: 'one_share_over_cap', text: 'One share costs $517.53', capital_needed: 2900 }];
  renderEmpty({ scan_status: 'no_picks', recommendations: [], passed_not_funded: pnf,
    accounting: { version: 1, funded: 0, watch: 0, universe: 1, could_not_analyse: { count: 0 } } });
  assert.ok(document.querySelector('#picks-results [data-passed-not-funded]'));
});

// ── P4 ──────────────────────────────────────────────────────────────────────

test('P4: book risk shows percent of total capital first, deployed second, coloured only past config limits', () => {
  const pro = require('../../static/elite-pro.js');
  const amzn = { ticker: 'AMZN', sector: 'Consumer Cyclical', is_funded: true, final_action: 'ACCUMULATE',
    allocation: 251.52, allocation_after: 251.52, shares: 1, entry_price: 251.52, stop_loss: 239.0, risk_grade: 'B' };
  const data = { capital: 2500, currency: '$', recommendations: [amzn],
    book_limits: { position_pct: 0.1833, sector_pct: 0.35, stop_risk_pct: 0.06 } };
  const d = new JSDOM(`<div>${pro.portfolioRiskCard(data)}</div>`).window.document;
  const cells = [...d.querySelectorAll('.qpro-risk-cell')].map((c) => ({
    t: c.textContent.replace(/\s+/g, ' ').trim(), cls: c.querySelector('.qpro-risk-val')?.className || '' }));
  const largest = cells.find((c) => /Largest position/.test(c.t));
  assert.match(largest.t, /Largest position 10\.1% of capital AMZN · 100\.0% of deployed · limit 18\.3%/);
  assert.doesNotMatch(largest.cls, /bear|warn/);
  const sector = cells.find((c) => /Top sector/.test(c.t));
  assert.match(sector.t, /10\.1% of capital/);
  assert.doesNotMatch(sector.cls, /bear|warn/);
  amzn.allocation = amzn.allocation_after = 520;
  const d2 = new JSDOM(`<div>${pro.portfolioRiskCard(data)}</div>`).window.document;
  assert.match(d2.querySelector('.qpro-risk-cell .qpro-risk-val').className, /bear/, '20.8% is over the 18.3% cap');
});

// ── P5 ──────────────────────────────────────────────────────────────────────

test('P5: the card shows the spot price, session and source beside the levels, and the sizing price', () => {
  const rec = { ticker: 'AMZN', company: 'Amazon', sector: 'Consumer Cyclical', signal: 'ACCUMULATE', final_action: 'ACCUMULATE',
    display_action: 'ACCUMULATE', is_funded: true, allocation: 252.02, allocation_after: 252.02, shares: 1,
    entry_price: 252.02, stop_loss: 241.86, price_target: 271.67, risk_reward_ratio: 1.85, overall_score: 6.4,
    quote_time: '2026-10-02T20:00:01+00:00',
    why: 'Score 64 of 100 is in the Accumulate band (55 to 65); it passed every screening and risk rule',
    final_action_reasons: ['Composite score 63.8 → candidate ACCUMULATE'],
    binding_levels: { source: 'research', label: 'Research levels (binding)', entry: 252.02, entry_zone_low: 246.39,
      entry_zone_high: 252.02, stop: 241.86, target: 271.67, risk_reward: 1.85, horizon_days: 63,
      price_basis: { price: 252.02, session: '2026-10-02', fetch: 'Research' } },
    scanner_levels: { source: 'scanner', entry: 251.52, stop: 236.03, target: 292.97, risk_reward: 2.58,
      price_basis: { price: 251.52, session: '2026-10-02', fetch: 'scan' } },
    decision: { label: 'Accumulate', as_of: '2026-10-02', sources: [{ kind: 'price', provider: 'yfinance' }] } };
  const d = new JSDOM(`<div>${app.pickCard(rec, '$', 0, { capital: 2500 })}</div>`).window.document;
  const levels = d.querySelector('.pd-levels').textContent.replace(/\s+/g, ' ');
  assert.match(levels, /Spot \$252\.02 · close of 2026-10-02 · yfinance · Research fetch/);
  assert.match(d.querySelector('.pd-alloc').textContent.replace(/\s+/g, ' '), /1 share at \$252\.02 \(binding entry\)/);
  assert.match(d.querySelector('[data-scanner-levels]').textContent.replace(/\s+/g, ' '),
    /built on the scan fetch's close \$251\.52 of 2026-10-02/);
  const why = d.querySelector('.pd-why, [data-why]') || d.body;
  assert.doesNotMatch(why.textContent.split('Detail')[0], /→/, 'no arrow trace in the decision row');
});

// ── P6 ──────────────────────────────────────────────────────────────────────

test('P6: the guide states one chase rule per stock, and the volatility widening from config', () => {
  const T = globalThis.__QUANTA_THRESHOLDS__;
  app.applyThresholds(document);
  const el = document.querySelector('#hw-effective-rules');
  let t = el.textContent.replace(/\s+/g, ' ');
  assert.doesNotMatch(t, /growth scans|\(growth:/i);
  assert.match(t, /Wider thresholds for volatile stocks ?Off: every stock uses the thresholds above in every scan\./);
  const saved = T.CHASE.WIDE_THRESHOLD_MIN_VOL;
  try {
    T.CHASE.WIDE_THRESHOLD_MIN_VOL = 0.35;
    app.applyThresholds(document);
    t = el.textContent.replace(/\s+/g, ' ');
    assert.match(t, new RegExp(`On for a stock whose ${T.CHASE.VOL_LOOKBACK_BARS}-session volatility is at least 35%, in any scan: gap 5%, runs 15% and 30%, penalty 0\.75`));
  } finally { T.CHASE.WIDE_THRESHOLD_MIN_VOL = saved; app.applyThresholds(document); }
});

test('P6: on a funded page the chase alerts are reachable: a count and a collapsed list outside the audit', () => {
  const chase = Array.from({ length: 20 }, (_, i) => ({ ticker: `C${i}`, trigger: 'gap', move_pct: 4, threshold_pct: 3, bar_date: '2026-10-02', active: true }));
  const funded = { ticker: 'AMZN', company: 'Amazon', sector: 'Consumer Cyclical', signal: 'ACCUMULATE', final_action: 'ACCUMULATE',
    display_action: 'ACCUMULATE', is_funded: true, allocation: 252, allocation_after: 252, shares: 1,
    entry_price: 252, stop_loss: 242, price_target: 272, risk_reward_ratio: 1.85, overall_score: 6.4 };
  app.renderPicks({ scan_status: 'success', currency: '$', capital: 2500, max_positions: 5, regime: { type: 'BULL' },
    risk_profile: {}, recommendations: [funded], funded_count: 1, watch_count: 0, chase_alerts: chase, early_momentum: [] });
  const box = document.querySelector('#picks-results [data-side-buckets]');
  assert.ok(box && box.tagName === 'DETAILS', 'a collapsed section of its own');
  assert.equal(box.open, false);
  assert.match(box.querySelector('summary').textContent, /Chase alerts \(20\)/);
  assert.equal(box.closest('.portfolio-diagnostics'), null, 'not buried in the audit');
  assert.equal(box.querySelectorAll('[data-chase]').length, 20);
});

// ── P7 ──────────────────────────────────────────────────────────────────────

test('P7: the pre-trade review states the engine rules from config and labels the rest a manual checklist', () => {
  const pro = require('../../static/elite-pro.js');
  const T = globalThis.__QUANTA_THRESHOLDS__;
  const S = T.SIZING;
  const data = { regime: { type: 'BULL', vix: 15.31 }, recommendations: [{ ticker: 'AMZN', is_funded: true, final_action: 'ACCUMULATE', allocation: 252, allocation_after: 252 }] };
  const d = new JSDOM(`<div>${pro.executionCaveatsCard(data)}</div>`).window.document;
  const t = d.body.textContent.replace(/\s+/g, ' ');
  const p = (v) => `${Math.round(v * 100)}%`;
  assert.match(t, new RegExp(`Earnings within ${S.EARNINGS_IMMINENT_DAYS} days: ${p(S.EARNINGS_IMMINENT_SIZE_MULT)} of the planned size; within ${S.EARNINGS_NEAR_DAYS} days: ${p(S.EARNINGS_NEAR_SIZE_MULT)}; within ${S.EARNINGS_ELEVATED_DAYS} days: ${p(S.EARNINGS_ELEVATED_SIZE_MULT)}; date unknown: ${p(S.EARNINGS_UNKNOWN_SIZE_MULT)}`));
  assert.match(t, /Applied by the scan/);
  assert.match(t, /Manual checklist at execution \(QUANTA does not check these\)/);
  assert.match(t, new RegExp(`VIX 15\.3 now; above ${T.REGIME.VIX_VOLATILE}`));
  assert.doesNotMatch(t, /1\/3 size|≤ 5 days|5 days/);
  assert.match(t, new RegExp(`Pre-market gap above ${p(T.CHECKLIST.PREMARKET_GAP_PCT)}`));
});

test('P7 follow-up: a caveat row has two columns (label, rule), not the old three-column grid', () => {
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  const m = css.match(/\.qpro-caveat-row \{[^}]*grid-template-columns: ([^;]+);/);
  assert.ok(m, 'caveat row grid');
  assert.equal(m[1].trim().split(/\s+(?![^(]*\))/).length, 2, m[1]);
});
