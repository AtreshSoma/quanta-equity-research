// Profit plan tab (mp-profit.js), rendered against profit_plan.json: the REAL
// portfolio/profit_plan.py over the synthetic My Portfolio fixture (make_profit_plan_fixture.py).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const read = (f) => JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', f), 'utf8'));
const view = read('my_portfolio_view.json');
const plan = read('profit_plan.json');

const hp = require('../../static/my-portfolio.js');
globalThis.QuantaCharts = require('../../static/mp-charts.js');
require('../../static/mp-insights.js').attach(hp);
require('../../static/mp-profit.js').attach(hp);
const X = hp.ext;
const ins = X.ins;

const clone = (o) => JSON.parse(JSON.stringify(o));
const frag = (h) => new JSDOM(`<body>${h}</body>`).window.document;
// Visible words in reading order, one space between text nodes (markup puts none).
const words = (el) => {
  const doc = el.ownerDocument || el;
  const walk = doc.createTreeWalker(el.body || el, 4);
  const out = [];
  for (let n = walk.nextNode(); n; n = walk.nextNode()) { const t = n.textContent.replace(/\s+/g, ' ').trim(); if (t) out.push(t); }
  return out.join(' ');
};
const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');

function render(data = plan) {
  hp._state.view = view;
  hp._state.hidden = false;
  ins.profit = { s: 'ok', data: clone(data), stamp: view.portfolio.retrieved_at };
  hp._state.tab = 'profit';
  return frag(hp.portfolioHtml(view));
}

test('a Profit plan tab sits after Guidance and controls its own panel', () => {
  const d = render();
  const tabs = [...d.querySelectorAll('[role="tab"]')].map((t) => t.dataset.hpTab);
  assert.deepEqual(tabs.slice(0, 4), ['overview', 'holdings', 'guidance', 'profit']);
  const panel = d.getElementById('hp-panel-profit');
  assert.equal(panel.hidden, false);
  assert.equal(panel.getAttribute('aria-labelledby'), 'hp-tab-profit');
  // The count is what to act on: holdings below a stop plus those with a booking rule.
  assert.equal(X.counts(view.portfolio).profit, plan.signals.STOP_HIT + plan.signals.BOOK);
});

test('the hero splits the open gain at the stops and adds up', () => {
  const d = render();
  const hero = d.querySelector('.pp-hero');
  const t = plan.totals;
  assert.ok(Math.abs(t.locked + t.exposed + t.unprotected - t.gain) < 0.01, 'locked + exposed is the whole gain');
  const segs = [...hero.querySelectorAll('.pp-seg')].map((s) => parseFloat(s.style.getPropertyValue('--w')));
  assert.equal(segs.length, 2);
  assert.ok(Math.abs(segs[0] + segs[1] - 100) < 0.01);
  assert.match(words(hero.querySelector('.pp-lead')), /^\$24,964 of your \$27,872 in open gains sits under a profit stop\. \$2,908 is exposed/);
  assert.match(words(hero.querySelector('.pp-book')), /Sell \$26,398 4 holdings/);
  assert.match(words(hero.querySelector('.pp-book')), /Net gain if you do both \+\$12,281/);
  // Stop hits are conditional on the close and named apart from "book now".
  assert.match(words(hero.querySelector('.pp-watch')), /^Watch the close\. MSFT, KO are trading below their profit stop\. If they close there, the rule books 50% of each/);
  assert.ok(hero.querySelector('[role="img"][aria-label*="percent is under a profit stop"]'));
});

test('the gain map has one row per holding, ordered by urgency, each opening its chart and rule', () => {
  const d = render();
  const rows = [...d.querySelectorAll('.pp-row')];
  assert.equal(rows.length, view.portfolio.positions.length);
  assert.deepEqual(rows.map((r) => r.querySelector('.pp-chip').textContent),
    plan.holdings.map((h) => h.signal.label));
  assert.equal(rows[0].querySelector('.pp-chip').className, 'pp-chip pp-chip--stop_hit');
  const msft = rows.find((r) => r.dataset.hpDisclosure === 'pp-MSFT');
  const blocks = [...msft.querySelectorAll('.pp-sale')].map(words);
  assert.match(blocks[0], /^If it closes below the stop .* Sell [\d.]+ shares, about \$[\d,]+/);
  // A booking rule that fires too keeps its own block.
  assert.match(blocks[1], /^Booking rule /);
  // The chart: price, stop and cost, labelled at the right edge, with an accessible summary.
  const fig = msft.querySelector('.pp-chart');
  assert.match(fig.getAttribute('aria-label'), /^MSFT over 126 sessions: price \$[\d,.]+, profit stop \$[\d,.]+, your average cost/);
  assert.deepEqual([...fig.querySelectorAll('.pp-end b')].map((b) => b.textContent).sort(), ['Cost', 'Price', 'Stop', 'Target']);
  assert.ok(fig.querySelector('path.pp-l-price').getAttribute('d').startsWith('M0.0,'));
  assert.ok(fig.querySelector('path.pp-z-exposed') || fig.querySelector('path.pp-z-locked'));
  // A tax-loss candidate says how long to stay out to keep the loss.
  const rivn = rows.find((r) => r.dataset.hpDisclosure === 'pp-RIVN');
  assert.match(words(rivn.querySelector('.pp-sale')), /To keep the loss, do not buy it back before 2026-10-29/);
  // No history: said, not drawn.
  const brk = rows.find((r) => r.dataset.hpDisclosure === 'pp-BRK.B');
  assert.match(words(brk), /Not enough price history for a chart/);
});

test('the sector path switches between tax-cheapest and pro rata, and the totals add up', () => {
  const d = render();
  const p = plan.sector_paths[0];
  const sec = d.querySelector('.pp-paths');
  assert.match(words(sec.querySelector('.q-head-title')), new RegExp(`^${p.sector} back inside its 30% limit$`));
  const sum = p.cheapest.legs.reduce((t, l) => t + l.value, 0);
  assert.ok(Math.abs(sum - p.excess_value) < 1);
  assert.equal(sec.querySelector('[data-hp-x="pp-path:cheapest"]').getAttribute('aria-pressed'), 'true');
  X.actions['pp-path']('pro_rata');
  try {
    const d2 = render();
    assert.equal(d2.querySelector('[data-hp-x="pp-path:pro_rata"]').getAttribute('aria-pressed'), 'true');
    assert.equal(d2.querySelectorAll('.pp-legs tbody tr').length, p.pro_rata.legs.length);
  } finally { X.actions['pp-path']('cheapest'); }
});

test('hidden amounts mask every account dollar on the tab', () => {
  const d = render();
  hp._state.hidden = true;
  try {
    const h = frag(X.profitBody(view.portfolio));
    const text = words(h.body).replace(/\$[\d,.]+ (?=\(|\/|·)|Price \$[\d,.]+|Stop \$[\d,.]+|Cost \$[\d,.]+|Target \$[\d,.]+/g, '');
    for (const sel of ['.pp-lead', '.pp-book', '.pp-gain', '.pp-legs tbody']) {
      assert.doesNotMatch(words(h.querySelector(sel)), /\$\d/, `${sel} hides amounts`);
    }
    assert.ok(text.length > 0);
  } finally { hp._state.hidden = false; }
  assert.ok(d);
});

test('the rules are stated with their settings and the plan says what it is not', () => {
  const d = render();
  const rules = words(d.querySelector('.pp-rules'));
  assert.match(rules, /highest close of the last 63 sessions minus 8 average daily moves/);
  assert.match(rules, /Past the 20% review level: sell down to the 15% trim target/);
  assert.match(rules, /QUANTA has not validated them as an edge, and none is an order/);
  assert.match(rules, /not tax advice/);
});

test('loading, a failed build and an older server each say so instead of drawing a guess', () => {
  hp._state.view = view;
  ins.profit = { s: 'loading' };
  assert.match(words(frag(X.profitBody(view.portfolio)).body), /Building the profit plan/);
  ins.profit = { s: 'error', data: null };
  assert.ok(frag(X.profitBody(view.portfolio)).querySelector('[data-hp-x="pp-retry"]'));
  ins.profit = { s: 'missing', data: null };
  assert.match(words(frag(X.profitBody(view.portfolio)).body), /predates the profit plan/);
});

test('the profit plan stylesheet keeps the page rules: amber for actions, red for losses only', () => {
  const block = css.slice(css.indexOf('/* Profit plan (#holdings/profit)'), css.indexOf('HOW QUANTA WORKS (#guide)'));
  assert.ok(block.length > 2000);
  const nonLoss = block.replace(/\.pp-(key\.is-loss|down|bar-l i\.is-loss|legs td\.pp-down) \{[^}]*}/g, '');
  assert.doesNotMatch(nonLoss, /--bear/);
  assert.doesNotMatch(block, /!important|gradient|backdrop-filter/);
  assert.match(block, /prefers-reduced-motion: reduce/);
});
