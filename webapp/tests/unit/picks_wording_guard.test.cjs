/**
 * [Calls] Wording guard: the Picks page shows QUANTA's call (Buy, Accumulate,
 * Watchlist) and never "funded", "not funded", "set aside", "on watch" or
 * "rated". Checks what the user sees (every tab over every fixture, the Pro
 * cards, the stage table, the progress stages, CSV, copy text and the Picks
 * view of the page template) and the string literals of the code that writes it.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/picks_wording_guard.test.cjs
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
global.filterWords = app.filterWords;
const pro = require('../../static/elite-pro.js');
const { payload, CASES } = require('../fixtures/picks_calls.cjs');
const TH = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'thresholds.json'), 'utf8'));

// "pro-rated" (a horizon target scaled from a 12-month one) is not a status word.
const BANNED = /(?<![\w-])(?:un)?(?:funded|set aside|on watch|rated)\b/i;
const hits = (where, s) => (BANNED.test(s || '') ? [`${where}: ${String(s).match(new RegExp(`.{0,60}${BANNED.source}.{0,60}`, 'i'))[0]}`] : []);
const shown = (el) => [el.textContent,
  ...[...el.querySelectorAll('[title], [aria-label], [placeholder], [data-tip]')].flatMap((n) => ['title', 'aria-label', 'placeholder', 'data-tip'].map((a) => n.getAttribute(a) || ''))]
  .join(' • ');

const render = (data) => {
  global.window = {};
  try { app.renderPicks(data); } finally { global.window = undefined; }
  return document.querySelector('#picks-results');
};
const stage = (key, entered, passed, threshold) => ({ key, entered, passed, evaluated: entered != null && entered > 0, ...(threshold ? { threshold } : {}) });
const withStages = (data) => ({ ...data, accounting: { ...data.accounting, reached_book: 6, not_analysed: 0,
  warnings: { earnings_calendar_missing: 2 },
  book_stages: [stage('actionable', 6, 4), stage('event', 4, 3), stage('risk_grade', 3, 3, { configured: TH.BOOK.BANNED_GRADES, value: null }),
    stage('score', 3, 3, { configured: TH.SCORE.ACCUMULATE, value: null }), stage('risk_reward', 3, 2, { configured: TH.RR.SCANNER_MIN, value: null }),
    stage('conviction', 2, 2, { configured: TH.BOOK.CONVICTION_FLOOR_SMALL, value: null }), stage('cost', 2, 2, { configured: TH.BOOK.COST_VIABILITY_MULTIPLE, value: null }),
    stage('selection', 2, 1), stage('final_review', 1, 1), stage('mandate', 1, 1)] },
  funnel: { universe_size: 100, opportunities: 6, final: 4, gate_stats: {}, filter_stats: {} } });

const FIXTURES = { full: () => payload(), ...CASES };

test('the guard catches the removed words and leaves "pro-rated" alone', () => {
  for (const bad of ['funded', 'Not funded', 'set aside', 'On watch', 'Rated stocks', 'unfunded']) assert.ok(BANNED.test(bad), bad);
  for (const ok of ['pro-rated to 63 sessions', 'generated', 'Watchlist', 'Screened out', 'Buy or Accumulate']) assert.ok(!BANNED.test(ok), ok);
});

for (const [name, make] of Object.entries(FIXTURES)) {
  test(`what the user sees uses the calls: ${name}`, () => {
    const data = withStages(make());
    const box = render(data);
    // Every tab is in the DOM (hidden ones too); the Pro layer fills its slots.
    globalThis.QuantaPro.injectPicksExtras(data);
    const found = [...hits(`${name} page`, shown(box)),
      ...hits(`${name} stage table`, pro.stageTableCard(data)),
      ...hits(`${name} funnel`, pro.funnelCard(data)),
      ...hits(`${name} CSV`, app.stockPicksCsv(data.stock_picks)),
      ...hits(`${name} copy`, app.pkCopyText(data.stock_picks, 'ba') + app.pkCopyText(data.stock_picks, 'watchlist'))];
    assert.deepEqual(found, []);
  });
}

test('the progress stages and the Picks view of the template use the calls', () => {
  app.renderScanStages({ key: 'complete', terminal: true, stages: [
    { key: 'selecting', label: 'Choosing the universe', state: 'done', done: 100, total: 100 },
    { key: 'book', label: 'Applying the book rules', state: 'done', note: '6 passed the screens, 2 Buy or Accumulate, 2 Watchlist' },
    { key: 'research_check', label: 'Running the Research check', state: 'skipped', note: 'skipped: no name was selected for it' }] });
  assert.deepEqual(hits('stages', shown(document.querySelector('#sp-stages'))), []);
  assert.match(document.querySelector('#sp-stages').textContent, /2 Buy or Accumulate, 2 Watchlist/);
  const view = document.querySelector('#view-picks').cloneNode(true);
  view.querySelector('#picks-results')?.replaceChildren();
  assert.deepEqual(hits('template', shown(view)), []);
});

test('the code that writes the Picks page has no removed word in a shown string', () => {
  // Every pk* function the app exports, the Picks helpers it calls, and the Pro
  // cards that render inside the report. Literals that are keys stay allowed.
  const names = Object.keys(app).filter((k) => /^pk[A-Z]/.test(k) || ['stockPicksCsv', 'renderPicksTableView', 'renderScanStages',
    'eventCheckBannerHtml', 'eventHeldDiagnosticsHtml', 'emptyScanExplanationHtml', 'filterWords'].includes(k));
  const src = names.map((k) => [k, typeof app[k] === 'function' ? app[k].toString() : '']).concat(
    ['stageTableCard', 'funnelCard', 'disagreementWatchlistCard', 'portfolioRiskCard', 'executionCaveatsCard', 'capitalAllocationTable']
      .map((k) => [`pro.${k}`, typeof pro[k] === 'function' ? pro[k].toString() : '']));
  assert.ok(src.filter(([, s]) => s).length >= 20, 'the guard reads the Picks code');
  const literal = /'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`/g;
  const found = [];
  for (const [k, s] of src) {
    for (const m of s.matchAll(literal)) {
      const body = (m[1] ?? m[2] ?? m[3] ?? '').replace(/\$\{[^}]*\}/g, ' ');
      if (/^[a-z0-9_]+$/.test(body)) continue;                  // a key or enum value
      found.push(...hits(k, body.replace(/\b(?:is|data|pk|pick|qpro)-[\w-]+/g, ' ')));   // class and data-attribute names
    }
  }
  assert.deepEqual(found, []);
});

test('the legacy Picks path, the Overview brief, the Guide and the whole template use the calls', () => {
  // Older saved results without stock_picks still render through renderPicks;
  // the Overview brief and the Guide describe Picks too. Read their source.
  const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  const body = (name) => {
    const at = src.search(new RegExp(`\\n(?:async )?function ${name}\\(`));
    assert.ok(at >= 0, `found ${name}`);
    const end = src.slice(at + 1).search(/\n(?:async )?function |\nconst [A-Z_]+ = /);
    return src.slice(at, end < 0 ? undefined : at + 1 + end).replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  };
  const literal = /'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`/g;
  const found = [];
  // renderPicks and pickCard nest templates the literal scan cannot split, so
  // they are rendered instead: the same fixtures without stock_picks.
  for (const [name, make] of Object.entries(FIXTURES)) {
    const { stock_picks: _drop, ...legacy } = withStages(make());
    const page = shown(render(legacy));
    assert.ok(page.length > 200, `${name} legacy page rendered`);
    found.push(...hits(`${name} legacy page`, page));
  }
  for (const k of ['compactPickRow', 'passedNotFundedHtml', 'renderOpportunityBrief',
    'renderTopOpportunities', 'guideSize', 'renderGuideFunnel', 'actionMeta']) {
    for (const m of body(k).matchAll(literal)) {
      const b = (m[1] ?? m[2] ?? m[3] ?? '').replace(/\$\{[^}]*\}/g, ' ');
      if (/^[a-z0-9_]+$/.test(b)) continue;
      found.push(...hits(k, b.replace(/\b(?:is|data|pk|pick|qpro|qr)-[\w-]+/g, ' ')));
    }
  }
  const page = new JSDOM(html).window.document.body;
  page.querySelectorAll('script, style').forEach((n) => n.remove());
  found.push(...hits('index.html', page.textContent.replace(/\s+/g, ' ')));
  for (const n of page.querySelectorAll('[title], [aria-label], [placeholder]')) {
    for (const a of ['title', 'aria-label', 'placeholder']) found.push(...hits(`index.html @${a}`, n.getAttribute(a)));
  }
  assert.deepEqual(found, []);
});
