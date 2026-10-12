/**
 * Audit U1 (F1 on the page): a name the corporate-event gate rejected is shown
 * as Avoid, with the reason built from its own SEC filing, instead of
 * disappearing from Picks. Synthetic names only.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/event_rejections.test.cjs
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

const rejected = [{
  ticker: 'SYNT', company: 'Synthetic Target Co', sector: 'Health Care', price: 39.35, final_action: 'AVOID',
  code: 'PENDING_ACQUISITION',
  reason: 'Pending cash acquisition at $40.00 (merger agreement 2026-08-03; DEFM14A filed 2026-09-08 on SEC EDGAR).',
  event_check: { status: 'flagged', code: 'PENDING_ACQUISITION', url: 'https://www.sec.gov/Archives/edgar/data/1/0001/x.htm' },
}, {
  ticker: 'SYNX', company: '<img src=x onerror=alert(1)>', final_action: 'AVOID', code: 'DELISTING',
  reason: 'Delisting of <b>Common Stock</b> filed with the SEC.', event_check: { url: 'javascript:alert(1)' },
}];

function scan(over) {
  return Object.assign({ scan_status: 'success', total_scanned: 250, currency: '$', capital: 2500, max_positions: 5,
    regime: { type: 'BULL', vix: 15, volatility: 'NORMAL', exposure: 100 }, risk_profile: {},
    recommendations: [{ ticker: 'SYNA', company: 'Synthetic Alpha', sector: 'Industrials', signal: 'HOLD',
      final_action: 'WATCHLIST', display_action: 'WATCHLIST', overall_score: 6, conviction: 3, entry_price: 50,
      stop_loss: 46, price_target: 57, risk_reward_ratio: 1.71, allocation: 0, shares: 0, is_funded: false }],
    rejected,
    deep_confirmation: { enabled: true, records: [], downgrades: [], not_confirmed: [], rejected: [] },
    chase_alerts: [], early_momentum: [], error_summary: { records: [] }, funnel: { universe_size: 250, gate_stats: {} } }, over);
}

test('an event-rejected name reads Avoid with its filing reason and source', () => {
  app.renderPicks(scan({}));
  const row = document.querySelector('#picks-results [data-event-reject="SYNT"]');
  assert.ok(row, 'the rejected name is on the page');
  assert.match(row.textContent, /Avoid/);
  assert.match(row.textContent, /Pending cash acquisition at \$40\.00/);
  assert.equal(row.querySelector('a').getAttribute('href'), rejected[0].event_check.url);
  assert.match(document.querySelector('#event-rejections-h').textContent, /\(2\)/);
});

test('the list also shows when nothing was published, and escapes everything', () => {
  global.window = {};            // the empty state looks for the optional Pro layer on window
  try { app.renderPicks(scan({ scan_status: 'no_picks', recommendations: [] })); } finally { global.window = undefined; }
  const row = document.querySelector('#picks-results [data-event-reject="SYNX"]');
  assert.ok(row, 'shown in the no-picks state too');
  assert.equal(row.querySelector('img'), null, 'company text is escaped');
  assert.equal(row.querySelector('b'), null, 'reason text is escaped');
  assert.equal(row.querySelector('a'), null, 'only sec.gov links are kept');
});

test('no rejected names, no section', () => {
  app.renderPicks(scan({ rejected: [] }));
  assert.equal(document.querySelector('#picks-results .event-rejections'), null);
});
