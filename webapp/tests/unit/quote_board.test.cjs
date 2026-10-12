/**
 * QUANTA Elite — quote board unit coverage (node --test).
 *
 * Covers the exchange-style session line (Open / High / Low / Prev Close / LTP)
 * shared by Key Market Moves and the Deep-Dive hero, plus the per-share price
 * formatter that replaced the market-cap-shaped fmtCurrency on price fields.
 *
 * Run:  node --test tests/unit/quote_board.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');

const {
  fmtPrice,
  fmtCount,
  normalizeQuote,
  rangePosition,
  quoteBoardHtml,
  rangeMeterHtml,
} = require('../../static/app.js');

// ---- fmtPrice · a quote is never compacted -------------------------------
test('fmtPrice never compacts a per-share price into K/M/B', () => {
  // The bug this guards: fmtCurrency(1450) => "$1.45K", which is correct for
  // market cap and flatly wrong for an Indian large-cap's traded price.
  assert.equal(fmtPrice(1450.5, '₹'), '₹1,450.50');
  assert.equal(fmtPrice(2456789.25, '$'), '$2,456,789.25');
  assert.equal(fmtPrice(140.2, '$'), '$140.20');
});

test('fmtPrice widens decimals for sub-unit instruments', () => {
  // 2dp would round a real 0.0042 quote to "0.00" and imply a worthless asset.
  assert.equal(fmtPrice(0.0042, '$'), '$0.0042');
  assert.equal(fmtPrice(0.85, '$'), '$0.8500');
  assert.equal(fmtPrice(1.5, '$'), '$1.50');
});

test('fmtPrice guards NaN / null / undefined / Infinity', () => {
  for (const bad of [NaN, null, undefined, '', Infinity, -Infinity, 'abc']) {
    assert.equal(fmtPrice(bad), '--', `fmtPrice(${String(bad)}) must be "--"`);
  }
  assert.equal(fmtPrice(0, '$'), '$0.00', 'a genuine zero is still a number');
});

test('fmtCount renders share counts, not currency', () => {
  assert.equal(fmtCount(2.1e8), '210.00M');
  assert.equal(fmtCount(1.25e9), '1.25B');
  assert.equal(fmtCount(9500), '9.5K');
  assert.equal(fmtCount(450), '450');
  for (const bad of [0, -5, null, undefined, NaN, 'abc']) {
    assert.equal(fmtCount(bad), null, `fmtCount(${String(bad)}) must be null`);
  }
});

// ---- normalizeQuote · every field is a finite number or null --------------
test('normalizeQuote passes through a full provider quote', () => {
  const q = normalizeQuote({
    open: 408.2, day_high: 417.75, day_low: 406.5,
    prev_close: 401.6, price: 416.8, change_abs: 15.2, change_pct: 3.78,
    volume: 1.2e7, week52_low: 340.1, week52_high: 470.0,
  });
  assert.equal(q.open, 408.2);
  assert.equal(q.high, 417.75);
  assert.equal(q.low, 406.5);
  assert.equal(q.prevClose, 401.6);
  assert.equal(q.ltp, 416.8);
  assert.equal(q.rangeLow, 340.1);
  assert.equal(q.rangeHigh, 470.0);
});

test('normalizeQuote reconstructs prev close from price + day change', () => {
  // The broad-universe fallback feed ships price and change_pct only.
  const q = normalizeQuote({ price: 110, change_pct: 10 });
  assert.ok(Math.abs(q.prevClose - 100) < 1e-9, `prevClose was ${q.prevClose}`);
  assert.ok(Math.abs(q.changeAbs - 10) < 1e-9, `changeAbs was ${q.changeAbs}`);
});

test('normalizeQuote derives the change percent from prev close', () => {
  const q = normalizeQuote({ price: 120, prev_close: 100 });
  assert.ok(Math.abs(q.changePct - 20) < 1e-9, `changePct was ${q.changePct}`);
  assert.ok(Math.abs(q.changeAbs - 20) < 1e-9, `changeAbs was ${q.changeAbs}`);
});

test('normalizeQuote nulls unusable fields instead of emitting NaN', () => {
  const q = normalizeQuote({ price: 'abc', open: '', day_high: null, day_low: undefined });
  for (const [k, v] of Object.entries(q)) {
    if (k === 'date' || k === 'prevCloseReported') continue;
    assert.ok(v === null || Number.isFinite(v), `${k} leaked a non-finite value: ${v}`);
  }
});

test('a derived prev close does not qualify a price-only row for a board', () => {
  // price + change_pct is enough to reconstruct prevClose, but a board built
  // from it would be four em-dashes and one number — worse than the stats line.
  const derived = normalizeQuote({ price: 110, change_pct: 10 });
  assert.equal(derived.prevCloseReported, false);
  assert.equal(quoteBoardHtml(derived, '$'), '');

  // A prev close the feed actually reported does qualify.
  const reported = normalizeQuote({ price: 110, prev_close: 100 });
  assert.equal(reported.prevCloseReported, true);
  assert.ok(quoteBoardHtml(reported, '$').includes('Prev Close'));
});

test('quote delta puts the sign before the currency symbol', () => {
  const html = quoteBoardHtml(normalizeQuote({ price: 95, prev_close: 100, open: 99 }), '$');
  assert.ok(html.includes('-$5.00'), `expected "-$5.00" in delta, got: ${html}`);
  assert.ok(!html.includes('$-5.00'), 'must not render the sign after the symbol');
});

test('quote delta inherits the price precision, not its own magnitude', () => {
  // A -0.81 move on a $12 stock is "-$0.81". Deriving decimals from the delta's
  // own magnitude would print "-$0.8100" and imply four-figure precision.
  const html = quoteBoardHtml(normalizeQuote({ price: 12.10, prev_close: 12.91, open: 12.85 }), '$');
  assert.ok(html.includes('-$0.81'), `expected "-$0.81", got: ${html}`);
  assert.ok(!html.includes('-$0.8100'), 'delta must not widen to 4dp on a >=1 price');

  // A sub-unit instrument keeps the wider precision on both price and delta.
  const penny = quoteBoardHtml(normalizeQuote({ price: 0.0420, prev_close: 0.0385, open: 0.0390 }), '$');
  assert.ok(penny.includes('$0.0420'), 'sub-unit price keeps 4dp');
  assert.ok(penny.includes('+$0.0035'), `sub-unit delta keeps 4dp, got: ${penny}`);
});

test('normalizeQuote repairs an inverted high/low band', () => {
  const q = normalizeQuote({ day_low: 417.75, day_high: 406.5, price: 410 });
  assert.equal(q.low, 406.5);
  assert.equal(q.high, 417.75);
});

test('normalizeQuote falls back to the tear-sheet payload when session is empty', () => {
  const q = normalizeQuote({}, { price: 250, change_pct: -2 });
  assert.equal(q.ltp, 250);
  assert.ok(q.prevClose > 250, 'a down day implies a higher previous close');
});

// ---- rangePosition · degenerate bands never divide by zero ----------------
test('rangePosition places a value inside its band and clamps outside it', () => {
  assert.equal(rangePosition(100, 200, 150), 50);
  assert.equal(rangePosition(100, 200, 100), 0);
  assert.equal(rangePosition(100, 200, 200), 100);
  assert.equal(rangePosition(100, 200, 500), 100, 'clamped, never > 100');
  assert.equal(rangePosition(100, 200, 10), 0, 'clamped, never < 0');
});

test('rangePosition returns null for a degenerate or unusable band', () => {
  assert.equal(rangePosition(100, 100, 100), null, 'zero-width band');
  assert.equal(rangePosition(200, 100, 150), null, 'inverted band');
  assert.equal(rangePosition(null, 200, 150), null);
  assert.equal(rangePosition(100, 200, NaN), null);
});

// ---- quoteBoardHtml · renders the reference columns, degrades honestly ----
test('quoteBoardHtml renders all five exchange columns', () => {
  const html = quoteBoardHtml(normalizeQuote({
    open: 408.2, day_high: 417.75, day_low: 406.5, prev_close: 401.6, price: 416.8,
  }), '₹');
  for (const label of ['Open', 'High', 'Low', 'Prev Close', 'Last']) {
    assert.ok(html.includes(`>${label}<`), `missing the ${label} column`);
  }
  assert.ok(html.includes('₹417.75'), 'high rendered with the market currency');
  assert.ok(html.includes('₹416.80'), 'LTP rendered with the market currency');
  assert.ok(!/undefined|NaN|\$NaN/.test(html), `leaked a bad value: ${html}`);
});

test('quoteBoardHtml keeps missing cells aligned rather than dropping them', () => {
  const html = quoteBoardHtml(normalizeQuote({ price: 100, prev_close: 98, open: 99 }), '$');
  assert.ok(html.includes('>Low<'), 'the Low column keeps its slot');
  assert.ok(html.includes('qb-na'), 'the absent value renders as a muted placeholder');
  assert.ok(!/undefined|NaN/.test(html));
});

test('quoteBoardHtml returns empty when there is no OHLC worth showing', () => {
  // Price alone is not a quote board — five em-dashes would be noise.
  assert.equal(quoteBoardHtml(normalizeQuote({ price: 100 }), '$'), '');
  assert.equal(quoteBoardHtml(null, '$'), '');
});

test('quoteBoardHtml tones the LTP cell by direction', () => {
  const up = quoteBoardHtml(normalizeQuote({ price: 110, prev_close: 100, open: 101 }), '$');
  assert.ok(/qb-ltp\s+up/.test(up), 'a gain tints the LTP cell bullish');
  const down = quoteBoardHtml(normalizeQuote({ price: 90, prev_close: 100, open: 99 }), '$');
  assert.ok(/qb-ltp\s+dn/.test(down), 'a loss tints the LTP cell bearish');
});

test('quoteBoardHtml escapes a hostile currency symbol', () => {
  const html = quoteBoardHtml(normalizeQuote({ open: 1, day_high: 2, day_low: 1, price: 2 }), '<img onerror=x>');
  assert.ok(!html.includes('<img'), 'currency symbol must not inject markup');
});

// ---- rangeMeterHtml ------------------------------------------------------
test('rangeMeterHtml renders a labelled, accessible band', () => {
  const html = rangeMeterHtml(406.5, 417.75, 416.8, '₹', 'Day Range');
  assert.ok(html.includes('Day Range'));
  assert.ok(html.includes('aria-label'), 'the meter is described for screen readers');
  assert.ok(html.includes('₹406.50') && html.includes('₹417.75'), 'both edges labelled');
  assert.ok(!/undefined|NaN/.test(html));
});

test('rangeMeterHtml renders nothing for a degenerate band', () => {
  assert.equal(rangeMeterHtml(100, 100, 100, '$', 'Day'), '');
  assert.equal(rangeMeterHtml(null, 200, 150, '$', 'Day'), '');
});
