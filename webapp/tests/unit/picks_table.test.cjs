/**
 * Picks: QUANTA's calls (Buy, Accumulate, Watchlist) in one ranked table, an
 * analysis per call, and the names that are not picks under Method.
 * Synthetic payloads shaped like the worker's result after _picks_view.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/picks_table.test.cjs
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
const { base, ROWS, payload, CASES } = require('../fixtures/picks_calls.cjs');

const T = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
const render = (data) => {
  global.window = {};
  try { app.renderPicks(data); } finally { global.window = undefined; }
  return document.querySelector('#picks-results');
};
const panel = (box, k) => box.querySelector(`[data-pk-panel="${k}"]`);

test('one ranked table of calls: Buy, then Accumulate, then Watchlist', () => {
  const box = render(payload());
  const view = box.querySelector('[data-picks-v2]');
  assert.ok(view, 'the redesigned view renders when the payload carries stock_picks');
  const heads = [...view.querySelectorAll('table.pk-table thead th')].map((th) => T(th));
  assert.deepEqual(heads.slice(0, 8), ['#', 'Stock', 'Price', 'Call', 'Levels', 'R:R', 'Score', 'Your capital']);
  const rows = [...view.querySelectorAll('tbody tr.pk-row')];
  assert.deepEqual(rows.map((r) => r.dataset.pkTicker), ['AMZN', 'MSFT', 'CSCO', 'JPM']);
  assert.deepEqual(rows.map((r) => T(r.querySelector('.pk-c-signal .pk-signal'))), ['Buy', 'Accumulate', 'Watchlist', 'Watchlist']);
});

test('each row states its call, labelled levels, score bar and capital fit', () => {
  const rows = ROWS();
  rows[0].setup = 'Growth';
  const box = render(payload({ stock_picks: rows }));
  const row = (t) => box.querySelector(`tr.pk-row[data-pk-ticker="${t}"]`);
  assert.match(T(row('AMZN')), /AMZN ?AMZN Inc\. \$252\.65 Buy ?Growth/);
  const lv = row('AMZN').querySelector('.pk-c-levels');
  assert.equal(T(lv.querySelector('.pk-lv-labels')), 'Stop $244.51Target $272.14 +7.7%');
  assert.equal(T(lv.querySelector('.pk-lv-entry')), 'Entry $247.22-$252.65');
  assert.equal(lv.querySelector('.pk-lv-bar').getAttribute('aria-hidden'), 'true', 'the bar is decoration; the labels carry the values');
  const sb = row('AMZN').querySelector('.pk-c-score');
  assert.equal(T(sb.querySelector('b')), '69', 'D3: scores read out of 100');
  assert.equal(sb.querySelector('.pk-sb-track span').style.width, '69%');
  assert.match(T(row('AMZN').querySelector('.pk-c-pos')), /^\$253 ?1 share$/);
  assert.equal(T(row('MSFT').querySelector('.pk-c-pos')), 'Needs about $2,900 for one share within the position cap');
  // A Watchlist row carries no capital line; its group row says it once.
  const none = row('CSCO').querySelector('.pk-c-pos .pk-na');
  assert.equal(none.getAttribute('aria-label'), 'No new money on a Watchlist call');
  assert.match(T(box.querySelector('tr.pk-grp[data-pk-grp="watchlist"]')), /^Watchlist ?2 ?No new money on these calls\./);
  assert.ok(row('AMZN').classList.contains('is-sized'));
  assert.equal(box.querySelectorAll('tr.pk-row .pk-stars').length, 0, 'conviction lives in the analysis, not the table');
  assert.doesNotMatch(box.querySelector('[data-picks-v2]').textContent, /[–—]/, 'no en or em dashes in the report');
});

test('the header counts the calls; the Overview cards hold the figures, once each', () => {
  const box = render(payload());
  const head = box.querySelector('[data-picks-head]');
  assert.equal(T(head.querySelector('[data-result-heading]')), '2 Buy or Accumulate, 2 Watchlist, from 100 scanned');
  const meta = [...head.querySelectorAll('.pk-meta li')].map((li) => T(li));
  assert.equal(meta[0], 'Quick scan');
  assert.deepEqual(meta.slice(2), ['US', 'Balanced mandate', 'Up to 5 positions']);
  // Three figures with one job each; the call counts live in the header only.
  const cards = [...box.querySelectorAll('[data-pk-summary] .pk-sc')];
  assert.deepEqual(cards.map((c) => T(c.querySelector('dt'))), ['Scan to calls', 'Suggested deployment', 'Regime']);
  assert.equal(cards[0].querySelector('.pk-fun').getAttribute('aria-label'), '100 scanned, 4 ranked, 1 sized');
  assert.equal(T(cards[0].querySelector('dd > span')), '95 screened out, 1 excluded for safety');
  assert.equal(T(cards[1].querySelector('dd b')), '$253');
  assert.match(T(cards[1].querySelector('dd > span:last-child')), /^10\.1% of \$2,500/);
  assert.equal(cards[1].querySelector('.pk-meter-fill').style.width, '10.1%');
  assert.deepEqual([T(cards[2].querySelector('dd b')), T(cards[2].querySelector('dd span'))], ['Bull', 'VIX 15.6']);
  const all = head.textContent + panel(box, 'overview').textContent;
  for (const once of ['10.1%', 'from 100 scanned', 'VIX 15.6', 'Model not yet validated']) {
    assert.equal(all.split(once).length - 1, 1, `${once} appears once`);
  }
});

test('the call chips filter the table; the note states the order', () => {
  const box = render(payload());
  const order = () => [...box.querySelectorAll('tbody tr.pk-row')].filter((r) => !r.hidden).map((r) => r.dataset.pkTicker);
  const chips = [...box.querySelectorAll('[data-pk-filter]')];
  assert.deepEqual(chips.map((c) => T(c)), ['All', 'Buy', 'Accumulate', 'Watchlist']);
  chips[3].click();
  assert.deepEqual(order(), ['CSCO', 'JPM']);
  assert.equal(chips[3].getAttribute('aria-pressed'), 'true');
  assert.equal(chips[0].getAttribute('aria-pressed'), 'false');
  chips[0].click();
  assert.equal(order().length, 4);
  // The order is shown, not explained: a group row opens each call kind.
  const heads = () => [...box.querySelectorAll('tbody tr.pk-grp')].filter((g) => !g.hidden).map((g) => T(g.querySelector('.pk-grp-l')));
  assert.deepEqual(heads(), ['Buy', 'Accumulate', 'Watchlist']);
  chips[3].click();
  assert.deepEqual(heads(), ['Watchlist'], 'a filtered-out group drops its row');
  chips[0].click();
  const th = [...box.querySelectorAll('thead th')].find((x) => T(x) === 'Levels');
  th.querySelector('button').click();
  assert.equal(th.getAttribute('aria-sort'), 'descending');
  assert.deepEqual(order(), ['MSFT', 'AMZN', 'JPM', 'CSCO'], 'by upside to target');
  assert.deepEqual(heads(), [], 'sorted by another column the groups step aside');
  const rank = [...box.querySelectorAll('thead th')].find((x) => T(x) === '#');
  rank.querySelector('button').click();
  assert.deepEqual(order(), ['AMZN', 'MSFT', 'CSCO', 'JPM']);
  assert.deepEqual(heads(), ['Buy', 'Accumulate', 'Watchlist'], 'back in rank order the groups return in place');
  const tbodyRows = [...box.querySelectorAll('tbody tr')].map((tr) => tr.dataset.pkGrp || tr.dataset.pkTicker);
  assert.deepEqual(tbodyRows, ['buy', 'AMZN', 'accumulate', 'MSFT', 'watchlist', 'CSCO', 'JPM']);
});

test('the model line shows until the evidence is validated', () => {
  assert.ok(render(payload()).querySelector('[data-pk-valid]'));
  const ok = ROWS().map((r) => ({ ...r, evidence: 'VALIDATED' }));
  assert.equal(render(payload({ stock_picks: ok })).querySelector('[data-pk-valid]'), null);
});

test('the report is five sections; a row opens its analysis beside the list of calls', () => {
  const rows = ROWS();
  Object.assign(rows[0], { volatility: 0.38, beta: 1.6, risk_grade: 'C', sma_50: 240, atr: 5.4, trailing_stop: 244.51, days_to_earnings: 20 });
  const box = render(payload({ stock_picks: rows }));
  const tabs = [...box.querySelectorAll('.pk-tabs [role="tab"]')];
  assert.deepEqual(tabs.map((t) => T(t)), ['Overview', 'Analysis4', 'Momentum alerts', 'Risk and execution', 'Method']);
  assert.equal(panel(box, 'overview').hidden, false);
  assert.ok(['analysis', 'signals', 'risk', 'method'].every((k) => panel(box, k).hidden));
  const groups = [...panel(box, 'analysis').querySelectorAll('.pk-alist-h')].map((h) => T(h));
  assert.deepEqual(groups, ['Buy', 'Accumulate', 'Watchlist']);
  const shown = () => [...panel(box, 'analysis').querySelectorAll('[data-pk-card]')].filter((c) => !c.hidden).map((c) => c.dataset.pkCard);
  assert.deepEqual(shown(), ['AMZN'], 'the first call opens first');
  const card = panel(box, 'analysis').querySelector('[data-pk-card="AMZN"]');
  assert.match(T(card.querySelector('.pk-card-tags')), /^Buy ?Model not yet validated$/);
  assert.match(T(card.querySelector('.pk-keys')), /Price ?\$252\.65 ?last close ?Target ?\$272\.14 ?\+7\.7% from price ?Stop ?\$244\.51 ?-3\.2% from price ?R:R ?2\.28 ?net of costs/);
  const plan = T(card.querySelector('.pk-plan'));
  assert.match(plan, /Your capital ?1 share · \$253 \(10\.1% of capital\) · risk at stop \$8 \(0\.33% of capital\)/);
  assert.match(plan, /Conviction ?★★★★☆ 4\/5/);
  const checks = [...card.querySelectorAll('.pk-check li')].map((li) => T(li.querySelector('.pk-check-k')));
  assert.deepEqual(checks, ['Close below the stop', 'Loses the 50-day average', 'VIX above 25', 'Guidance cut at earnings']);
  box.querySelector('tr.pk-row[data-pk-ticker="MSFT"] .pk-c-price').click();
  assert.equal(panel(box, 'overview').hidden, true);
  assert.equal(panel(box, 'analysis').hidden, false);
  assert.deepEqual(shown(), ['MSFT']);
  assert.equal(box.querySelector('[data-pk-pick="MSFT"]').getAttribute('aria-current'), 'true');
  assert.match(T(panel(box, 'analysis').querySelector('[data-pk-card="MSFT"] .pk-plan')), /Your capital ?Needs about \$2,900 for one share within the position cap/);
  box.querySelector('[data-pk-pick="CSCO"]').click();
  assert.deepEqual(shown(), ['CSCO']);
  const csco = panel(box, 'analysis').querySelector('[data-pk-card="CSCO"]');
  assert.equal(T(csco.querySelector('.pk-card-tags')), 'Watchlist');
  assert.doesNotMatch(T(csco), /Conviction/, 'a Watchlist name shows no conviction');
  assert.match(T(csco), /Caution ?Watchlist\. Deep review downgraded it: heavy insider selling/);
  tabs[4].click();
  assert.equal(panel(box, 'method').hidden, false);
  assert.equal(tabs[4].getAttribute('aria-selected'), 'true');
});

test('the call never changes with capital fit; capital is its own field', () => {
  const rows = ROWS();
  const msft = rows.find((r) => r.ticker === 'MSFT');
  assert.equal(app.pkGroupOf(msft), 'accumulate');
  assert.equal(app.pkCapital(msft, '$').text, 'Needs about $2,900 for one share within the position cap');
  assert.equal(app.pkCapital(rows[0], '$').text, '$253, 1 share');
  // An older payload without `call` maps the engine's final action the same way.
  assert.equal(app.pkGroupOf({ signal: 'STRONG_BUY', status: 'not_funded' }), 'buy', 'Strong Buy folds into Buy until validated');
  assert.equal(app.pkGroupOf({ signal: 'BUY', status: 'funded' }), 'buy');
  assert.equal(app.pkGroupOf({ signal: 'ACCUMULATE', status: 'not_funded' }), 'accumulate');
  assert.equal(app.pkGroupOf({ signal: 'BUY', status: 'watch' }), 'watchlist');
  assert.equal(app.pkGroupOf({ signal: 'AVOID', status: 'avoid' }), '', 'an Avoid is not a pick');
  assert.equal(app.pkGroupOf({ signal: 'HOLD', status: 'not_funded' }), '', 'a Hold is not a pick');
  assert.equal(app.pkGroupOf({ signal: 'BUY', status: 'below_rule' }), '', 'a name that missed a book rule is not a pick');
});

test('Method lists the names that are not picks: excluded for safety, then screened out', () => {
  const box = render(payload());
  const m = panel(box, 'method');
  const ex = m.querySelector('[data-pk-excluded]');
  assert.match(T(ex), /^Excluded for safety ?1 name ?AVDX ?AVDX Inc\. ?Pending cash acquisition at \$102\.50\. ?SEC EDGAR, filed 2026-09-28 filing$/);
  assert.equal(ex.querySelector('a').getAttribute('href'), 'https://www.sec.gov/Archives/edgar/data/1/x.htm');
  assert.equal(ex.querySelector('a').getAttribute('rel'), 'noopener noreferrer');
  const so = [...m.querySelectorAll('[data-pk-screened] li')].map((li) => T(li));
  assert.deepEqual(so, ['LOWR LOWR Inc.Reward to risk 0.80:1, needs 1.80:1'], 'the name, then the book rule it missed');
  assert.match(T(m.querySelector('[data-pk-screened] .pkp-h')), /Screened out at the book rules ?1 name/);
  assert.doesNotMatch(T(m.querySelector('[data-pk-screened]')), /Signal not actionable/, 'per-name reasons are counted once, in the stage table');
  assert.equal(box.querySelector('tr.pk-row[data-pk-ticker="AVDX"], tr.pk-row[data-pk-ticker="LOWR"]'), null, 'neither is a row in the table');
});

test('a blocking event-check problem shows one line above the report', () => {
  let box = render(payload());
  assert.ok(panel(box, 'method').querySelector('[data-coverage]'), 'coverage line in Method');
  assert.equal(box.querySelector('[data-picks-v2] > [data-pk-alert]'), null, 'nothing blocking, no alert line');
  box = render(payload({ event_check: { checked: 100, clear: 95, flagged: 0, unavailable: 5, contact: { state: 'configured' },
    reasons: [{ reason: 'coverage_short', label: 'filing history longer than the pages read', count: 5, tickers: ['JPM', 'GS', 'MS', 'C', 'BAC'], book_tickers: ['JPM', 'GS'], fix: 'x' }] } }));
  const alert = box.querySelector('[data-picks-v2] > [data-pk-alert]');
  assert.ok(alert);
  assert.match(T(alert), /Corporate-event check could not read 2 names in the book \(JPM, GS\); they stay on the Watchlist/);
  assert.ok(panel(box, 'risk').querySelector('[data-event-check-banner]'), 'full banner under Risk and execution');
});

test('unreadable names that never reached the book raise no alert line (they block nothing)', () => {
  const box = render(payload({ event_check: { checked: 80, clear: 68, flagged: 0, unavailable: 12, contact: { state: 'configured' },
    reasons: [{ reason: 'no_registrant', label: 'no SEC registrant for the ticker', count: 12, tickers: ['CFLT', 'NEWR'], book_tickers: [], fix: 'x' }] } }));
  assert.equal(box.querySelector('[data-picks-v2] > [data-pk-alert]'), null);
  assert.ok(panel(box, 'risk').querySelector('[data-event-check-banner]'), 'still listed under Risk and execution');
});

/* ── The named cases ─────────────────────────────────────────────────────── */
test('case: only Watchlist names says there is no Buy or Accumulate pick, with the count and reasons', () => {
  const box = render(CASES.only_watch());
  const empty = T(box.querySelector('[data-pk-empty]'));
  assert.equal(empty, 'No Buy or Accumulate picks right now. 2 names are on the Watchlist. Top reasons: Hold signal, no buy setup (50), reward-to-risk below the hurdle (44).');
  assert.deepEqual([...box.querySelectorAll('tbody tr.pk-row')].map((r) => r.dataset.pkTicker), ['CSCO', 'JPM'], 'the Watchlist still shows');
  assert.equal(box.querySelector('[data-pk-copy="ba"]'), null, 'nothing to copy as Buy or Accumulate');
  assert.ok(box.querySelector('[data-pk-copy="watchlist"]'));
  assert.match(T(panel(box, 'risk')), /No suggested position in this scan, so there is nothing to size or execute\./);
});

test('case: only Buy names shows no empty state and one copy button', () => {
  const box = render(CASES.only_buy());
  assert.equal(box.querySelector('[data-pk-empty]'), null);
  assert.equal(box.querySelector('.pk-chips'), null, 'one call kind needs no filter');
  assert.ok(box.querySelector('[data-pk-copy="ba"]'));
  assert.equal(box.querySelector('[data-pk-copy="watchlist"]'), null);
});

test('case: a cost-limited name keeps its call and says what one share needs', () => {
  const box = render(CASES.cost_limited());
  const row = box.querySelector('tr.pk-row[data-pk-ticker="MSFT"]');
  assert.equal(T(row.querySelector('.pk-signal')), 'Accumulate');
  assert.equal(T(row.querySelector('.pk-c-pos')), 'Needs about $2,900 for one share within the position cap');
  assert.equal(box.querySelector('[data-pk-empty]'), null, 'an Accumulate call is a pick even when the capital does not fit');
  assert.match(T(box.querySelector('[data-pk-summary]')), /Suggested deployment ?\$0/);
});

test('case: two share classes of one company are one row; the other class is named in its analysis', () => {
  const box = render(CASES.two_classes());
  assert.deepEqual([...box.querySelectorAll('tbody tr.pk-row')].map((r) => r.dataset.pkTicker), ['AMZN']);
  assert.match(T(box.querySelector('[data-pk-card="AMZN"] .pk-plan')), /Share classes ?Also trades as AMZN\.B: the same company\. One class per company is shown, chosen after the Research check\./);
});

test('case: a class held back by Research holds back its twin, and the row says so (D2)', () => {
  const watch = ROWS().find((r) => r.ticker === 'CSCO');
  const reason = 'Held back with CSCO: same company, and the Research check held CSCO back, so no share class of it gets new money';
  const box = render(payload({ stock_picks: [{ ...watch, other_classes: [{ ticker: 'CSCO.B', company: 'CSCO Inc.', held_back: true, reason }] }] }));
  const row = box.querySelector('tr.pk-row[data-pk-ticker="CSCO"]');
  const held = row.querySelector('[data-pk-held]');
  assert.equal(T(held), 'CSCO.B held back too: same company');
  assert.equal(held.getAttribute('title'), reason);
  assert.match(T(box.querySelector('[data-pk-card="CSCO"] .pk-plan')), /Share classes ?Also trades as CSCO\.B: the same company\. The Research check held CSCO back, so CSCO\.B is held back too; no class of this company gets new money\./);
  assert.equal(box.querySelector('tr.pk-row[data-pk-ticker="CSCO.B"]'), null, 'the held-back class has no row of its own');
  // A twin that simply ranked behind a funded class is not marked as held back.
  const plain = render(CASES.two_classes());
  assert.equal(plain.querySelector('[data-pk-held]'), null);
});

test('case: an unreadable event check is a Watchlist call with the reason and the fix', () => {
  const box = render(CASES.event_unavailable());
  const card = T(box.querySelector('[data-pk-card="JPM"]'));
  assert.match(card, /Caution ?Watchlist\. Corporate-event check unavailable: filing history longer than the pages read\. Held at Watchlist until it can be checked\. Fix: Raise EVENTS\.FILING_PAGES_MAX in thresholds\.py/);
  assert.equal(T(box.querySelector('tr.pk-row[data-pk-ticker="JPM"] .pk-signal')), 'Watchlist');
});

test('case: a pending acquisition is never a pick; it is listed under Method', () => {
  const box = render(CASES.pending_acquisition());
  assert.equal(box.querySelector('table.pk-table'), null);
  assert.match(T(box.querySelector('[data-pk-empty]')), /^No Buy or Accumulate picks right now\. Top reasons/);
  assert.match(T(panel(box, 'method').querySelector('[data-pk-excluded]')), /AVDX .*Pending cash acquisition .*SEC EDGAR, filed 2026-09-28/);
});

test('case: an empty scan says so, with the reasons, and has nothing to analyse', () => {
  const box = render(CASES.empty());
  assert.equal(box.querySelector('table.pk-table'), null);
  assert.equal(box.querySelector('.pk-chips'), null);
  assert.equal(box.querySelector('[data-pk-copy]'), null);
  assert.equal(box.querySelector('[data-pk-csv]'), null);
  assert.match(T(box.querySelector('[data-pk-summary]')), /Suggested deployment ?\$0/);
  assert.doesNotMatch(T(box.querySelector('[data-pk-summary]')), /Watchlist/, 'the counts live in the header');
  assert.match(T(box.querySelector('[data-pk-empty]')), /Hold signal, no buy setup \(50\), reward-to-risk below the hurdle \(44\)/);
  assert.match(T(panel(box, 'analysis')), /No Buy, Accumulate or Watchlist call in this scan, so there is nothing to analyse\./);
  assert.equal(panel(box, 'method').querySelector('[data-pk-excluded]'), null);
});

/* ── CSV and copy ─────────────────────────────────────────────────────────── */
test('the CSV carries the call and your capital, never a status column', () => {
  const lines = app.stockPicksCsv(ROWS()).trim().split('\n');
  assert.equal(lines[0], 'rank,ticker,company,call,price,entry,target,upside_pct,stop,risk_reward,score,risk_grade,your_capital,suggested_dollars,suggested_shares,reason,why_picked');
  assert.equal(lines.length, 5);
  assert.match(lines[1], /^1,AMZN,AMZN Inc\.,Buy,252\.65,.*,"\$253, 1 share",252\.65,1,,$/);
  assert.match(lines[2], /^2,MSFT,MSFT Inc\.,Accumulate,.*,"Needs about \$2,900 for one share within the position cap",,,,$/);
  assert.match(lines[3], /^3,CSCO,CSCO Inc\.,Watchlist,.*,No new money on a Watchlist call,,,Deep review downgraded it: heavy insider selling,$/);
});

test('copy: Buy and Accumulate, or the Watchlist', async () => {
  assert.equal(app.pkCopyText(ROWS(), 'ba'), 'AMZN,MSFT');
  assert.equal(app.pkCopyText(ROWS(), 'watchlist'), 'CSCO,JPM');
  const box = render(payload());
  const copied = [];
  const was = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async (t) => { copied.push(t); } } }, configurable: true, writable: true });
  try {
    box.querySelector('[data-pk-copy="ba"]').click();
    box.querySelector('[data-pk-copy="watchlist"]').click();
    await new Promise((r) => setTimeout(r, 0));
  } finally { if (was) Object.defineProperty(globalThis, 'navigator', was); else delete globalThis.navigator; }
  assert.deepEqual(copied, ['AMZN,MSFT', 'CSCO,JPM']);
});

/* ── Diagnostics placement ───────────────────────────────────────────────── */
test('the Pro layer never adds its cards beside the table', () => {
  const data = payload({ deep_confirmation: { rejected: [{ ticker: 'CSCO', company: 'Cisco', reason: 'Heavy insider selling', scanner_action: 'BUY', deep_action: 'WATCHLIST' }] },
    analyzer_disagreement_watchlist: [{ ticker: 'CSCO', company: 'Cisco', reason: 'Heavy insider selling', scanner_action: 'BUY', deep_action: 'WATCHLIST' }] });
  const box = render(data);
  require('../../static/elite-pro.js');
  const api = globalThis.QuantaPro;
  assert.ok(api && typeof api.injectPicksExtras === 'function', 'Pro API loaded');
  api.injectPicksExtras(data);
  const view = box.querySelector('[data-picks-v2]');
  const outside = [...view.children].filter((c) => /qpro-/.test(c.className || ''));
  assert.deepEqual(outside.map((c) => c.className), [], 'no Pro card outside the sections');
});

test('a partial scan goes into Method; a throttled one stays above the report', () => {
  let box = render(payload({ scan_status: 'partial', analysis_failed_count: 1, total_scanned: 100 }));
  assert.equal(box.querySelector('[data-picks-v2] > [data-pk-status]'), null);
  assert.match(T(panel(box, 'method').querySelector('[data-pk-status]')), /Partial scan: 1 of 100 names hit provider or analysis errors/);
  box = render(payload({ scan_status: 'partial', analysis_failed_count: 30, provider_rate_limited_count: 30, total_scanned: 100 }));
  assert.ok(box.querySelector('[data-picks-v2] > [data-pk-status]'), 'a throttled scan keeps its line (and safe-mode retry) above the report');
});

test('warnings are only the ones that hold', () => {
  const box = render(payload());
  const warn = [...box.querySelectorAll('[data-pk-warn] li')].map((li) => T(li));
  assert.deepEqual(warn, ['Re-check the entry if the open gaps more than 2% against you.',
    'Concentration: Technology (3) among these calls; their prices tend to move together.']);
  const calm = ROWS().map((r, i) => ({ ...r, sector: `S${i}` }));
  const high = [...render(payload({ stock_picks: calm, regime: { type: 'VOLATILE', vix: 31.2 } })).querySelectorAll('[data-pk-warn] li')].map((li) => T(li));
  assert.ok(high.includes('VIX 31.2 is above 25: consider standing down.'));
  assert.ok(!high.some((t) => t.startsWith('Concentration')));
});

test('the arrow and the whole row open the analysis; arrow keys move between sections', () => {
  const box = render(payload());
  box.querySelector('tr.pk-row[data-pk-ticker="JPM"] [data-pk-open]').click();
  assert.equal(panel(box, 'analysis').hidden, false);
  assert.equal(box.querySelector('[data-pk-card="JPM"]').hidden, false);
  const tabs = [...box.querySelectorAll('.pk-tabs [role="tab"]')];
  tabs[1].focus();
  tabs[1].dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(tabs[2].getAttribute('aria-selected'), 'true');
  assert.match(T(panel(box, 'signals')), /No chase alerts, early-momentum setups or filled gaps in this scan\./);
});

test('an entry range may wrap at its hyphen', () => {
  const box = render(payload());
  const zones = [...box.querySelector('tr.pk-row[data-pk-ticker="AMZN"] .pk-lv-entry').querySelectorAll('.pk-zone')].map((z) => z.textContent);
  assert.deepEqual(zones, ['$247.22-', '$252.65'], 'two pieces: wrap only where wrapping is allowed (a <wbr> breaks even under nowrap)');
});

/* ── Why this stock was picked ─────────────────────────────────────────── */
const WHY = {
  summary: 'A Buy analyst consensus, rising earnings estimates and an established uptrend.',
  for: [
    { kind: 'analysts', label: 'Analysts', text: 'Wall Street consensus is Buy from 42 analysts; mean target $300.00 (+18.7% from the price).', short: 'x', source: 'Analyst consensus via Yahoo Finance' },
    { kind: 'catalyst', label: 'New contract', text: 'Amazon wins $1.2B cloud contract', short: 'x' },
  ],
  against: [{ kind: 'earnings', label: 'Earnings soon', text: 'Earnings are due in 9 days: results can move the price sharply.', short: 'x' }],
  news: [{ title: 'Amazon expands AWS regions', publisher: 'Reuters', published: '2026-10-03T13:00:00+00:00', url: 'https://example.com/a' },
    { title: 'Headline without a link', publisher: '', published: '', url: '' }],
  notes: [],
};

test('why picked: each card says why, what counts against it, with linked company news', () => {
  const rows = ROWS();
  rows[0].why_picked = WHY;
  const box = render(payload({ stock_picks: rows }));
  const sec = box.querySelector('[data-pk-card="AMZN"] [data-why-picked]');
  const t = T(sec);
  assert.match(t, /^Why this stock was picked ?A Buy analyst consensus, rising earnings estimates and an established uptrend\./);
  assert.match(t, /Analysts\. Wall Street consensus is Buy from 42 analysts.* Analyst consensus via Yahoo Finance ?New contract\. Amazon wins \$1\.2B cloud contract/);
  assert.match(t, /Caution ?Earnings soon\. Earnings are due in 9 days/);
  const link = sec.querySelector('.pk-news a');
  assert.equal(link.getAttribute('href'), 'https://example.com/a');
  assert.equal(link.getAttribute('target'), '_blank');
  assert.match(link.getAttribute('rel'), /noopener/);
  assert.match(t, /Amazon expands AWS regions ?Reuters · Oct 3/);
  assert.equal(sec.querySelectorAll('.pk-news a').length, 1, 'an item without a link is plain text');
});

test('why picked: empty sides say so plainly; a fund says what it was scored on', () => {
  const rows = ROWS();
  rows[0].why_picked = { summary: '', for: [], against: [], news: [], notes: ['A fund is scored on trend and momentum only: it has no earnings, margins or balance sheet.'] };
  rows[0].is_fund = true;
  const box = render(payload({ stock_picks: rows }));
  const card = box.querySelector('[data-pk-card="AMZN"]');
  const t = T(card.querySelector('[data-why-picked]'));
  assert.match(t, /Score 69 of 100 is in the BUY band/, 'without a summary the call line leads');
  assert.match(t, /Nothing measured stands out beyond the overall score\./);
  assert.match(t, /Nothing measured counts against it\./);
  assert.match(t, /A fund is scored on trend and momentum only/);
  assert.doesNotMatch(T(card.querySelector('.pk-check')), /earnings/i, 'a fund has no earnings to fail on');
});

/* ── Method and scan types ─────────────────────────────────────────────── */
test('what makes a pick: the method is stated from the rules this scan applied', () => {
  const box = render(payload());
  const m = T(panel(box, 'method').querySelector('[data-pk-method]'));
  assert.match(m, /What makes a QUANTA pick/);
  assert.match(m, /price \$10 to \$5,000, at least 750,000 shares and \$20M traded a day, market cap at least \$2B/);
  assert.match(m, /Accumulate from 5\.5, Buy from 6\.5/);
  assert.match(m, /at least 8% upside to target and reward\/risk of 1\.8:1 after trading costs; risk grade F excluded/);
  assert.match(m, /If the check cannot be read, the name stays on the Watchlist\./);
  assert.doesNotMatch(m, /Style screen/, 'a broad scan has no style screen');
  const val = T(render(payload({ scan_type: 'focused_value' })).querySelector('[data-pk-method]'));
  assert.match(val, /Style screen: positive earnings \(and positive free cash flow where it is reported\), and cheap on at least 2 of: forward P\/E at most 16, EV\/EBITDA at most 11, free-cash-flow yield at least 5%, price\/book at most 2\.5\./);
  const etf = T(render(payload({ scan_type: 'focused_etf' })).querySelector('[data-pk-method]'));
  assert.match(etf, /a fund is scored on trend and momentum only/);
});

test('scan types: the menu has one Specialty heading and names the index each scan reads', () => {
  const labels = [...document.querySelectorAll('#sp-scan optgroup')].map((g) => g.getAttribute('label'));
  assert.equal(labels.filter((l) => l === 'Specialty').length, 1);
  const opt = (v) => T(document.querySelector(`#sp-scan option[value="${v}"]`));
  assert.match(opt('small_cap'), /S&P 600/);
  assert.match(opt('mid_cap'), /S&P 400/);
  assert.equal(app.scanTypeName('focused_value'), 'Value Compounders');
  assert.equal(T(render(payload({ scan_type: 'focused_value' })).querySelector('.pk-meta li')), 'Value Compounders scan');
});

test('scan types: the universe line names the index list and says when the snapshot was used', () => {
  const sel = { method: 'liquidity_ranked', candidates: 603, selected: 80, seconds: 9.1, seconds_measure: 'price_download',
    pool_label: 'S&P 600 small-cap names', universe_total: 603, index_source: 'snapshot', index_as_of: '2026-10-05',
    index_cause: 'the live list could not be read (timeout)' };
  const line = T(render(payload({ scan_type: 'small_cap', universe_selection: sel })).querySelector('[data-coverage]'));
  assert.match(line, /top 80 of 603 S&P 600 small-cap names, ranked by average daily traded value; price download 9\.1 s \(index list from the 2026-10-05 snapshot: the live list could not be read \(timeout\)\)/);
});

/* ── Signs and whole reasons ───────────────────────────────────────────── */
test('a negative upside reads "-0.3%", never "+-0.3%"; a target under the price is said plainly', () => {
  const rows = ROWS();
  Object.assign(rows[2], { price: 112.20, entry: 112.20, stop: 108.24, target: 111.91, upside_pct: -0.26 });
  const box = render(payload({ stock_picks: rows }));
  assert.doesNotMatch(box.querySelector('[data-picks-v2]').textContent, /\+-|-\+/);
  assert.match(T(box.querySelector('tr.pk-row[data-pk-ticker="CSCO"] .pk-lv-labels')), /Target \$111\.91 -0\.3%$/);
  const keys = box.querySelector('[data-pk-card="CSCO"] .pk-keys');
  assert.match(T(keys), /Target ?\$111\.91 ?below the price/);
  assert.ok(keys.querySelector('[title]'), 'the reason is in a tooltip');
  assert.equal(app.pkSignedPct(0), '0.0%');
});

test('a long Watchlist reason is shown whole', () => {
  const rows = ROWS();
  rows[2].reason = 'Deep review downgraded it: heavy insider selling over the last 90 days and a falling estimate trend';
  const box = render(payload({ stock_picks: rows }));
  assert.match(T(box.querySelector('[data-pk-card="CSCO"]')), /Watchlist\. Deep review downgraded it: heavy insider selling over the last 90 days and a falling estimate trend/);
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  const rule = css.slice(css.indexOf('.pk-why {')).split('\n')[0];
  assert.doesNotMatch(rule, /line-clamp|ellipsis/, 'the reason is not clipped');
});

/* ── Analysis: chart, gauges, factors, valuation, street, comparison ────── */
const closes = (n, start, step) => Array.from({ length: n }, (_, i) => +(start + i * step).toFixed(2));
const RICH = () => {
  const rows = ROWS();
  Object.assign(rows[0], {
    history: { dates: Array.from({ length: 260 }, (_, i) => `2025-${String(1 + Math.floor(i / 22)).padStart(2, '0')}-01`), close: closes(260, 200, 0.2) },
    tech: { rsi: 72.4, sma_20: 250, sma_50: 240, sma_200: 220, volume_ratio: 1.5, atr: 5.05, momentum_1m: 0.042, momentum_3m: -0.061, high_52w: 260, low_52w: 190 },
    analyst_split: { buy: 30, hold: 8, sell: 2 },
    insider: { sentiment: 'BEARISH', buys: 1, sells: 6, net_value: -2500000, note: '' },
    valuation: { price: 252.65, low: 180, high: 320, sentence: 'The analyst mean ($300.00) is 67% above the DCF fair value ($180.00) because analyst targets are 12-month views from the covering analysts, while the DCF value discounts projected free cash flows to today.',
      marks: [{ key: 'analyst_low', label: 'Analyst low', value: 220 }, { key: 'analyst_mean', label: 'Analyst mean', value: 300 }, { key: 'analyst_high', label: 'Analyst high', value: 320 },
        { key: 'level_target', label: 'Level target', value: 272.14 }, { key: 'dcf', label: 'DCF fair value', value: 180 }], missing: [] },
  });
  return rows;
};

test('analysis: the chart draws price, both averages, the entry zone, stop and target', () => {
  const box = render(payload({ stock_picks: RICH() }));
  const fig = box.querySelector('[data-pk-card="AMZN"] .pk-chart');
  const plot = fig.querySelector('[role="img"]');
  assert.match(plot.getAttribute('aria-label'), /^AMZN closes .* to .*: \$\d/);
  const svg = plot.querySelector('svg');
  for (const cls of ['pk-ch-price', 'pk-ch-ma50', 'pk-ch-ma200', 'pk-ch-zone', 'pk-ch-stop', 'pk-ch-target']) {
    assert.ok(svg.querySelector(`.${cls}`), cls);
  }
  assert.ok(fig.querySelector('.pk-ch-dot'), 'the last close is marked');
  assert.deepEqual(app.pkSma([1, 2, 3, 4], 2), [null, 1.5, 2.5, 3.5]);
});

test('analysis: technicals read as gauges with plain readings', () => {
  const box = render(payload({ stock_picks: RICH() }));
  const rows = [...box.querySelectorAll('[data-pk-card="AMZN"] .pk-tech .pk-grow')].map((g) => [T(g.querySelector('dt')), T(g.querySelector('.pk-gval'))]);
  assert.deepEqual(rows.slice(0, 3).map((r) => r[1]), ['72.4', '+1.1%', '+5.3%']);
  assert.match(rows[0][0], /RSI/);
});

test('analysis: valuation scale, analysts and insiders', () => {
  const box = render(payload({ stock_picks: RICH() }));
  const card = box.querySelector('[data-pk-card="AMZN"]');
  const vals = [...card.querySelectorAll('.pk-vlist li')].map((li) => T(li));
  assert.deepEqual(vals, ['DCF fair value$180.00-28.8%', 'Analyst low$220.00-12.9%', 'Price$252.65', 'Level target$272.14+7.7%',
    'Analyst mean$300.00+18.7%', 'Analyst high$320.00+26.7%'], 'low to high, each with its distance from the price');
  assert.match(T(card.querySelector('.pk-vsent')), /^The analyst mean \(\$300\.00\) is 67% above the DCF fair value/);
  assert.equal(T(card.querySelector('.pk-split-key')), '30 buy 8 hold 2 sell');
  assert.match(T(card.querySelector('.pk-ins')), /-\$2\.50M ?1 buy, 6 sales, net/, 'net insider value is compact, like a market cap');
});

test('analysis: missing data hides a visual with a one-line reason, never an empty chart', () => {
  const box = render(payload());
  const card = box.querySelector('[data-pk-card="MSFT"]');
  const t = T(card);
  assert.equal(card.querySelector('svg'), null);
  assert.match(t, /No price history was kept for this name, so there is no chart\./);
  assert.match(t, /No technical readings were kept for this name\./);
  assert.match(t, /No valuation estimates were measured for this name\./);
});

test('analysis: each value appears once in a card', () => {
  const box = render(payload({ stock_picks: RICH() }));
  const t = T(box.querySelector('[data-pk-card="AMZN"]'));
  for (const once of ['$244.51', '72.4', '30 buy', '$253']) {
    assert.equal(t.split(once).length - 1, 1, `${once} appears once`);
  }
});

test('analysis: two to three stocks compare side by side', () => {
  const box = render(payload({ stock_picks: RICH() }));
  const btn = box.querySelector('[data-pk-compare]');
  assert.equal(btn.disabled, true);
  const tick = (t) => { const b = box.querySelector(`[data-pk-cmp="${t}"]`); b.checked = true; b.dispatchEvent(new dom.window.Event('change')); };
  tick('AMZN'); assert.equal(btn.disabled, true);
  tick('CSCO'); assert.equal(btn.disabled, false);
  tick('MSFT');
  assert.equal(box.querySelector('[data-pk-cmp="JPM"]').disabled, true, 'a fourth cannot be added');
  btn.click();
  const cmp = box.querySelector('[data-pk-compare-panel]');
  assert.equal(cmp.hidden, false);
  const head = [...cmp.querySelectorAll('thead th')].map((th) => T(th));
  assert.deepEqual(head, ['Measure', 'AMZN', 'MSFT', 'CSCO']);
  const row = (k) => [...cmp.querySelectorAll('tbody tr')].find((tr) => T(tr.querySelector('th')) === k);
  assert.deepEqual([...row('Call').querySelectorAll('td')].map((td) => T(td)), ['Buy', 'Accumulate', 'Watchlist']);
  cmp.querySelector('[data-pk-cmp-close]').click();
  assert.equal(cmp.hidden, true);
});

// Keep the base row helper exported by the fixture in use for ad-hoc rows.
test('a row built from the fixture helper renders with its call', () => {
  const r = base('ZZZZ', 1, { call: 'accumulate', capital: { kind: 'not_sized', text: 'Book already holds its 5 positions' } });
  const box = render(payload({ stock_picks: [r] }));
  assert.equal(T(box.querySelector('tr.pk-row .pk-c-pos')), 'Book already holds its 5 positions');
});

/* ── India: the market, its VIX, its rules, and why no Buy call ─────────── */
const INDIA = () => payload({ market: 'INDIA', currency: '\u20b9', capital: 200000, scan_type: 'india_quick', stock_picks: [], screened_out: [],
  excluded_for_safety: [], recommendations: [], funded_count: 0, watch_count: 0, total_scanned: 50, regime: { type: 'BEAR', vix: 13.6, exposure: 75 },
  accounting: { version: 1, universe: 50, funded: 0, watch: 0, reached_book: 0, could_not_analyse: { count: 1, by_reason: { data_invalid: 1 } },
    rule_rejected: { count: 49, by_reason: { signal_not_actionable: 25, score_below_min: 23, low_volume: 1 } }, book_stages: [{ key: 'actionable', entered: null, passed: null, evaluated: false }] },
  event_check: { checked: 50, clear: 0, flagged: 0, unavailable: 50, contact: { state: 'configured' },
    reasons: [{ reason: 'not_covered', label: 'exchange not covered by an event source', count: 50, tickers: ['TCS.NS'], book_tickers: [] }] } });

test('india: the header names India, the regime quotes India VIX, every scanned name is counted', () => {
  const box = render(INDIA());
  assert.ok([...box.querySelectorAll('.pk-meta li')].map((li) => T(li)).includes('India'));
  const old = INDIA(); delete old.market;
  assert.ok([...render(old).querySelectorAll('.pk-meta li')].map((li) => T(li)).includes('India'), 'an older payload is read from its currency');
  assert.ok([...render(payload()).querySelectorAll('.pk-meta li')].map((li) => T(li)).includes('US'));
  const cards = T(render(INDIA()).querySelector('[data-pk-summary]'));
  assert.match(cards, /Scan to calls ?50 ?scanned ?0 ?ranked ?0 ?sized ?49 screened out, 1 not analysed/);
  assert.match(cards, /regime allows 75%/);
  assert.match(cards, /Regime ?Bear ?India VIX 13\.6$/);
});

test('india: the empty state says no name passed and why no Buy call is possible', () => {
  const t = T(render(INDIA()).querySelector('[data-pk-empty]'));
  assert.match(t, /^No Buy or Accumulate picks right now\. No name passed the per-name rules\. Top reasons: Hold signal, no buy setup \(25\), score below the minimum \(23\), trading volume too low \(1\)\./);
  assert.match(t, /(?:NSE and BSE|BSE and NSE) listings get no Buy or Accumulate call: no corporate-event source covers them yet, so the best call a name can reach is Watchlist\./);
  assert.equal(render(payload()).querySelector('[data-pk-uncovered]'), null, 'a US scan says nothing about it');
});

test('india: Method states the rupee rules the scan applied, not the US ones', () => {
  const m = T(render(INDIA()).querySelector('[data-pk-method]'));
  assert.match(m, /price ₹50 to ₹1,00,000, at least 1,00,000 shares and ₹5 crore traded a day, market cap at least ₹5,000 crore/);
  assert.match(m, /no corporate-event source covers (?:NSE and BSE|BSE and NSE) listings yet, so a name can reach Watchlist at most/);
  assert.doesNotMatch(m, /\$|SEC/);
});

test('india: with no name in the book the stage table is one line, not rows of "not evaluated"', () => {
  const data = INDIA();
  render(data);
  require('../../static/elite-pro.js');
  const h = globalThis.QuantaPro.stageTableCard ? globalThis.QuantaPro.stageTableCard(data) : require('../../static/elite-pro.js').stageTableCard(data);
  assert.match(h, /No name reached the book, so none of its 1 stages ran\./);
  assert.doesNotMatch(h, /Book stage<\/th>/);
});

test('india: the scan menu offers each universe once and names the Nifty 500 honestly', () => {
  const opts = [...document.querySelectorAll('#sp-scan option[data-market="IN"]')].map((o) => [o.value, T(o)]);
  assert.deepEqual(opts.map(([v]) => v), ['india_quick', 'india_standard', 'india_sectoral', 'india_comprehensive',
    'india_growth', 'india_value', 'india_dividend', 'india_momentum', 'india_large', 'india_mid', 'india_small']);
  assert.equal(opts[3][1], 'Comprehensive: all of the Nifty 500 (slowest)');
  assert.ok(!opts.some(([v]) => v === 'india_full'), 'Full built the same names as Comprehensive');
});

/* ── Scan filters, presets and result filters ─────────────────────────── */
test('scan filters: built from config, units follow the market, amounts sent raw', () => {
  global.window = {};
  global.CSS = global.CSS || { escape: (v) => String(v) };
  try {
    app.applyPicksMarketUI('US');
    const boxes = [...document.querySelectorAll('#sp-sectors input')];
    assert.equal(boxes.length, 11, 'one box per configured sector');
    assert.equal(T(document.querySelector('.sp-f-unit[data-unit="cap"]')), '($B)');
    boxes.find((b) => b.value === 'Technology').checked = true;
    document.querySelector('#sp-f-capmin').value = '10';
    document.querySelector('#sp-f-value').value = '50';
    assert.deepEqual(app.pickScanFilters(), { sectors: ['Technology'], min_market_cap: 1e10, min_daily_value: 5e7 });
    document.querySelector('#sp-market').value = 'IN';
    app.applyPicksMarketUI('IN');
    assert.equal(T(document.querySelector('.sp-f-unit[data-unit="cap"]')), '(\u20b9 crore)');
    assert.equal(document.querySelector('#sp-f-capmin').value, '', 'amounts mean something else in the other market');
    assert.equal(boxes.find((b) => b.value === 'Technology').checked, true, 'sector choice carries over');
    document.querySelector('#sp-f-capmin').value = '5000';
    assert.equal(app.pickScanFilters().min_market_cap, 5e10, 'Rs 5,000 crore');
    document.querySelector('#sp-f-pmin').value = '900'; document.querySelector('#sp-f-pmax').value = '100';
    assert.match(app.pickScanFilters().error, /above/);
    document.querySelector('#sp-filters-clear').click();
    assert.deepEqual(app.pickScanFilters(), {});
  } finally {
    document.querySelector('#sp-market').value = 'US';
    app.applyPicksMarketUI('US');
    global.window = undefined;
  }
});

test('presets: the US menu offers Momentum; Method states each preset screen', () => {
  assert.ok(document.querySelector('#sp-scan option[value="focused_momentum"][data-market="US"]'));
  const mom = T(render(payload({ scan_type: 'focused_momentum' })).querySelector('[data-pk-method]'));
  assert.match(mom, /Style screen: price above its 50-day average, which is above the 200-day, and a 3-month return of at least 5%\./);
  const div = T(render({ ...INDIA(), scan_type: 'india_dividend' }).querySelector('[data-pk-method]'));
  assert.match(div, /Style screen: a dividend yield of at least 1\.5%, payout at most 100% of earnings/);
  assert.doesNotMatch(div, /Aristocrats/);
  const small = T(render({ ...INDIA(), scan_type: 'india_small' }).querySelector('[data-pk-method]'));
  assert.match(small, /market cap at least ₹1,000 crore/);
});

test('scan filters a result ran with are named in the header and in Method', () => {
  const box = render(payload({ filters: { sectors: ['Healthcare', 'Technology'], min_market_cap: 1e10, max_price: 500, min_daily_value: 5e7 } }));
  const want = 'Healthcare, Technology; market cap at least $10B; price up to $500; at least $50M traded a day';
  assert.ok([...box.querySelectorAll('.pk-meta li')].map((li) => T(li)).includes(`Filters: ${want}`));
  assert.ok(T(box.querySelector('[data-pk-method]')).includes(`Your filters: ${want}.`));
  const india = render({ ...INDIA(), filters: { min_market_cap: 5e10, sectors: ['Energy', 'Utilities', 'Industrials'] } });
  assert.ok([...india.querySelectorAll('.pk-meta li')].map((li) => T(li)).includes('Filters: 3 sectors; market cap at least ₹5,000 crore'));
  assert.ok(![...render(payload()).querySelectorAll('.pk-meta li')].some((li) => /Filters/.test(T(li))));
});

test('result filters narrow the table without rescanning, and say how many show', () => {
  const rows = ROWS();
  Object.assign(rows[0], { risk_grade: 'A', sector: 'Technology', score: 7.2, risk_reward: 2.8 });
  Object.assign(rows[1], { risk_grade: 'C', sector: 'Technology', score: 6.1, risk_reward: 1.9 });
  Object.assign(rows[2], { risk_grade: 'B', sector: 'Healthcare', score: 5.8, risk_reward: 2.4 });
  Object.assign(rows[3], { risk_grade: 'B', sector: 'Financials', score: 5.2, risk_reward: 2.0 });
  const box = render(payload({ stock_picks: rows }));
  const shown = () => [...box.querySelectorAll('tbody tr.pk-row')].filter((r) => !r.hidden).map((r) => r.dataset.pkTicker);
  const set = (k, v) => { const el = box.querySelector(`[data-pk-rf="${k}"]`); el.value = v; el.dispatchEvent(new dom.window.Event(el.tagName === 'SELECT' ? 'change' : 'input')); };
  set('sector', 'Technology');
  assert.deepEqual(shown(), ['AMZN', 'MSFT']);
  assert.equal(T(box.querySelector('[data-pk-rf-count]')), 'Showing 2 of 4');
  set('grade', 'B');
  assert.deepEqual(shown(), ['AMZN'], 'grade B or better');
  set('sector', ''); set('grade', ''); set('rr', '2.3');
  assert.deepEqual(shown(), ['AMZN', 'CSCO']);
  set('score', '70');
  assert.deepEqual(shown(), ['AMZN']);
  box.querySelector('[data-pk-filter="watchlist"]').click();
  assert.deepEqual(shown(), [], 'the call chip and the filters combine');
  assert.equal(box.querySelector('[data-pk-nomatch]').hidden, false);
  box.querySelector('[data-pk-rf-clear]').click();
  assert.deepEqual(shown(), ['CSCO', 'JPM'], 'clearing the filters keeps the chosen call');
  box.querySelector('[data-pk-filter="all"]').click();
  assert.equal(shown().length, 4);
  assert.equal(T(box.querySelector('[data-pk-rf-count]')), '');
});

test('B-89: a held name says "already held" on its row and card; the table says whether holdings were checked', () => {
  const rows = ROWS();
  const msft = rows.find((r) => r.ticker === 'MSFT');
  Object.assign(msft, { held: { quantity: 3, market_value: 1530.4, pct_of_capital: 61.2 }, allocation: 0, shares: 0,
    capital: { kind: 'held', text: 'Already held: $1,530 reaches the 50% per-name cap, so no new money is suggested' } });
  const box = render(payload({ stock_picks: rows, holdings_check: { state: 'checked', held: 1, retrieved_at: '2026-10-08T14:00:00+00:00' } }));
  const row = box.querySelector('tr.pk-row[data-pk-ticker="MSFT"]');
  const own = row.querySelector('[data-pk-own]');
  assert.equal(T(own), 'Already held · 61.2% of capital');
  assert.match(own.getAttribute('title'), /3 shares, \$1,530/);
  assert.equal(T(row.querySelector('.pk-c-pos')), 'Already held: $1,530 reaches the 50% per-name cap, so no new money is suggested');
  assert.equal(box.querySelector('tr.pk-row[data-pk-ticker="AMZN"] [data-pk-own]'), null, 'an unheld name carries no mark');
  assert.equal(T(box.querySelector('[data-pk-card="MSFT"] [data-pk-own]')), 'Already held · 61.2% of capital', 'the card says so too');
  assert.match(T(box.querySelector('[data-pk-holdings]')), /^Checked against your broker holdings/);
  const off = render(payload({ stock_picks: ROWS(), holdings_check: { state: 'not_checked', text: 'Holdings not checked: load your broker account in My Portfolio to size picks around what you already own.' } }));
  assert.equal(T(off.querySelector('[data-pk-holdings]')), 'Holdings not checked: load your broker account in My Portfolio to size picks around what you already own.');
  assert.equal(render(payload({ stock_picks: ROWS() })).querySelector('[data-pk-holdings]'), null, 'no check reported, no line');
});
