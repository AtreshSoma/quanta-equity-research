/**
 * Picks says when a scan's result cannot be trusted, and why (2026-11-09).
 *
 * A "250 most liquid" scan read an even spread of 1,517 names (pyarrow missing)
 * with no SEC contact set, and the page said "No Buy or Accumulate picks right
 * now" with "Top reasons: Hold signal ...", as if that were a read on the market.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/picks_reliability.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');

const frag = (h) => { const el = document.createElement('div'); el.innerHTML = h; return el; };
const FIX = 'Set QUANTA_SEC_CONTACT to your email address in the repository .env';
const contactMissing = { checked: 250, clear: 0, unavailable: 250,
  reasons: [{ reason: 'contact_missing', label: 'no SEC contact configured', fix: FIX, count: 250, book_tickers: ['C'] }] };
const fallback = { scan_type: 'standard', method: 'even_spread', candidates: 1517, selected: 250,
  cause: "liquidity download failed: ImportError: Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'.\nA suitable version of pyarrow or fastparquet is required for parquet support" };
const ranked = { scan_type: 'standard', method: 'liquidity_ranked', candidates: 1517, selected: 250, cause: null };

test('a universe that fell back from the liquidity ranking is named, with the cause in plain words', () => {
  const fb = app.pkUniverseFallback({ universe_selection: fallback });
  assert.equal(fb.selected, 250);
  assert.equal(fb.candidates, 1517);
  assert.ok(fb.missingEngine);
  assert.doesNotMatch(fb.cause, /ImportError|fastparquet/, 'no stack-trace text reaches the page');
  assert.equal(app.pkUniverseFallback({ universe_selection: ranked }), null);
  assert.equal(app.pkUniverseFallback({}), null);
  // An even spread chosen on purpose (no cause) is not a failure.
  assert.equal(app.pkUniverseFallback({ universe_selection: { method: 'even_spread', selected: 50 } }), null);
});

test('a timeout fallback keeps its own cause', () => {
  const fb = app.pkUniverseFallback({ universe_selection: { method: 'even_spread', selected: 250, candidates: 1517, cause: 'liquidity download exceeded its 90s budget' } });
  assert.equal(fb.missingEngine, false);
  assert.match(fb.cause, /exceeded its 90s budget/);
});

test('a missing SEC contact is one alert that says no Buy was possible, with the fix', () => {
  assert.deepEqual(app.pkContactBlock({ event_check: contactMissing }), { label: 'no SEC contact configured', fix: FIX });
  assert.equal(app.pkContactBlock({ event_check: { reasons: [{ reason: 'timeout', count: 2, book_tickers: ['A'] }] } }), null);
  const el = frag(app.pkAlertHtml({ event_check: contactMissing, universe_selection: ranked }));
  const alerts = el.querySelectorAll('[data-pk-alert]');
  assert.equal(alerts.length, 1, 'the contact alert replaces the per-name one');
  assert.match(alerts[0].textContent, /No Buy call was possible in this scan/);
  assert.match(alerts[0].textContent, /QUANTA_SEC_CONTACT/);
});

test('both problems show, the universe first', () => {
  const el = frag(app.pkAlertHtml({ event_check: contactMissing, universe_selection: fallback }));
  const kinds = [...el.querySelectorAll('[data-pk-alert]')].map((a) => a.getAttribute('data-pk-alert'));
  assert.deepEqual(kinds, ['universe', 'contact']);
  assert.match(el.textContent, /did not read the most liquid names/);
  assert.match(el.textContent, /even spread of 250 of 1,517 names/);
  assert.match(el.textContent, /start\.sh --reinstall/);
});

test('names one event check could not read: singular and plural agree, and the pointer names a real tab', () => {
  const one = frag(app.pkAlertHtml({ event_check: { reasons: [{ reason: 'timeout', count: 1, book_tickers: ['C'] }] } })).textContent;
  assert.match(one, /could not read 1 name in the book \(C\); it stays on the Watchlist/);
  assert.match(one, /Method tab/);
  const two = frag(app.pkAlertHtml({ event_check: { reasons: [{ reason: 'timeout', count: 2, book_tickers: ['C', 'JPM'] }] } })).textContent;
  assert.match(two, /2 names in the book \(C, JPM\); they stay/);
});

test('a clean scan shows no alert', () => {
  assert.equal(app.pkAlertHtml({ event_check: { checked: 250, clear: 250, reasons: [] }, universe_selection: ranked }), '');
});

test('an empty book from a setup problem is not presented as a market verdict', () => {
  const data = { event_check: contactMissing, universe_selection: fallback,
    accounting: { reached_book: 1, rule_rejected: { by_reason: { signal_not_actionable: 58, score_below_min: 38 } } } };
  const el = frag(app.pkEmptyHtml(data, []));
  assert.match(el.querySelector('[data-result-heading]').textContent, /could not make a Buy call/);
  assert.match(el.querySelector('[data-pk-blocked]').textContent, /setup problem, not a read on the market/);
  // A genuine empty book keeps the plain verdict.
  const plain = frag(app.pkEmptyHtml({ event_check: { reasons: [] }, universe_selection: ranked, accounting: data.accounting }, []));
  assert.match(plain.querySelector('[data-result-heading]').textContent, /No Buy or Accumulate picks right now/);
  assert.equal(plain.querySelector('[data-pk-blocked]'), null);
});

test('levels the Research check never replaced are marked on the row', () => {
  const row = { ticker: 'C', status: 'watch', price: 121.97, entry: 121.97, stop: 111.56, target: 141.68, upside_pct: 16.16 };
  const unchecked = frag(app.pkLevelsCellHtml({ ...row, levels_label: 'Scanner levels (Research check not run)' }, '$'));
  assert.ok(unchecked.querySelector('[data-levels-unchecked]'));
  const checked = frag(app.pkLevelsCellHtml({ ...row, levels_label: 'Research levels (binding)' }, '$'));
  assert.equal(checked.querySelector('[data-levels-unchecked]'), null);
});

test('before a scan: a server that cannot rank by liquidity says so, ahead of the SEC notice', () => {
  const n = app.universeRankingNotice({ universe_ranking: { ok: false, label: 'the pyarrow package is not installed', fix: 'Run ./start.sh --reinstall' } });
  assert.match(n.text, /cannot rank by liquidity/);
  assert.match(n.more, /start\.sh --reinstall/);
  assert.equal(app.universeRankingNotice({ universe_ranking: { ok: true, engine: 'pyarrow' } }), null);
  assert.equal(app.universeRankingNotice({}), null);
});

test('the Standard universe is described once', () => {
  assert.doesNotMatch(html, /full universe \(recommended\)/);
});

const nearMisses = [
  { ticker: 'ETSY', reason: 'risk_reward_low', score: 8.12, detail: 'After-cost R:R 0.52:1 below 1.80:1', rr: 0.52, needed: 1.8, entry: 60, stop: 55, target: 64, entry_needed: 56.9, target_needed: 69.1 },
  { ticker: 'EOG', reason: 'risk_reward_low', score: 8.11, detail: 'After-cost R:R 1.46:1 below 1.80:1', rr: 1.46, needed: 1.8, entry_needed: 118.2, target_needed: 131.4 },
  { ticker: 'SN', reason: 'signal_not_actionable', score: 7.11, detail: 'HOLD: RSI 74.6 exceeds the entry limit 70; wait for a pullback' },
  ...['A', 'B', 'C', 'D'].map((t) => ({ ticker: t, reason: 'signal_not_actionable', score: 6, detail: 'HOLD: score 6.0 below the entry floor 6.5' })),
];

test('names ranked high but stopped by one rule are a table with what clears them, never picks', () => {
  const el = frag(app.pkNearMissHtml({ near_misses: nearMisses }, '$'));
  const sec = el.querySelector('[data-pk-near-miss]');
  assert.ok(sec);
  assert.match(sec.querySelector('.pk-nm-note').textContent, /Not picks/);
  assert.deepEqual([...sec.querySelectorAll('thead th')].map((th) => th.textContent.replace(/\s+/g, ' ').trim()),
    ['Stock', 'Score', 'Price', 'Reward/risk vs 1.80:1', 'Buy at or below', 'Or a target of']);
  const rows = [...sec.querySelectorAll('tbody tr')];
  assert.deepEqual(rows.map((r) => r.getAttribute('data-pk-nm')), ['ETSY', 'EOG', 'SN', 'A', 'B', 'C', 'D']);
  const shown = Number(globalThis.__QUANTA_THRESHOLDS__.UI.NEAR_MISS_SHOWN);
  assert.equal(rows.filter((r) => !r.hidden).length, shown, 'the rest fold behind Show all');
  assert.match(sec.querySelector('[data-pk-nm-toggle]').textContent, /Show all 7/);
  const etsy = [...rows[0].querySelectorAll('td')].map((td) => td.textContent.replace(/\s+/g, ' ').trim());
  assert.equal(etsy[0], 'ETSY');
  assert.equal(etsy[1], '81');
  assert.equal(etsy[2], '$60.00');
  assert.equal(etsy[3], '0.52:1');
  assert.equal(etsy[4], '$56.90-5.2%');
  assert.equal(etsy[5], '$69.10+8.0% vs $64.00');
  assert.match(rows[0].querySelector('.pk-nm-meter').getAttribute('aria-label'), /0\.52:1 of the 1\.80:1 needed/);
  // Another rule spans the price columns, without the engine's prefix.
  const sn = rows[2].querySelectorAll('td');
  assert.equal(sn.length, 3);
  assert.equal(sn[2].getAttribute('colspan'), '4');
  assert.match(sn[2].textContent, /^RSI 74\.6 exceeds the entry limit 70; wait for a pullback$/);
  assert.ok(rows[0].querySelector('.pk-open[data-ticker="ETSY"]'), 'each opens Research');
  assert.ok(sec.querySelector('[role="region"][tabindex="0"]'), 'a wide table scrolls inside a labelled region');
  assert.equal(app.pkNearMissHtml({ near_misses: [] }, '$'), '');
  assert.equal(app.pkNearMissHtml({}, '$'), '');
});

test('the header says when the daily bars trail the last close', () => {
  const base = { scan_type: 'standard', timestamp: '2026-11-09T20:48:06-05:00', market: 'US' };
  const behind = frag(app.pkHeadHtml({ ...base, data_dates: { bar_session: '2026-11-06', bar_lag_sessions: 1 } }, [], '$'));
  assert.match(behind.textContent, /Daily bars to Nov 6 \(1 session behind\)/);
  assert.ok(behind.querySelector('.pk-bars.is-behind'));
  const current = frag(app.pkHeadHtml({ ...base, data_dates: { bar_session: '2026-11-09', bar_lag_sessions: 0 } }, [], '$'));
  assert.match(current.textContent, /Daily bars to Nov 9/);
  assert.doesNotMatch(current.textContent, /behind/);
});

test('a saved scan whose setup problems are fixed drops those warnings without a new notice', () => {
  const data = { event_check: contactMissing, universe_selection: fallback,
    accounting: { reached_book: 1, rule_rejected: { by_reason: { signal_not_actionable: 58 } } } };
  const fixedHealth = { universe_ranking: { ok: true, engine: 'pyarrow' }, sec_contact: { state: 'configured' } };
  assert.equal(app.pkFixedSince(data, fixedHealth, true).length, 2);
  assert.equal(app.pkAlertHtml(data, fixedHealth, true), '', 'nothing above the report once both are fixed');
  // Only what is fixed is dropped.
  const half = frag(app.pkAlertHtml(data, { universe_ranking: { ok: true }, sec_contact: { state: 'missing' } }, true));
  assert.deepEqual([...half.querySelectorAll('[data-pk-alert]')].map((a) => a.getAttribute('data-pk-alert')), ['contact']);
  // A fresh scan (not saved) or an unknown server state keeps the scan's own warnings.
  assert.equal(app.pkFixedSince(data, fixedHealth, false).length, 0);
  assert.equal(app.pkFixedSince(data, null, true).length, 0);
  assert.deepEqual([...frag(app.pkAlertHtml(data, null, false)).querySelectorAll('[data-pk-alert]')].map((a) => a.getAttribute('data-pk-alert')), ['universe', 'contact']);
});
