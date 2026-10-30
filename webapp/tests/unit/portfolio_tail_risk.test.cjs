// My Portfolio "Tail risk" block: shown from metrics.tail_risk, hidden when absent.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'my_portfolio_view.json'), 'utf8'));
const hp = require('../../static/my-portfolio.js');

const clone = (o) => JSON.parse(JSON.stringify(o));
const frag = (h) => new JSDOM(`<body>${h}</body>`).window.document;
const TAIL = {
  confidence: 0.95, horizon_days: 1, days: 250, coverage: 1, var_pct: 0.021, cvar_pct: 0.034,
  var_value: 2100, cvar_value: 3400, reason: null,
  worst_window: { sessions: 21, return: -0.12, value: -12000, end: '2025-04-08' },
  scenarios: [
    { market_move: -0.1, book_pct: -0.12, value: -12000, account_pct: -0.1 },
    { market_move: -0.2, book_pct: -0.24, value: -24000, account_pct: -0.2 },
  ],
};

test('tail risk block shows VaR, CVaR, worst window and both scenarios with the caption', () => {
  const v = clone(fixture);
  v.portfolio.metrics.tail_risk = clone(TAIL);
  const el = frag(hp.portfolioHtml(v)).querySelector('[data-hp="tail-risk"]');
  assert.ok(el, 'block rendered');
  const text = el.textContent;
  assert.match(text, /1-day VaR 95%/);
  assert.match(text, /2\.1%/);
  assert.match(text, /3\.4%/);
  assert.match(text, /Worst 21 sessions/);
  assert.match(text, /-12\.0%/);
  assert.match(text, /Market -10%/);
  assert.match(text, /Market -20%/);
  assert.match(text, /Historical, from the last 250 sessions of your current weights; not a forecast\./);
});

test('tail risk block is absent when the server sent no tail_risk or only empty fields', () => {
  const v = clone(fixture);
  delete v.portfolio.metrics.tail_risk;
  assert.equal(frag(hp.portfolioHtml(v)).querySelector('[data-hp="tail-risk"]'), null);
  v.portfolio.metrics.tail_risk = { var_pct: null, cvar_pct: null, worst_window: null, scenarios: [], reason: 'No daily return history available.' };
  assert.equal(frag(hp.portfolioHtml(v)).querySelector('[data-hp="tail-risk"]'), null);
});

test('tail risk text from the server is escaped', () => {
  const v = clone(fixture);
  v.portfolio.metrics.tail_risk = Object.assign(clone(TAIL), { var_pct: null, cvar_pct: null, reason: '<img src=x onerror=alert(1)>' });
  const el = frag(hp.portfolioHtml(v)).querySelector('[data-hp="tail-risk"]');
  assert.ok(el);
  assert.equal(el.querySelector('img'), null);
  assert.match(el.textContent, /<img src=x/);
});
