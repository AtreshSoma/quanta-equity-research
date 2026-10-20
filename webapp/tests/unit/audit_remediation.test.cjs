/**
 * Audit remediation (2026-09-27): the Overview must not turn missing market
 * data into guidance. Every assertion here was on screen with every feed down:
 * a "Lean Defensive 20-45% gross" playbook, an "Elevated" risk level, a 5.3/10
 * signal score, "0" earnings and a "quiet" headline tape.
 *
 * Run: node --test tests/unit/audit_remediation.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '../..');

function boot() {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8'));
  const ctx = vm.createContext({
    __QUANTA_THRESHOLDS__: globalThis.__QUANTA_THRESHOLDS__,
    document: dom.window.document, window: dom.window, console, URLSearchParams, AbortController,
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout() { return 0; }, clearTimeout() {}, setInterval() { return 0; }, clearInterval() {},
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app.js'), 'utf8') +
    '\n globalThis.api = {state, renderMarketRegime, renderSignalCheck, setFreshness, renderTrackRecord, renderPicks};', ctx);
  return { ...ctx.api, document: dom.window.document };
}

const degradedMarket = () => ({
  quotes: [],
  market_regime_engine: {
    posture: 'Lean Defensive', marketRegime: 'Data Incomplete', colorTheme: { toneClass: 'neutral' },
    guidanceWithheld: true, earningsCalendarKnown: false, newsFeedsLive: false,
    dataFreshness: { stale: true, unmeasuredFactors: ['trend', 'volatility', 'momentum'] },
    portfolioGuidance: { band: '20-45% gross', actions: [] },
    primaryAction: { sizing: 'Half-size risk', avoid: 'Avoid averaging down' },
    factors: [{ key: 'trend', label: 'Trend', score: 0, note: 'Trend unavailable' },
              { key: 'macro_risk', label: 'Macro / Catalyst Risk', score: 0, note: 'unknown' }],
    marketDrivers: [],
  },
});

test('withheld guidance: the mood card shows no exposure band or sizing', () => {
  const app = boot();
  app.renderMarketRegime(degradedMarket(), {}, {});
  const body = app.document.querySelector('#decision-brief');
  assert.match(body.querySelector('[data-guidance="withheld"]').textContent, /No exposure band, sizing or preference/);
  assert.equal(body.querySelector('.ovx-money'), null, 'no money bar');
  assert.equal(body.querySelector('.ovx-mood-word').textContent, 'No read');
  assert.doesNotMatch(body.textContent, /20-45% gross|Half-size/);
  assert.equal(body.querySelector('.ovx-sure'), null, 'no confidence read on withheld inputs');
});

test('withheld guidance: unmeasured event counts are unknown, never zero', () => {
  const app = boot();
  app.renderMarketRegime(degradedMarket(), {}, {});
  const watch = app.document.querySelector('#decision-brief .ovx-watch').textContent;
  assert.match(watch, /Earnings calendar not read yet/, 'unknown calendar is not "0 earnings"');
  assert.doesNotMatch(watch, /\b0 earnings/);
  assert.doesNotMatch(watch, /\b0 high-impact/);
});

test('withheld guidance: no headline signal score is printed', () => {
  const app = boot();
  app.renderMarketRegime(degradedMarket(), {}, {});
  app.renderSignalCheck(degradedMarket(), {}, {});
  const body = app.document.querySelector('#signal-check-body');
  assert.match(body.textContent, /not scored/);
  assert.doesNotMatch(body.textContent, /\d\.\d\s*out of 10/);
});

test('an unreachable news tape is never called quiet', () => {
  const app = boot();
  app.renderMarketRegime(degradedMarket(), {}, {});
  const el = app.document.querySelector('#decision-brief');
  const note = [...el.querySelectorAll('.ovx-note')].find((n) => /No news feed is reachable/.test(n.textContent));
  assert.ok(note && !note.classList.contains('pro-only'), 'said in plain English too');
  assert.match(note.textContent, /unknown, not quiet/);
  assert.doesNotMatch(el.textContent, /tape is quiet/);
});

test('the freshness chip reports incomplete data instead of "just now"', () => {
  const app = boot();
  app.state.dashboard = { market: degradedMarket() };
  app.setFreshness(new Date().toISOString());
  assert.equal(app.document.querySelector('#freshness-time').textContent, 'data incomplete');
  assert.equal(app.document.querySelector('#freshness-chip').dataset.staleness, 'stale');
});

test('an unreported multiple is not shown as a measured 0.0', () => {
  const { fmtMultiple } = require('../../static/app.js');
  assert.equal(fmtMultiple(0, 1), 'Not reported');
  assert.equal(fmtMultiple(null, 1), 'Not reported');
  assert.equal(fmtMultiple(-4.2, 1), 'n/m (negative)');
  assert.equal(fmtMultiple(15.24, 1), '15.2');
});

test('Model Evidence is a secondary view: a header icon and the footer link, no tab', () => {
  const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8');
  const doc = new JSDOM(html).window.document;
  assert.equal(doc.querySelector('.nav-tab[data-view="track"]'), null, 'no Evidence tab');
  assert.equal(doc.querySelectorAll('.nav-tab').length, 5);
  const icon = doc.querySelector('.topbar #evidence-link[data-view-link="track"]');
  assert.ok(icon, 'header icon link to Model Evidence');
  assert.equal(icon.getAttribute('href'), '#track');
  assert.match(icon.getAttribute('aria-label'), /evidence/i);
  assert.ok(doc.querySelector('.app-footer [data-view-link="track"]'), 'footer link kept');
});

test('an untested model whose predecessor failed says so everywhere it is named', () => {
  const { evidencePlain } = require('../../static/app.js');
  const ev = { status: 'UNTESTED', lineage: [{ strategy: 'elite.composite', status: 'NO_EDGE' }] };
  assert.equal(evidencePlain(ev), 'Not yet tested (earlier version: no edge)');
  assert.equal(evidencePlain({ status: 'UNTESTED', lineage: [] }), 'Not yet tested');
});

test('the evidence table lists predecessor results as lineage rows', () => {
  const app = boot();
  app.renderTrackRecord({
    totals: { recorded: 0, resolved: 0, pending: 0 }, groups: [], recent: [], min_sample: 30,
    engines: [{ engine: 'elite', label: 'Deep analysis', recorded: 0, evidence: {
      status: 'UNTESTED', status_text: 'Never validated', summary: 'This version has not been tested.',
      lineage: [{ strategy: 'elite.composite', status: 'NO_EDGE', status_text: 'Tested out of sample — no demonstrated edge',
        summary: 'IC -0.023 (t -1.41)', tested_through: '2026-08-25', change: 'original composite' }] } }],
  });
  const row = app.document.querySelector('[data-lineage="elite.composite"]');
  assert.ok(row, 'lineage row rendered');
  assert.match(row.textContent, /no demonstrated edge/);
  assert.match(row.textContent, /2026-08-25/);
});

test('an empty scan names a VIX substitute as an estimate and shows the exposure it sent', () => {
  const app = boot();
  const scan = { recommendations: [], total_scanned: 5, scan_status: 'complete',
    regime: { type: 'BULL', vix: 13.5, vix_source: 'realized_vol_proxy', vix_is_proxy: true, exposure: 100 } };
  app.renderPicks(scan);
  const text = app.document.querySelector('#picks-results').textContent;
  assert.match(text, /VIX estimate \(no live quote\) 13\.5/);
  assert.match(text, /Suggested exposure 100%/);
  app.renderPicks({ ...scan, regime: { type: 'BULL', vix: null, vix_source: 'default', vix_is_proxy: true, exposure: 100 } });
  assert.doesNotMatch(app.document.querySelector('#picks-results').textContent, /VIX/);
});
