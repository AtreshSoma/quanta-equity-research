/**
 * A01 on the page: one coverage line states how the universe was chosen
 * (method, size, time taken and, for a fallback, why). Synthetic values only.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/coverage_line.test.cjs
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

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

test('a liquidity-ranked universe says so with its size and time', () => {
  const t = text(app.coverageLineHtml({ universe_selection: { method: 'liquidity_ranked', candidates: 503, selected: 100, seconds: 12.34, budget_seconds: 90 } }));
  assert.match(t, /Universe: top 100 of 503 names, ranked by average daily traded value; ranked in 12\.3 s/);
});

test('a fallback names the method and the cause', () => {
  const t = text(app.coverageLineHtml({ universe_selection: { method: 'even_spread', candidates: 503, selected: 100, seconds: 90.2, budget_seconds: 90, cause: 'liquidity download exceeded its 90s budget' } }));
  assert.match(t, /Universe: fallback: an even spread of 100 of 503 names, not liquidity-ranked: liquidity download exceeded its 90s budget \(stopped after 90\.2 s\)/);
});

test('custom and listed universes, and nothing when the record is absent', () => {
  assert.match(text(app.coverageLineHtml({ universe_selection: { method: 'custom_list', selected: 12 } })), /Universe: your list of 12 names/);
  assert.match(text(app.coverageLineHtml({ universe_selection: { method: 'listed_order', candidates: 900, selected: 80 } })), /Universe: 80 of 900 names, index names first, then the rest of the list/);
  assert.equal(app.coverageLineHtml({}), '');
});

test('A08: the effective-rule view states chase and India rules from config', () => {
  app.applyThresholds(document);
  const el = document.querySelector('#hw-effective-rules');
  assert.ok(el, 'effective rules present');
  const t = el.textContent.replace(/\s+/g, ' ');
  assert.doesNotMatch(t, /n\/a/);
  const T = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'thresholds.json'), 'utf8'));
  assert.match(t, new RegExp(`more than ${+(T.CHASE.GAP_PCT * 100).toFixed(2)}% on either of the last ${T.CHASE.GAP_LOOKBACK_BARS} sessions`));
  assert.match(t, new RegExp(`market cap at least ₹${(T.UNIVERSE.INDIA_MIN_MARKET_CAP / 1e7).toLocaleString('en-IN')} crore`));
  assert.match(document.querySelector('.hw-tabs-dl').textContent.replace(/\s+/g, ' '), new RegExp(`at least ${T.CHASE.EARLY_MOMENTUM_MIN_SIGNALS} of six checks`));
});

test('Picks revamp: the selection names floors, names not analysed, the style screen and the snapshot fallback', () => {
  const sel = { scan_type: 'focused_momentum', method: 'liquidity_ranked', candidates: 503, selected: 150, passed_floors: 410,
    rank_by: '20-day average daily traded value', pool_label: 'S&P 500 names', universe_total: 503,
    index_source: 'snapshot', index_as_of: '2026-10-05', index_cause: 'the live S&P 500 list could not be read' };
  assert.equal(app.selectionText(sel), 'top 150 of 503 S&P 500 names, ranked by 20-day average daily traded value; '
    + '410 passed the price and volume floors, 260 of them not analysed; the uptrend screen then runs on these 150 '
    + '(index list from the 2026-10-05 snapshot: the live S&P 500 list could not be read)');
  assert.equal(app.selectionText(null), '');
});

test('Picks revamp: the progress panel shows the selection before analysis and keeps it on the finished row', () => {
  const sel = { method: 'even_spread', candidates: 806, selected: 250, cause: 'liquidity download exceeded its 90s budget' };
  app.renderScanStages({ key: 'selecting', label: 'Choosing the universe', done: 250, total: 806, selection: sel });
  const rows = () => [...document.querySelectorAll('#sp-stages li')].map((li) => `${li.dataset.state}|${li.textContent}`);
  assert.match(rows()[0], /^current\|Choosing the universe: Fallback: an even spread of 250 of 806 names, not liquidity-ranked: liquidity download exceeded/);
  app.renderScanStages({ key: 'analysing', label: 'Analysing names', done: 79, total: 250, selection: sel });
  assert.match(rows()[0], /^done\|Choosing the universe: Fallback: an even spread of 250 of 806/);
  assert.match(rows()[1], /^current\|Analysing names: 79 of 250$/);
  app.renderScanStages({ key: 'complete', terminal: true, stages: [
    { key: 'selecting', label: 'Universe', state: 'done', done: 250, total: 806, note: '', selection: sel },
    { key: 'analysing', label: 'Analysis', state: 'done', done: 248, total: 250, note: '' }] });
  assert.match(rows()[0], /^done\|Universe: Fallback: an even spread of 250 of 806/);
  assert.match(rows()[1], /^done\|Analysis: 248 of 250$/);
});

test('decision (a): Momentum names the uptrend screen that ran before the cap', () => {
  const sel = { scan_type: 'focused_momentum', method: 'liquidity_ranked', candidates: 503, selected: 150, passed_floors: 480,
    screened_before_cap: 'momentum', passed_screen: 212, rank_by: '20-day average daily traded value', pool_label: 'S&P 500 names' };
  assert.equal(app.selectionText(sel), 'top 150 of 503 S&P 500 names, ranked by 20-day average daily traded value; '
    + '480 passed the price and volume floors and 212 the uptrend screen, 62 of them not analysed');
});

test('B-87: an unreadable list says so; no curated or built-in list is named', () => {
  assert.equal(app.selectionText({ method: 'none', selected: 0, candidates: 0, cause: 'no index constituent list could be read and no snapshot was found' }),
    'no names: no index constituent list could be read and no snapshot was found');
  const etf = app.selectionText({ method: 'etf_directory', selected: 40, candidates: 40, pool_label: 'US-listed ETFs' });
  assert.match(etf, /^all 40 US-listed ETFs in the exchange symbol directory/);
  const down = app.selectionText({ method: 'index_list', selected: 0, candidates: 0, pool_label: 'S&P 500 names', index_source: 'none', index_cause: 'the live list could not be read (timeout)' });
  assert.match(down, /\(index list unavailable: the live list could not be read \(timeout\)\)$/);
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
  assert.doesNotMatch(src, /curated (seed )?list|built-in index list|sector registry/);
});
