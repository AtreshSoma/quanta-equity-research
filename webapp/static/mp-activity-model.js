/* Activity model: the numbers behind the Activity tab, with no DOM.
 *
 * One record set drives every section. For closing trades that is Robinhood's own
 * per-trade realized P&L (or, only when Robinhood provides none, a labelled
 * first-in, first-out estimate on the order history). The KPI total, the chart's
 * buckets, the by-stock ranking and the table are all sums over the same filtered
 * rows, so they agree by construction; tests hold them to it.
 *
 * Robinhood's bucketed realized P&L for the chosen range is used twice, never as a
 * second source of figures: a bucket it reports with closing trades but no figure
 * (transfers without a cost basis) excludes the trades inside it everywhere, and its
 * window total is compared with the records, any difference becoming a named line.
 *
 * Every limit, label and window comes from the server's Activity config
 * (core/portfolio/activity_config.py) or from the loaded data.
 */
(function (root) {
  'use strict';

  const DAY_MS = 864e5;
  const EPS = 0.005;
  const ASSETS = { all: 'All', equity: 'Equities', crypto: 'Crypto' };
  // Legacy payloads lack classification; never guess an asset class from a ticker.
  const instrumentType = (r) => String(r.instrument_type || 'unknown').trim().toLowerCase();
  const equityTypes = new Set(['equity', 'stock', 'etf', 'etn', 'fund', 'reit', 'adr', 'preferred', 'warrant', 'right', 'unit']);
  const matchesAsset = (r, asset) => asset === 'all' || (asset === 'equity' ? equityTypes.has(instrumentType(r)) : instrumentType(r) === 'crypto');
  function symbolsFor(records, asset) {
    return [...new Set([...(records.closed_trades || []), ...(records.fills || []), ...(records.lots || []),
      ...((records.crypto || {}).fills || [])].filter((r) => r.symbol && matchesAsset(r, asset)).map((r) => r.symbol))].sort();
  }

  // ------------------------------------------------------------ time
  // Instants are compared as wall-clock strings in the market time zone (ISO date and
  // time without a zone), so a day, week or month is the market's, never the viewer's.
  const fmtCache = new Map();
  function wallFmt(tz) {
    if (!fmtCache.has(tz)) {
      fmtCache.set(tz, new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }));
    }
    return fmtCache.get(tz);
  }
  function wallOf(d, tz) {
    const p = {};
    wallFmt(tz).formatToParts(d).forEach((x) => { p[x.type] = x.value; });
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
  }
  const ZONED = /([zZ]|[+-]\d\d:?\d\d)$/;
  // A broker time: zoned strings are instants; a zoneless one is read as UTC (fills and
  // trades are sent in UTC).
  function instant(s) {
    if (!s) return null;
    const t = String(s);
    const d = new Date(ZONED.test(t) ? t : `${t}${t.length > 10 ? 'Z' : 'T12:00:00Z'}`);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  // A bucket bound: zoned strings convert; a zoneless one is already market wall time.
  function wallFrom(s, tz) {
    if (!s) return null;
    const t = String(s);
    if (ZONED.test(t)) { const d = instant(t); return d ? wallOf(d, tz) : null; }
    return t.length <= 10 ? `${t}T00:00:00` : t.slice(0, 19);
  }
  // Calendar arithmetic on ISO date strings, through noon UTC (no daylight-saving edges).
  const noon = (ymd) => new Date(`${ymd}T12:00:00Z`);
  const ymdOfUtc = (d) => d.toISOString().slice(0, 10);
  const addDays = (ymd, n) => ymdOfUtc(new Date(noon(ymd).getTime() + n * DAY_MS));
  function addMonths(ymd, n) {
    const [y, m, d] = ymd.split('-').map(Number);
    const first = new Date(Date.UTC(y, m - 1 + n, 1, 12));
    const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0, 12)).getUTCDate();
    first.setUTCDate(Math.min(d, last));
    return ymdOfUtc(first);
  }
  const daysBetween = (a, b) => Math.round((noon(b) - noon(a)) / DAY_MS);
  const weekStart = (ymd) => addDays(ymd, -((noon(ymd).getUTCDay() + 6) % 7));
  const monthStart = (ymd) => `${ymd.slice(0, 7)}-01`;

  // ------------------------------------------------------------ formatting
  const finite = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? null : Number(v));
  const dec = (cfg, k) => cfg.decimals[k];
  // Shares: up to the configured decimals, trailing zeros trimmed.
  const shareFmts = new Map();
  const shares = (v, cfg) => {
    const n = finite(v);
    if (n === null) return null;
    const d = dec(cfg, 'shares');
    if (!shareFmts.has(d)) shareFmts.set(d, new Intl.NumberFormat('en-US', { maximumFractionDigits: d }));
    return shareFmts.get(d).format(n);
  };
  // Percent from a fraction: 0.0404 -> "+4.04%".
  function pct(fraction, cfg, signed = true) {
    const n = finite(fraction);
    if (n === null) return null;
    const p = n * 100;
    const s = Math.abs(p).toFixed(dec(cfg, 'percent'));
    const zero = Number(s) === 0;
    return `${signed && !zero ? (p > 0 ? '+' : '-') : !signed && p < 0 && !zero ? '-' : ''}${s}%`;
  }
  const dateFmt = (opts) => new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', ...opts });
  const F_FULL = dateFmt({ month: 'short', day: 'numeric', year: 'numeric' });
  const F_DAY = dateFmt({ month: 'short', day: 'numeric' });
  const F_WDAY = dateFmt({ weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  const F_MON = dateFmt({ month: 'short' });
  const F_MONY = dateFmt({ month: 'long', year: 'numeric' });
  const dateLong = (ymd) => (ymd ? F_FULL.format(noon(ymd)) : null);
  const yearOf = (ymd) => ymd.slice(0, 4);
  const memoFmt = (cache, key, make) => { if (!cache.has(key)) cache.set(key, make()); return cache.get(key); };
  const zoneFmts = new Map();
  const clockFmts = new Map();
  const tzShort = (d, tz) => {
    const p = memoFmt(zoneFmts, tz, () => new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' })).formatToParts(d).find((x) => x.type === 'timeZoneName');
    return p ? p.value : tz;
  };
  const clock = (d, tz) => `${memoFmt(clockFmts, tz, () => new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit' })).format(d)} ${tzShort(d, tz)}`;
  // A period's full name, for tooltips and screen readers.
  function periodLong(p, gran) {
    if (gran === 'month') return F_MONY.format(noon(p.start));
    if (gran === 'week') return `Week of ${F_FULL.format(noon(p.start))}`;
    return F_WDAY.format(noon(p.start));
  }
  // Axis labels never pair a month with a two-digit number that could be a day or a year:
  // months read as the short month name, with the four-digit year under the first label
  // and wherever the year changes; days read as month and day, weeks as "Week of" a day.
  function axisLabels(periods, gran, maxLabels) {
    const step = Math.max(1, Math.ceil(periods.length / Math.max(1, maxLabels)));
    let lastYear = null;
    return periods.map((p, i) => {
      if (i % step !== 0) return null;
      const y = yearOf(p.start);
      const main = gran === 'month' ? F_MON.format(noon(p.start)) : F_DAY.format(noon(p.start));
      const year = y !== lastYear ? y : null;
      lastYear = y;
      return { main, year, pre: gran === 'week' ? 'Week of' : null };
    });
  }

  // ------------------------------------------------------------ windows and periods
  function rangeOf(cfg, key) { return cfg.ranges.find((r) => r.key === key) || null; }
  // Inclusive New York dates. "all" spans the loaded history (earliest record to today).
  function windowOf(cfg, range, today, earliest) {
    if (range && typeof range === 'object') return { from: range.from, to: range.to, custom: true };
    const r = rangeOf(cfg, range);
    if (!r || !r.back) return { from: earliest && earliest < today ? earliest : today, to: today, all: true };
    const { unit, n } = r.back;
    const from = unit === 'day' ? addDays(today, -n) : unit === 'month' ? addMonths(today, -n)
      : unit === 'year' ? addMonths(today, -12 * n) : `${today.slice(0, 4)}-01-01`;
    return { from, to: today };
  }
  const periodStart = (ymd, gran) => (gran === 'week' ? weekStart(ymd) : gran === 'month' ? monthStart(ymd) : ymd);
  const nextStart = (start, gran) => (gran === 'week' ? addDays(start, 7) : gran === 'month' ? addMonths(start, 1) : addDays(start, 1));
  function periodsOf(from, to, gran) {
    const out = [];
    for (let s = periodStart(from, gran); s <= to; s = nextStart(s, gran)) out.push({ key: s, start: s, end: addDays(nextStart(s, gran), -1) });
    return out;
  }
  const countPeriods = (from, to, gran) => {
    if (gran === 'day') return daysBetween(from, to) + 1;
    if (gran === 'week') return Math.floor(daysBetween(weekStart(from), weekStart(to)) / 7) + 1;
    return (Number(to.slice(0, 4)) - Number(from.slice(0, 4))) * 12 + Number(to.slice(5, 7)) - Number(from.slice(5, 7)) + 1;
  };
  // The finest granularity whose bucket count fits. When that averages fewer rows per
  // bucket than the configured sparse limit, the configured coarser one is used instead
  // (when it fits). An override is honoured when it fits.
  function granularity(cfg, win, override, rows = null) {
    const options = cfg.granularities.map((g) => {
      const count = countPeriods(win.from, win.to, g.key);
      return { key: g.key, label: g.label, noun: g.noun, count, fits: count <= cfg.max_buckets };
    });
    let auto = (options.find((o) => o.fits) || options[options.length - 1]).key;
    const sp = cfg.sparse;
    const first = options.find((o) => o.key === auto);
    const coarse = sp && options.find((o) => o.key === sp.use && o.fits);
    const sparse = !!(sp && rows !== null && rows / first.count < sp.rows_per_bucket);
    if (sparse && coarse) auto = coarse.key;
    const pick = options.find((o) => o.key === override && o.fits);
    return { key: pick ? pick.key : auto, auto, sparse, options };
  }

  // ------------------------------------------------------------ rows
  // Closing trades, Robinhood's: the closed cost is what the realized gain implies
  // (a sale: proceeds less gain; a buy to close: cost plus gain), so the return is
  // realized P&L divided by the cost basis of the closed lots.
  function robinhoodSales(records, tz) {
    return (records.closed_trades || []).map((x, i) => {
      const t = instant(x.at || x.closed_at);
      if (!t) return null;
      const qty = finite(x.quantity);
      const px = finite(x.price);
      const realized = finite(x.realized_gain);
      const side = x.side === 'buy' ? 'buy' : 'sell';
      const flow = qty !== null && px !== null ? qty * px : null;
      const cost = flow === null || realized === null ? null : side === 'buy' ? flow + realized : flow - realized;
      const opened = x.opened_at ? String(x.opened_at).slice(0, 10) : null;
      const wall = wallOf(t, tz);
      return {
        id: `t${i}`, symbol: x.symbol || null, instrument_type: instrumentType(x), side, t, wall, ymd: wall.slice(0, 10), qty, price: px, realized,
        entry: cost !== null && qty ? cost / qty : null, cost, ret: cost ? realized / cost : null,
        opened, held: opened ? daysBetween(opened, wall.slice(0, 10)) : null,
      };
    }).filter((x) => x && x.symbol);
  }
  // Fallback only: first-in, first-out against the buys in the order history. It cannot
  // see stock splits, so the page labels it as an estimate.
  const fifoMemo = new WeakMap();
  function fifo(fills, tz) {
    if (fifoMemo.has(fills)) return fifoMemo.get(fills);
    const asc = fills.map((f, i) => ({ f, i, t: instant(f.at) })).filter((x) => x.t).sort((a, b) => a.t - b.t);
    const queue = {};
    const sales = [];
    asc.forEach(({ f, i, t }) => {
      const qty = finite(f.quantity);
      const px = finite(f.price);
      if (qty === null || px === null || !f.symbol) return;
      const key = `${instrumentType(f)}:${f.symbol}`;
      const q = (queue[key] = queue[key] || []);
      const wall = wallOf(t, tz);
      if (f.side === 'buy') { q.push({ qty, px, ymd: wall.slice(0, 10) }); return; }
      let left = qty;
      let cost = 0;
      let opened = null;
      const tolerance = instrumentType(f) === 'crypto' ? Math.max(1e-12, qty * 1e-10) : Math.max(1e-6, qty * 1e-6);
      while (left > (instrumentType(f) === 'crypto' ? 1e-12 : 1e-9) && q.length) {
        const lot = q[0];
        const take = Math.min(left, lot.qty);
        if (!opened) opened = lot.ymd;
        cost += take * lot.px;
        lot.qty -= take;
        left -= take;
        if (lot.qty <= (instrumentType(f) === 'crypto' ? 1e-12 : 1e-9)) q.shift();
      }
      const complete = left <= tolerance;
      const realized = complete ? qty * px - cost : null;
      sales.push({ id: `f${i}`, symbol: f.symbol, instrument_type: instrumentType(f), side: 'sell', t, wall, ymd: wall.slice(0, 10), qty, price: px, realized,
        entry: complete ? cost / qty : null, cost: complete ? cost : null, ret: complete && cost ? realized / cost : null,
        opened: complete ? opened : null, held: complete && opened ? daysBetween(opened, wall.slice(0, 10)) : null, unmatched: complete ? 0 : left });
    });
    fifoMemo.set(fills, sales);
    return sales;
  }
  // Robinhood's trade records carry only some crypto sales; the crypto endpoint has
  // every execution but no realized gains. Each crypto sale is estimated first-in,
  // first-out over the whole crypto history, and kept only when no broker record is
  // the same sale (same symbol and side, within the evidence window): the broker's
  // own figure always wins, and nothing is counted twice.
  function cryptoEstimates(records, rows, tz, windowS) {
    const crypto = records.crypto;
    if (!crypto || !crypto.available) return [];
    const ms = (Number.isFinite(windowS) ? windowS : 0) * 1000;
    const broker = new Map();
    rows.forEach((r) => {
      const k = `${r.symbol}|${r.side}`;
      if (!broker.has(k)) broker.set(k, []);
      broker.get(k).push(r.t.getTime());
    });
    const reported = (e) => (broker.get(`${e.symbol}|sell`) || []).some((t) => Math.abs(t - e.t.getTime()) <= ms);
    return fifo(crypto.fills || [], tz).filter((e) => !reported(e)).map((e) => ({ ...e, id: `c${e.id}`, estimated: true }));
  }
  function salesSource(records, tz, windowS) {
    if (records.closed_trades != null) {
      const rows = robinhoodSales(records, tz);
      return { source: 'robinhood', rows: [...rows, ...cryptoEstimates(records, rows, tz, windowS)] };
    }
    if (records.crypto && records.crypto.available) return { source: 'fifo', rows: fifo([...(records.fills || []), ...records.crypto.fills], tz) };
    if (records.fills != null) return { source: 'fifo', rows: fifo(records.fills, tz) };
    return { source: null, rows: null };
  }
  function buyRows(records, tz) {
    if (records.fills == null && !(records.crypto && records.crypto.available)) return null;
    return [...(records.fills || []), ...((records.crypto || {}).fills || [])].map((f, i) => {
      const t = instant(f.at);
      const qty = finite(f.quantity);
      const px = finite(f.price);
      if (!t || f.side !== 'buy' || qty === null || px === null || !f.symbol) return null;
      const wall = wallOf(t, tz);
      return { id: `b${i}`, symbol: f.symbol, instrument_type: instrumentType(f), t, wall, ymd: wall.slice(0, 10), qty, price: px, amount: qty * px };
    }).filter(Boolean);
  }
  function ageOf(cfg, days) {
    if (days === null) return null;
    return (cfg.lot_ages.find((a) => a.max_days === null || days < a.max_days) || cfg.lot_ages[cfg.lot_ages.length - 1]).key;
  }
  function lotRows(records, cfg, today) {
    return (records.lots || []).map((l, i) => {
      const qty = finite(l.quantity);
      const cps = finite(l.cost_per_share);
      const cost = finite(l.cost) !== null ? finite(l.cost) : qty !== null && cps !== null ? qty * cps : null;
      const gain = finite(l.gain);
      const value = finite(l.value) !== null ? finite(l.value) : cost !== null && gain !== null ? cost + gain : null;
      const ymd = l.acquired_on ? String(l.acquired_on).slice(0, 10) : null;
      const age = ymd ? Math.max(0, daysBetween(ymd, today)) : null;
      return { id: `l${i}`, symbol: l.symbol, instrument_type: instrumentType(l), ymd, age, ageKey: ageOf(cfg, age), qty, cps, cost, value, gain,
        ret: gain !== null && cost ? gain / cost : null, term: l.term || null };
    }).filter((x) => x.symbol);
  }

  // ------------------------------------------------------------ Robinhood's buckets
  // Buckets with closing trades but no figure exclude the trades inside them.
  function brokerSeries(series, tz) {
    if (!series || !series.available) return null;
    const bs = (series.buckets || []).map((b) => ({ start: wallFrom(b.start, tz), end: wallFrom(b.end, tz), gain: finite(b.gain), trades: Number.isInteger(b.trades) ? b.trades : null }))
      .filter((b) => b.start);
    bs.forEach((b, i) => { if (!b.end) b.end = bs[i + 1] ? bs[i + 1].start : null; });
    return {
      total: finite(series.total), buckets: bs,
      noFigure: bs.filter((b) => b.gain === null && (b.trades || 0) > 0),
      from: bs.length ? bs[0].start : null, to: bs.length ? bs[bs.length - 1].end : null,
    };
  }
  const inBucket = (wall, b) => wall >= b.start && (!b.end || wall < b.end);

  // ------------------------------------------------------------ the model
  const VALUE = { sales: (r) => r.realized, buys: (r) => r.amount, lots: (r) => r.gain };

  /**
   * ctx: { cfg, view, records, today (ISO date), series (Robinhood's realized for the
   *   range's span, or null), filters: { range: key | {from,to}, asset, symbols: [..], period, age, gran } }
   */
  function build(ctx) {
    const { cfg, view, records, today } = ctx;
    const f = Object.assign({ asset: 'all', symbols: [], period: null, age: null, gran: null }, ctx.filters);
    const tz = cfg.timezone;
    const out = { view, cfg, filters: f, excluded: [], notes: [] };
    if (f.asset === 'crypto' && records.crypto) {
      if (view === 'lots') return { ...out, unavailable: true, reason: 'Robinhood does not provide crypto tax lots through this connection.' };
      if (!records.crypto.available) return { ...out, unavailable: true, reason: records.crypto.reason };
    }
    let all;
    if (view === 'sales') {
      const s = salesSource(records, tz, cfg.evidence_window_s);
      out.source = s.source;
      all = s.rows;
    } else if (view === 'buys') {
      out.source = records.fills == null ? null : 'orders';
      all = buyRows(records, tz);
    } else {
      out.source = 'broker';
      all = lotRows(records, cfg, today);
    }
    if (all === null) { out.unavailable = true; return out; }
    out.unclassified = all.filter((r) => !matchesAsset(r, 'equity') && !matchesAsset(r, 'crypto')).length;
    out.symbolsAll = [...new Set(all.map((r) => r.symbol))].sort();
    const dated = all.filter((r) => r.ymd);
    const earliest = dated.length ? dated.reduce((m, r) => (r.ymd < m ? r.ymd : m), dated[0].ymd) : null;
    const latest = dated.length ? dated.reduce((m, r) => (r.ymd > m ? r.ymd : m), dated[0].ymd) : null;
    out.loaded = { from: earliest, to: latest, count: all.length };
    const win = windowOf(cfg, f.range, today, earliest);
    out.window = win;
    const sym = new Set(f.symbols || []);
    const bySym = (r) => !sym.size || sym.has(r.symbol);
    const byAsset = (r) => matchesAsset(r, f.asset);
    // Lots carry no event in a range unless they have a date; undated lots appear under All.
    const inWin = (r) => (r.ymd ? r.ymd >= win.from && r.ymd <= win.to : view === 'lots' && win.all);
    let rows = all.filter((r) => inWin(r) && bySym(r) && byAsset(r));
    out.undated = view === 'lots' ? all.filter((r) => !r.ymd && bySym(r) && byAsset(r)).length : 0;

    // Exclusions: only a trade with no realized figure of its own. A Robinhood
    // bucket reported without a total only flags its period: dropping every trade
    // inside it made the same trades sum differently per range, since coarser
    // ranges use coarser buckets (audit A1). The cross-check below still leaves
    // those periods out on both sides.
    // Account-wide broker buckets cannot annotate or reconcile an asset subset.
    // Crypto estimates are not in Robinhood's total, so the comparison uses its own records only.
    const B = view === 'sales' && out.source === 'robinhood' && f.asset === 'all' ? brokerSeries(ctx.series, tz) : null;
    out.broker = B;
    const noFigureBucket = (r) => (B ? B.noFigure.find((b) => inBucket(r.wall, b)) || null : null);
    if (view === 'sales') {
      rows = rows.filter((r) => {
        if (r.realized === null) { out.excluded.push({ row: r, reason: noFigureBucket(r) ? 'bucket' : 'trade', bucket: noFigureBucket(r) }); return false; }
        return true;
      });
      out.inNoFigure = rows.filter((r) => noFigureBucket(r)).length;
    } else if (view === 'lots') {
      rows = rows.filter((r) => {
        if (r.gain === null) { out.excluded.push({ row: r, reason: 'gain' }); return false; }
        return true;
      });
    }
    const val = VALUE[view];

    // Periods: time buckets (sales, purchases) or age buckets (open lots).
    let periods;
    if (view === 'lots') {
      out.gran = null;
      const ages = rows.some((r) => r.ageKey === null) ? [...cfg.lot_ages, cfg.lot_age_unknown] : cfg.lot_ages;
      periods = ages.map((a) => ({ key: a.key, label: a.label, long: a.label }));
      rows.forEach((r) => { r.pk = r.ageKey === null ? cfg.lot_age_unknown.key : r.ageKey; });
    } else {
      const g = granularity(cfg, win, f.gran, rows.length);
      out.gran = g;
      periods = periodsOf(win.from, win.to, g.key).map((p) => ({ ...p, long: periodLong(p, g.key) }));
      rows.forEach((r) => { r.pk = periodStart(r.ymd, g.key); });
      const labels = axisLabels(periods, g.key, cfg.max_axis_labels);
      periods.forEach((p, i) => { p.axis = labels[i]; });
      if (B) {
        periods.forEach((p) => {
          const a = `${p.start}T00:00:00`;
          const z = `${addDays(p.end, 1)}T00:00:00`;
          p.noFigure = B.noFigure.some((b) => b.start < z && (!b.end || b.end > a));
        });
      }
      out.excluded.forEach((e) => { e.pk = periodStart(e.row.ymd, g.key); });
    }
    const byKey = new Map(periods.map((p) => [p.key, p]));
    periods.forEach((p) => { p.value = 0; p.n = 0; p.wins = 0; p.losses = 0; p.best = null; p.worst = null; });
    rows.forEach((r) => {
      const p = byKey.get(r.pk);
      if (!p) return;
      const v = val(r);
      p.value += v; p.n += 1;
      if (view !== 'buys') {
        if (v > 0) p.wins += 1; else if (v < 0) p.losses += 1;
        if (!p.best || v > val(p.best)) p.best = r;
        if (!p.worst || v < val(p.worst)) p.worst = r;
      } else if (!p.best || v > val(p.best)) p.best = r;
    });
    const sel = view === 'lots' ? f.age : f.period;
    const selValid = sel && byKey.has(sel) ? sel : null;
    out.selected = selValid;
    periods.forEach((p) => { p.active = !selValid || p.key === selValid; });
    let cum = 0;
    periods.forEach((p) => { cum += p.value; p.cum = cum; });
    out.periods = periods;

    // The rows every other section shows: the window, the stocks, and a chosen bar.
    const shown = selValid ? rows.filter((r) => r.pk === selValid) : rows;
    out.windowRows = rows;
    out.rows = shown;
    out.total = shown.reduce((t, r) => t + val(r), 0);
    out.periodTotal = periods.filter((p) => p.active).reduce((t, p) => t + p.value, 0);

    // Ranking by stock over the shown rows.
    const g = new Map();
    shown.forEach((r) => {
      const e = g.get(r.symbol) || { symbol: r.symbol, value: 0, n: 0, cost: 0 };
      e.value += val(r); e.n += 1; e.cost += r.cost || 0;
      g.set(r.symbol, e);
    });
    out.ranking = [...g.values()].sort((a, b) => b.value - a.value || a.symbol.localeCompare(b.symbol));
    out.rankingTotal = out.ranking.reduce((t, e) => t + e.value, 0);
    out.kpis = kpis(out, shown, val);

    // Robinhood's own total for the same window, compared with the records.
    // Every usable record inside Robinhood's own window bounds, not the page's window.
    if (B && !sym.size && !selValid && !win.custom && B.total !== null && B.from) {
      const inB = (r) => r.wall >= B.from && (!B.to || r.wall < B.to);
      const usable = all.filter((r) => !r.estimated && inB(r) && r.realized !== null && !noFigureBucket(r));
      const recs = usable.reduce((t, r) => t + r.realized, 0);
      const brokerKnown = B.buckets.filter((b) => b.gain !== null).reduce((t, b) => t + b.gain, 0);
      const diff = brokerKnown - recs;
      out.check = { broker: brokerKnown, records: recs, diff, matches: Math.abs(diff) < EPS,
        brokerTrades: B.buckets.filter((b) => b.gain !== null).reduce((t, b) => t + (b.trades || 0), 0),
        recordTrades: usable.length };
    }
    return out;
  }

  function kpis(out, rows, val) {
    const k = { count: rows.length, stocks: new Set(rows.map((r) => r.symbol)).size };
    if (out.view === 'sales') {
      const cost = rows.reduce((t, r) => t + (r.cost || 0), 0);
      const costed = rows.every((r) => r.cost !== null);
      k.realized = out.total;
      k.closedCost = costed && cost > 0 ? cost : null;
      k.ret = k.closedCost ? out.total / k.closedCost : null;
      const wins = rows.filter((r) => r.realized > 0);
      const losses = rows.filter((r) => r.realized < 0);
      k.wins = wins.length; k.losses = losses.length;
      k.winRate = wins.length + losses.length ? wins.length / (wins.length + losses.length) : null;
      k.avgWin = wins.length ? wins.reduce((t, r) => t + r.realized, 0) / wins.length : null;
      k.avgLoss = losses.length ? losses.reduce((t, r) => t + r.realized, 0) / losses.length : null;
      k.best = rows.length ? rows.reduce((m, r) => (r.realized > m.realized ? r : m), rows[0]) : null;
      k.worst = rows.length ? rows.reduce((m, r) => (r.realized < m.realized ? r : m), rows[0]) : null;
      const withClosings = out.periods.filter((p) => p.active && p.n > 0);
      k.periodsWith = withClosings.length;
      k.periodsPositive = withClosings.filter((p) => p.value > 0).length;
      gross(k, wins.map((r) => r.realized), losses.map((r) => r.realized));
    } else if (out.view === 'buys') {
      k.deployed = out.total;
      k.avg = rows.length ? out.total / rows.length : null;
      k.largest = rows.length ? rows.reduce((m, r) => (r.amount > m.amount ? r : m), rows[0]) : null;
      const sizes = rows.map((r) => r.amount).sort((a, b) => a - b);
      const mid = Math.floor(sizes.length / 2);
      k.smallest = sizes.length ? sizes[0] : null;
      k.median = sizes.length ? (sizes.length % 2 ? sizes[mid] : (sizes[mid - 1] + sizes[mid]) / 2) : null;
    } else {
      const cost = rows.reduce((t, r) => t + (r.cost || 0), 0);
      const value = rows.reduce((t, r) => t + (r.value || 0), 0);
      k.unrealized = out.total;
      k.cost = rows.length && rows.every((r) => r.cost !== null) ? cost : null;
      k.value = rows.length && rows.every((r) => r.value !== null) ? value : null;
      k.ret = k.cost ? out.total / k.cost : null;
      k.ages = out.periods.map((p) => ({ key: p.key, label: p.label, n: p.n }));
      gross(k, rows.filter((r) => r.gain > 0).map((r) => r.gain), rows.filter((r) => r.gain < 0).map((r) => r.gain));
    }
    return k;
  }
  // Gross gain and gross loss over the shown rows; together they make the total.
  // Profit factor = gross gain / gross loss, only when there is a loss.
  function gross(k, gains, losses) {
    k.grossGain = gains.reduce((t, v) => t + v, 0);
    k.grossLoss = losses.reduce((t, v) => t + v, 0);
    k.profitFactor = losses.length && k.grossLoss < 0 ? k.grossGain / -k.grossLoss : null;
  }

  // ------------------------------------------------------------ chart scale
  function niceStep(span, ticks) {
    const raw = span / Math.max(1, ticks);
    if (!(raw > 0)) return 1;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const m = raw / mag;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * mag;
  }
  // One scale for bars and the running total; zero always inside it.
  function scale(values, ticks) {
    const xs = values.filter((v) => Number.isFinite(v));
    let lo = Math.min(0, ...xs);
    let hi = Math.max(0, ...xs);
    if (lo === hi) hi = lo + 1;
    const step = niceStep(hi - lo, ticks);
    lo = Math.floor(lo / step + 1e-9) * step;
    hi = Math.ceil(hi / step - 1e-9) * step + 0;
    lo += 0;
    if (lo === hi) hi = lo + step;
    const out = [];
    for (let v = lo; v <= hi + step / 2; v += step) out.push(Math.abs(v) < step / 1e6 ? 0 : v);
    const at = (v) => ((hi - v) / (hi - lo)) * 100;
    return { lo, hi, step, ticks: out, at };
  }

  // ------------------------------------------------------------ URL state
  // "#holdings/activity/r=<range>&v=<view>&s=<symbols>"; a brushed range is "r=<from>~<to>".
  const YMD = /^\d{4}-\d{2}-\d{2}$/;
  function parseHash(hash, cfg) {
    const parts = String(hash || '').replace(/^#/, '').split('/');
    const q = new URLSearchParams(parts.slice(2).join('/'));
    const out = { range: cfg.default_range, view: cfg.default_view, asset: 'all', symbols: [] };
    if (Object.hasOwn(ASSETS, q.get('a'))) out.asset = q.get('a');
    const r = q.get('r');
    if (r && rangeOf(cfg, r)) out.range = r;
    else if (r && /~/.test(r)) {
      const [a, b] = r.split('~');
      if (YMD.test(a) && YMD.test(b) && a <= b) out.range = { from: a, to: b };
    }
    const v = q.get('v');
    if (v && cfg.views.some((x) => x.key === v)) out.view = v;
    const s = q.get('s');
    if (s) out.symbols = [...new Set(s.split(',').map((x) => x.trim().toUpperCase()).filter((x) => /^[A-Z0-9.\-]{1,12}$/.test(x)))];
    return out;
  }
  function hashOf(st) {
    const q = [];
    q.push(`r=${typeof st.range === 'object' ? `${st.range.from}~${st.range.to}` : st.range}`);
    q.push(`v=${st.view}`);
    if (st.symbols && st.symbols.length) q.push(`s=${st.symbols.join(',')}`);
    if (st.asset && st.asset !== 'all' && Object.hasOwn(ASSETS, st.asset)) q.push(`a=${st.asset}`);
    return q.join('&');
  }

  const api = {
    ASSETS, symbolsFor,
    build, scale, granularity, windowOf, periodsOf, countPeriods, periodLong, axisLabels, parseHash, hashOf,
    shares, pct, dateLong, clock, tzShort, wallOf, instant, wallFrom, addDays, addMonths, daysBetween, weekStart,
    salesSource, buyRows, lotRows, brokerSeries, rangeOf, EPS,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.QuantaActivityModel = api;
}(typeof window !== 'undefined' ? window : globalThis));
