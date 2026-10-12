// Isolated UI test server. Synthetic portfolio fixture; no broker or market calls.
// Run: node tests/tools/portfolio-preview.cjs (PORT=5617, MARKET=0 for no index quotes, BOOK=18 for the 18-holding book,
// ACTIVITY=<scenario> to serve tests/fixtures/activity/<scenario>.json to the Activity tab; pin the page's
// clock to that file's "now" when looking at it, e.g. portfolio-shots.cjs with NOW=<iso>).
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const book = process.env.BOOK ? `_${process.env.BOOK}` : '';
const fixture = JSON.parse(fs.readFileSync(path.join(root, `tests/fixtures/my_portfolio_view${book}.json`), 'utf8'));
const status = { ...fixture.status, account: fixture.portfolio.account.handle };
// Insight endpoints: output of the real Insights code over synthetic prices (make_insights_fixture.py).
// FAIL=perf,events makes those endpoints error, to check partial-failure states.
const insights = JSON.parse(fs.readFileSync(path.join(root, `tests/fixtures/my_portfolio_insights${book}.json`), 'utf8'));
const failing = new Set(String(process.env.FAIL || '').split(',').filter(Boolean));
const INSIGHT = { '/api/portfolio/performance': 'performance', '/api/portfolio/intraday': 'intraday', '/api/portfolio/exposure': 'exposure', '/api/portfolio/events': 'events',
  '/api/portfolio/outcomes': 'outcomes', '/api/portfolio/activity': 'activity', '/api/portfolio/records': 'records', '/api/portfolio/plan': 'plan', '/api/portfolio/journal': 'journal' };
fixture.portfolio.journal = insights.journal;
// MARKET=0 serves no index quotes, to check the page without a market comparison.
const tape = process.env.MARKET === '0' ? {} : JSON.parse(fs.readFileSync(path.join(root, 'tests/fixtures/market_tape.json'), 'utf8'));
// The tape is dated to the standard book's session; the 18-holding book closed on Sep 29.
if (book && tape.market) tape.market.quotes.forEach((q) => { q.quote_date = '2026-09-29'; q.as_of = '2026-09-29T16:00:00-04:00'; });
// FILLS=0 keeps the fixture's "no order history". Otherwise a deterministic synthetic
// order history: a buy for every open lot, plus closed round trips over the past year,
// so the Activity ledger (sales, purchases, FIFO realized) has something to show.
if (process.env.FILLS !== '0' && insights.records && insights.records.fills == null) {
  let seed = 7;
  const rand = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
  const fills = [];
  const iso = (d) => d.toISOString().slice(0, 19) + 'Z';
  const at = (day, minute) => { const d = new Date(`${day}T13:30:00Z`); d.setUTCMinutes(d.getUTCMinutes() + minute); return iso(d); };
  (insights.records.lots || []).forEach((l) => {
    if (l.acquired_on && l.quantity && l.cost_per_share) fills.push({ symbol: l.symbol, side: 'buy', quantity: l.quantity, price: l.cost_per_share, at: at(l.acquired_on, Math.floor(rand() * 380)) });
  });
  // Closed round trips, all generated from the seed: synthetic tickers (SY*), sizes,
  // prices and dates within the year before the fixture's session.
  const day = (back) => new Date(Date.parse('2026-09-25T12:00:00Z') - back * 864e5).toISOString().slice(0, 10);
  const cents = (v) => Math.round(v * 100) / 100;
  const trips = 'ABCDEFGHI'.split('').map((c) => {
    const q = 1 + Math.floor(rand() * 5);
    const buy = cents(20 + rand() * 480);
    const sell = cents(buy * (0.75 + rand() * 0.6));
    const held = 30 + Math.floor(rand() * 200);
    const closedBack = 5 + Math.floor(rand() * 150);
    return [`SY${c}`, q, buy, sell, day(closedBack + held), day(closedBack)];
  });
  trips.forEach(([sym, q, buy, sell, d1, d2]) => {
    fills.push({ symbol: sym, side: 'buy', quantity: q, price: buy, at: at(d1, Math.floor(rand() * 380)) });
    fills.push({ symbol: sym, side: 'sell', quantity: q, price: sell, at: at(d2, Math.floor(rand() * 380)) });
  });
  // A sale of shares bought before the history begins: realized is not computable.
  const orphan = { symbol: 'SYZ', quantity: 2, price: cents(50 + rand() * 200), day: day(190), gain: cents(20 + rand() * 80) };
  fills.push({ symbol: orphan.symbol, side: 'sell', quantity: orphan.quantity, price: orphan.price, at: at(orphan.day, 45) });
  fills.sort((a, b) => b.at.localeCompare(a.at));
  // Robinhood's closing-trade records for the same sales (realized per trade).
  const closed = trips.map(([sym, q, buy, sell, , d2]) => {
    const f = fills.find((x) => x.symbol === sym && x.side === 'sell');
    return { symbol: sym, side: 'sell', quantity: q, price: sell, realized_gain: Math.round((sell - buy) * q * 100) / 100, at: f.at, closed_at: f.at.slice(0, 10), opened_at: null };
  });
  closed.push({ symbol: orphan.symbol, side: 'sell', quantity: orphan.quantity, price: orphan.price, realized_gain: orphan.gain, at: at(orphan.day, 45), closed_at: orphan.day, opened_at: null });
  closed.sort((a, b) => b.at.localeCompare(a.at));
  insights.records = { ...insights.records, fills, fills_rejected: 0, closed_trades: process.env.TRADES === '0' ? null : closed };
}
if (process.env.ACTIVITY) {
  const act = JSON.parse(fs.readFileSync(path.join(root, `tests/fixtures/activity/${process.env.ACTIVITY}.json`), 'utf8'));
  insights.records = act.records;
  insights.activity = act.activity;
  console.log(`Activity scenario ${act.scenario}; its clock: ${act.now}`);
}
const thresholds = fs.readFileSync(path.join(root, 'tests/fixtures/thresholds.json'), 'utf8');
const port = Number(process.env.PORT || 5617);
const types = { '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.webp': 'image/webp' };
http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  res.setHeader('Cache-Control', 'no-store');
  if (url.pathname === '/') {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    return res.end(fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8')
      .replace(/\{\{\s*asset_url\('([^']+)'\)\s*\}\}/g, '/static/$1')
      .replace(/\{\{\s*thresholds_json\|safe\s*\}\}/, thresholds));
  }
  if (url.pathname.startsWith('/static/')) {
    const file = path.resolve(root, '.' + decodeURIComponent(url.pathname));
    if (!file.startsWith(path.join(root, 'static') + path.sep) || !fs.existsSync(file)) {
      res.writeHead(404); return res.end();
    }
    res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
    return fs.createReadStream(file).pipe(res);
  }
  res.setHeader('Content-Type', 'application/json');
  // What-if: the real PortfolioService answers, recorded per combination of scenarios.
  if (url.pathname === '/api/portfolio/whatif') {
    if (process.env.STALE === '1') { res.writeHead(404); return res.end(JSON.stringify({ error: 'Not found' })); }
    let raw = '';
    req.on('data', (c) => { raw += c; });
    return req.on('end', () => {
      let changes = [];
      try { changes = JSON.parse(raw || '{}').changes || []; } catch (_) { changes = []; }
      const key = changes.slice().sort((a, b) => a.symbol.localeCompare(b.symbol)).map((c) => `${c.symbol}:${Math.round(c.delta_value * 100)}`).join(',');
      const hit = (insights.whatif || {})[key];
      setTimeout(() => res.end(JSON.stringify(hit || { available: false, reason: 'This combination was not recorded in the fixture.' })), 90);
    });
  }
  if (INSIGHT[url.pathname]) {
    const key = INSIGHT[url.pathname];
    if (failing.has(key) || failing.has(key.slice(0, 4))) { res.writeHead(502); return res.end(JSON.stringify({ error: 'Synthetic provider failure' })); }
    // STALE=1 answers every new route with 404, like a server started before the update.
    if (process.env.STALE === '1') { res.writeHead(404); return res.end(JSON.stringify({ error: 'Not found' })); }
    const body = key === 'activity' ? insights.activity[url.searchParams.get('period') || 'month'] : insights[key];
    return res.end(JSON.stringify(body));
  }
  const body = url.pathname === '/api/portfolio/status' ? status
    : url.pathname === '/api/portfolio/accounts' ? { accounts: [fixture.portfolio.account] }
      : url.pathname.startsWith('/api/portfolio') ? { ...fixture, status }
        : url.pathname === '/api/dashboard' ? tape
          : url.pathname === '/api/health' ? { status: 'ok' } : {};
  res.end(JSON.stringify(body));
}).listen(port, '127.0.0.1', () => console.log(`Synthetic portfolio preview: http://127.0.0.1:${port}/#holdings`));
