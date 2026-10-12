// Tests for the QUANTA Elite Pro "Scanner pick not confirmed" watchlist card.
//
// Covers Case E of the data-reliability fix: a real ticker (e.g. C) whose deep
// validation failed for a DATA reason must render as "NOT CONFIRMED" /
// "Not independently confirmed" — never "INVALID" — and the score cell must
// never show a bare "--/10".
//
// Run: node --test webapp/tests/

const test = require('node:test');
const assert = require('node:assert/strict');

const { disagreementWatchlistCard } = require('../static/elite-pro.js');

test('not-confirmed row labels analyzer as NOT CONFIRMED, never INVALID', () => {
  const data = {
    analyzer_disagreement_watchlist: [{
      ticker: 'C',
      company: 'Citigroup',
      sector: 'Financials',
      scanner_signal: 'BUY',
      row_kind: 'not_confirmed',
      not_independently_confirmed: true,
      validation_status: 'Not independently confirmed',
      scanner_score: 7.4,
      analyzer_score: null,
      score_reason: 'Deep analyzer could not fetch historical bars (provider issue).',
      reason: 'Deep analyzer could not fetch enough historical bars for C (data provider issue).',
    }],
  };
  const html = disagreementWatchlistCard(data);
  assert.match(html, /Scanner pick not confirmed/); // sentence case since the styling pass
  assert.match(html, /NOT CONFIRMED/);
  assert.match(html, /Not independently confirmed/);
  // Must NOT mislabel a real ticker as INVALID.
  assert.doesNotMatch(html, /INVALID/);
  // Score: scanner score shown with sublabel, never a bare "--/10".
  assert.match(html, /74\/100/);
  assert.doesNotMatch(html, /--\/10/);
});

test('data failure with no scanner score shows N/A, not --/10', () => {
  const data = {
    analyzer_disagreement_watchlist: [{
      ticker: 'FFBC',
      company: 'First Financial Bancorp',
      sector: 'Financials',
      scanner_signal: 'STRONG_BUY',
      row_kind: 'not_confirmed',
      not_independently_confirmed: true,
      validation_status: 'Not independently confirmed',
      scanner_score: null,
      analyzer_score: null,
      score_reason: 'Provider rate-limited.',
      reason: 'Yahoo Finance rate-limited the request.',
    }],
  };
  const html = disagreementWatchlistCard(data);
  assert.match(html, />N\/A</);
  assert.doesNotMatch(html, /--\/10/);
  assert.doesNotMatch(html, /INVALID/);
});

test('a genuine analyzer disagreement still shows the analyzer decision', () => {
  const data = {
    analyzer_disagreement_watchlist: [{
      ticker: 'XYZ',
      company: 'XYZ Corp',
      sector: 'Tech',
      scanner_signal: 'BUY',
      analyzer_signal: 'SELL',
      row_kind: 'disagreement',
      not_independently_confirmed: false,
      validation_status: 'Analyzer disagrees',
      scanner_score: 6.0,
      analyzer_score: 3.2,
      reason: 'Valuation stretched; momentum rolling over.',
    }],
  };
  const html = disagreementWatchlistCard(data);
  assert.match(html, /SELL/);
  assert.match(html, /Analyzer disagrees/);
  assert.match(html, /32\/100/);
});

test('empty watchlist renders nothing', () => {
  assert.equal(disagreementWatchlistCard({ analyzer_disagreement_watchlist: [] }), '');
  assert.equal(disagreementWatchlistCard({}), '');
});

const pro = require('../static/elite-pro.js');

test('funded means an allocating resolved action with capital attached', () => {
  assert.equal(pro.isFundedRec({ final_action: 'BUY', allocation_after: 1200 }), true);
  assert.equal(pro.isFundedRec({ final_action: 'WATCHLIST', allocation_after: 0 }), false);
  assert.equal(pro.isFundedRec({ final_action: 'BUY', allocation_after: 0 }), false);
  assert.equal(pro.isFundedRec({ decision: { action: 'WATCHLIST' }, final_action: 'BUY', allocation: 900 }), false,
    'the Decision outranks a stale final_action');
});

test('entry caveats are omitted when nothing is being entered', () => {
  const watchOnly = { recommendations: [{ final_action: 'WATCHLIST', allocation_after: 0 }], regime: {} };
  assert.equal(pro.executionCaveatsCard(watchOnly), '');
  const funded = { recommendations: [{ final_action: 'BUY', allocation_after: 1000 }], regime: {} };
  assert.match(pro.executionCaveatsCard(funded), /Pre-trade review/);
});

test('the funnel never calls a deep-downgraded pick "confirmed"', () => {
  const html = pro.funnelCard({
    recommendations: [{ final_action: 'WATCHLIST', allocation_after: 0 }, { final_action: 'WATCHLIST', allocation_after: 0 }],
    funnel: { universe_size: 100, opportunities: 2, final: 2, gate_stats: { gate7_sector_cap: 0 }, filter_stats: {} },
  });
  assert.doesNotMatch(html, /Confirmed by Deep Analysis/);
  assert.match(html, /0 sized · 2 Watchlist after deep review/);
  assert.match(html, /Provisionally sized/);
  assert.match(html, /Sized/);
  assert.doesNotMatch(html, /Funded/);
  assert.match(html, /width:0%/);
});

test('capital table renders the evidence-qualified Decision label', () => {
  const html = pro.capitalAllocationTable({ capital: 1000, recommendations: [{
    ticker: 'TEST', sector: 'Tech', final_action: 'BUY', allocation_after: 100,
    shares: 1, entry_price: 100, stop_loss: 90,
    decision: { action: 'BUY', label: 'BUY', qualifier: 'Unproven model' },
  }] });
  assert.match(html, />BUY<\/span><span class="qpro-qual" title="Unproven model">Model not yet validated<\/span>/);
});

test('filter share is measured against all rejected tickers', () => {
  const html = pro.funnelCard({ recommendations: [], funnel: {
    universe_size: 100, opportunities: 20, final: 0,
    gate_stats: {}, filter_stats: { momentum: 40, volume: 20 },
  } });
  assert.match(html, /40 · 50%/);
  assert.match(html, /20 · 25%/);
});
