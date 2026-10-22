/* Live price chart: one symbol, one range at a time, from GET /api/chart.
 *
 * Same construction as mp-charts.js: shapes are SVG stretched over a fixed
 * viewBox (strokes keep their width with vector-effect) and every piece of text
 * is HTML positioned by percentage, so type never scales with the chart.
 *
 * Axes are exact: the 1D time axis spans the exchange's whole trading day
 * (pre-market start to post-market end, from the provider's session times) and
 * is labelled in the exchange's time zone; longer ranges place bars by trading
 * session and label the first bar of each day, month or year. Price ticks are
 * round numbers that cover every plotted value and reference line.
 *
 * Display: a line (green at or above the reference, red below) or candles, with
 * optional studies computed here from the bars: simple averages (20, 50, 200),
 * a 21-bar exponential average, Bollinger bands (20, 2), session VWAP on
 * intraday ranges, volume, and RSI (14) and MACD (12, 26, 9) in their own panes.
 * The server sends warm-up bars before the window (``lead``) so a 200-bar
 * average is right from the first plotted bar. "Compare" redraws the symbol and
 * its benchmark (SPY, or the Nifty 50 for Indian listings) as percent change
 * from the first visible bar. Ctrl + scroll (or pinch), the + and - buttons or
 * keys zoom; dragging pans a zoomed chart; 0 or Reset shows the whole range.
 *
 * Colour roles: gold marks a price target, red a stop, gray the previous close;
 * studies use the chart series colours, never the bull, bear or gold roles.
 * Live: the 1D chart refreshes every 20 seconds while its session (or extended
 * hours) is open, 5D every minute; longer ranges update their last bar from the
 * live quote every minute while the market is open. Nothing refreshes while the
 * page is hidden or the chart is off screen.
 */
(function (root) {
  'use strict';

  const W = 1000;
  const H = 300;            // price pane height in viewBox units
  const VOL_SHARE = 0.16;
  const RANGES = [['1d', '1D'], ['5d', '5D'], ['1mo', '1M'], ['6mo', '6M'], ['ytd', 'YTD'], ['1y', '1Y'], ['5y', '5Y'], ['max', 'Max']];
  const RANGE_WORDS = { '1d': 'today', '5d': 'past 5 days', '1mo': 'past month', '6mo': 'past 6 months', ytd: 'year to date', '1y': 'past year', '5y': 'past 5 years', max: 'all time' };
  const REFRESH_MS = { '1d': 20000, '5d': 60000 };
  const LIVE_PATCH_MS = 60000;
  const PANE_PX = 96;       // CSS height of an RSI or MACD pane
  const MIN_VIEW = 12;      // fewest bars a zoom can show
  const INTERVAL_MS = { '1m': 60000, '5m': 300000, '30m': 1800000 };

  // Studies a reader can switch on. ``pane`` studies get their own pane.
  const STUDIES = [
    { key: 'vol', label: 'Volume' },
    { key: 'sma20', label: 'SMA 20', tone: 1, overlay: true },
    { key: 'sma50', label: 'SMA 50', tone: 2, overlay: true },
    { key: 'sma200', label: 'SMA 200', tone: 3, overlay: true },
    { key: 'ema21', label: 'EMA 21', tone: 4, overlay: true },
    { key: 'bb', label: 'Bollinger 20, 2', tone: 1, overlay: true },
    { key: 'vwap', label: 'VWAP', tone: 4, overlay: true, intraday: true },
    { key: 'rsi', label: 'RSI 14', pane: true },
    { key: 'macd', label: 'MACD 12, 26, 9', pane: true },
  ];
  const DEFAULTS = {
    research: { type: 'candles', studies: ['vol', 'sma50', 'sma200'] },
    overview: { type: 'line', studies: ['vol'] },
    default: { type: 'line', studies: ['vol'] },
  };

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const CURRENCY = { USD: '$', INR: '₹', EUR: '€', GBP: '£', JPY: '¥' };
  const symOf = (cur) => CURRENCY[String(cur || '').toUpperCase()] || '';
  // An index (^VIX, ^TNX, ^GSPC) is a level, not a price: no currency symbol.
  const isLevel = (data) => String(data.symbol || '').startsWith('^') || /^INDEX$/i.test(String(data.instrument_type || ''));
  // Volatility indices are read inversely: a falling VIX supports risk, so it
  // takes the supportive colour and a rising one the warning colour.
  const isInverse = (data) => /^\^(VIX|VXN|VVIX|RVX|INDIAVIX)/i.test(String(data.symbol || ''));
  // What a symbol is compared against: the Nifty 50 for Indian listings, else SPY.
  const benchmarkFor = (sym) => {
    const s = String(sym || '').toUpperCase();
    if (!s || s.startsWith('^') || s.includes('=') || s === 'SPY' || s === '^NSEI') return null;
    return /\.(NS|BO)$/.test(s) ? '^NSEI' : 'SPY';
  };
  const store = {
    get(k) { try { return root.localStorage ? root.localStorage.getItem(k) : null; } catch (_) { return null; } },
    set(k, v) { try { if (root.localStorage) root.localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
  };

  function niceStep(span, target) {
    const raw = span / Math.max(1, target);
    const pow = 10 ** Math.floor(Math.log10(raw));
    const f = raw / pow;
    return (f < 1.5 ? 1 : f < 2.25 ? 2 : f < 3.5 ? 2.5 : f < 7 ? 5 : 10) * pow;
  }
  function decimalsFor(step) { return step >= 1 ? (step % 1 ? 1 : 0) : Math.min(4, Math.ceil(-Math.log10(step)) + (String(step).includes('25') ? 1 : 0)); }
  function fmtNum(v, d = 2) { return finite(v) ? v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '--'; }
  function priceDigits(v) { return Math.abs(v) >= 1 ? 2 : 4; }
  function fmtSigned(v, d = 2) { return finite(v) ? `${v >= 0 ? '+' : '-'}${fmtNum(Math.abs(v), d)}` : '--'; }
  function fmtVol(v) {
    if (!finite(v)) return '--';
    const a = Math.abs(v);
    return a >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : a >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(Math.round(v));
  }

  // ------------------------------------------------------------- indicators
  // Pure functions over arrays; a value is null until its window is full.

  function sma(values, n) {
    const out = new Array(values.length).fill(null);
    let sum = 0;
    let count = 0;
    for (let i = 0; i < values.length; i += 1) {
      const v = values[i];
      if (!finite(v)) { sum = 0; count = 0; continue; }
      sum += v; count += 1;
      if (count > n) { sum -= values[i - n]; count = n; }
      if (count === n) out[i] = sum / n;
    }
    return out;
  }

  function ema(values, n) {
    const out = new Array(values.length).fill(null);
    const k = 2 / (n + 1);
    let prev = null;
    let seed = [];
    for (let i = 0; i < values.length; i += 1) {
      const v = values[i];
      if (!finite(v)) continue;
      if (prev === null) {
        seed.push(v);
        if (seed.length === n) { prev = seed.reduce((a, b) => a + b, 0) / n; out[i] = prev; seed = []; }
        continue;
      }
      prev = v * k + prev * (1 - k);
      out[i] = prev;
    }
    return out;
  }

  function bollinger(values, n = 20, mult = 2) {
    const mid = sma(values, n);
    const upper = new Array(values.length).fill(null);
    const lower = new Array(values.length).fill(null);
    for (let i = n - 1; i < values.length; i += 1) {
      if (mid[i] === null) continue;
      let ss = 0;
      for (let j = i - n + 1; j <= i; j += 1) ss += (values[j] - mid[i]) ** 2;
      const sd = Math.sqrt(ss / n);
      upper[i] = mid[i] + mult * sd;
      lower[i] = mid[i] - mult * sd;
    }
    return { mid, upper, lower };
  }

  // Wilder's RSI.
  function rsi(values, n = 14) {
    const out = new Array(values.length).fill(null);
    let gain = 0;
    let loss = 0;
    for (let i = 1; i < values.length; i += 1) {
      const ch = values[i] - values[i - 1];
      const g = ch > 0 ? ch : 0;
      const l = ch < 0 ? -ch : 0;
      if (i <= n) {
        gain += g; loss += l;
        if (i === n) { gain /= n; loss /= n; out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss); }
        continue;
      }
      gain = (gain * (n - 1) + g) / n;
      loss = (loss * (n - 1) + l) / n;
      out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
    }
    return out;
  }

  function macd(values, fast = 12, slow = 26, signal = 9) {
    const f = ema(values, fast);
    const s = ema(values, slow);
    const line = values.map((_, i) => (f[i] !== null && s[i] !== null ? f[i] - s[i] : null));
    const firstIdx = line.findIndex((v) => v !== null);
    const sig = new Array(values.length).fill(null);
    if (firstIdx >= 0) {
      const tail = ema(line.slice(firstIdx), signal);
      tail.forEach((v, j) => { sig[firstIdx + j] = v; });
    }
    const hist = line.map((v, i) => (v !== null && sig[i] !== null ? v - sig[i] : null));
    return { line, signal: sig, hist };
  }

  // Bars whose volume is more than ``mult`` times the median of the bars with
  // volume. Intraday feeds sometimes stamp a block or consolidated print of
  // millions of shares on one minute; left in, it flattens the volume scale and
  // drags VWAP to that bar's price.
  function volumeOutliers(bars, mult = 20) {
    const vols = bars.map((b) => b[2]).filter((v) => finite(v) && v > 0).sort((a, b) => a - b);
    const out = new Set();
    if (vols.length < 20) return out;
    const median = vols[Math.floor(vols.length / 2)];
    bars.forEach((b, i) => { if (finite(b[2]) && b[2] > median * mult) out.add(i); });
    return out;
  }

  // Volume-weighted average price, restarting each exchange session day.
  // ``skip`` holds bar indices left out of the weighting (unusual prints).
  function vwap(bars, dayKey, skip) {
    const out = new Array(bars.length).fill(null);
    let pv = 0;
    let vol = 0;
    let day = null;
    bars.forEach((b, i) => {
      const k = dayKey(b[0]);
      if (k !== day) { pv = 0; vol = 0; day = k; }
      const v = finite(b[2]) && !(skip && skip.has(i)) ? b[2] : 0;
      const typical = ((finite(b[4]) ? b[4] : b[1]) + (finite(b[5]) ? b[5] : b[1]) + b[1]) / 3;
      if (v > 0) { pv += typical * v; vol += v; }
      out[i] = vol > 0 ? pv / vol : null;
    });
    return out;
  }

  // Parts of a timestamp in the exchange's time zone.
  function zoned(tz) {
    const opts = (o) => { try { return new Intl.DateTimeFormat('en-US', { timeZone: tz || undefined, ...o }); } catch (_) { return new Intl.DateTimeFormat('en-US', o); } };
    const parts = opts({ year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', hourCycle: 'h23', minute: 'numeric', weekday: 'short' });
    const fTime = opts({ hour: 'numeric', minute: '2-digit' });
    const fSecs = opts({ hour: 'numeric', minute: '2-digit', second: '2-digit' });
    const fHour = opts({ hour: 'numeric' });
    const fDay = opts({ month: 'short', day: 'numeric' });
    const fWeekday = opts({ weekday: 'short', day: 'numeric' });
    const fMonth = opts({ month: 'short' });
    const fMonthYear = opts({ month: 'short', year: 'numeric' });
    const fFull = opts({ month: 'short', day: 'numeric', year: 'numeric' });
    const fZone = opts({ timeZoneName: 'short', hour: 'numeric' });
    const get = (t) => {
      const o = {};
      parts.formatToParts(new Date(t)).forEach((p) => { o[p.type] = p.value; });
      return { y: +o.year, m: +o.month, d: +o.day, h: +o.hour, min: +o.minute };
    };
    const zoneName = (t) => {
      const p = fZone.formatToParts(new Date(t)).find((x) => x.type === 'timeZoneName');
      return p ? p.value : '';
    };
    return {
      get,
      zone: zoneName,
      dayKey: (t) => { const p = get(t); return `${p.y}-${p.m}-${p.d}`; },
      time: (t) => `${fTime.format(new Date(t))} ${zoneName(t)}`.trim(),
      seconds: (t) => fSecs.format(new Date(t)),
      hour: (t) => fHour.format(new Date(t)),
      day: (t) => fDay.format(new Date(t)),
      weekday: (t) => fWeekday.format(new Date(t)),
      month: (t) => fMonth.format(new Date(t)),
      monthYear: (t) => fMonthYear.format(new Date(t)),
      full: (t) => fFull.format(new Date(t)),
      year: (t) => String(get(t).y),
    };
  }

  // X positions and the tick labels for one payload.
  function xModel(data, z) {
    const pts = data.points;
    const n = pts.length;
    const sess = data.session || {};
    if (data.range === '1d') {
      const s0 = (sess.pre && sess.pre[0]) || (sess.regular && sess.regular[0]) || pts[0][0];
      let trimmed = false;
      let s1 = (sess.post && sess.post[1]) || (sess.regular && sess.regular[1]) || pts[n - 1][0];
      // After the close, a post-market that never moved is a flat line across
      // a quarter of the chart: the axis ends at the last move instead.
      const closeT = sess.regular && sess.regular[1];
      if (closeT && sess.post && pts[n - 1][0] > closeT) {
        let lastMove = 0;
        for (let i = 1; i < n; i += 1) if (pts[i][1] !== pts[i - 1][1]) lastMove = i;
        const moveT = pts[lastMove][0];
        if (moveT <= closeT + 1800000) { s1 = Math.max(closeT, moveT) + 1800000; trimmed = true; }
      }
      const lo = Math.min(s0, pts[0][0]);
      const hi = trimmed ? Math.max(s1, lo + 1) : Math.max(s1, pts[n - 1][0], lo + 1);
      const x = (t) => ((t - lo) / (hi - lo)) * W;
      const ticks = [];
      const hourMs = 3600000;
      const spanH = (hi - lo) / hourMs;
      const every = spanH > 12 ? 2 : spanH > 1.5 ? 1 : 0;
      // Step by quarter hours so a zone offset by 30 or 45 minutes still lands on
      // its own whole hours. A zoomed-in hour or less is labelled every 15 minutes.
      const q = hourMs / 4;
      for (let t = Math.ceil(lo / q) * q; t <= hi; t += q) {
        const p = z.get(t);
        if (every === 0 ? true : (p.min === 0 && p.h % every === 0)) ticks.push({ x: x(t), label: every === 0 ? z.time(t).replace(/\s\S+$/, '') : z.hour(t) });
      }
      return { x: (i) => x(Math.min(pts[i][0], hi)), xt: x, ticks, ext: [sess.pre, sess.post].filter(Boolean).map(([a, b]) => [x(Math.max(a, lo)), x(Math.min(b, hi))]).filter(([a, b]) => b > a) };
    }
    const x = (i) => (n === 1 ? W / 2 : (i / (n - 1)) * W);
    const key = {
      '5d': (p) => `${p.y}-${p.m}-${p.d}`, '1mo': (p) => `${p.y}-${p.m}-${p.d}`,
      '6mo': (p) => `${p.y}-${p.m}`, ytd: (p) => `${p.y}-${p.m}`, '1y': (p) => `${p.y}-${p.m}`,
      '5y': (p) => `${p.y}`, max: (p) => `${p.y}`,
    }[data.range] || ((p) => `${p.y}-${p.m}`);
    const label = {
      '5d': z.weekday, '1mo': z.day,
      '6mo': (t) => (z.get(t).m === 1 ? z.monthYear(t) : z.month(t)),
      ytd: (t) => (z.get(t).m === 1 ? z.monthYear(t) : z.month(t)),
      '1y': (t) => (z.get(t).m === 1 ? z.monthYear(t) : z.month(t)),
      '5y': z.year, max: z.year,
    }[data.range] || z.day;
    const starts = [];
    let prev = null;
    pts.forEach((p, i) => {
      const k = key(z.get(p[0]));
      if (k !== prev) { if (i > 0 || data.range === '5d') starts.push(i); prev = k; }
    });
    // A zoomed window can hold fewer boundaries than labels fit: fall back to days.
    if (starts.length < 2 && n > 2 && data.range !== '5d') {
      const step = Math.max(1, Math.round(n / 6));
      for (let i = step; i < n; i += step) starts.push(i);
      const stride = 1;
      return { x, xt: null, ticks: starts.filter((_, j) => j % stride === 0).map((i) => ({ x: x(i), label: z.day(pts[i][0]) })), ext: [] };
    }
    const stride = Math.max(1, Math.ceil(starts.length / 7));
    const ticks = starts.filter((_, j) => j % stride === 0).map((i) => ({ x: x(i), label: label(pts[i][0]) }));
    return { x, xt: null, ticks, ext: [] };
  }

  function yModel(values, target = 5) {
    const vals = values.filter(finite);
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    if (!vals.length) { lo = 0; hi = 1; }
    if (hi === lo) { hi += Math.abs(hi) * 0.01 || 1; lo -= Math.abs(lo) * 0.01 || 1; }
    const step = niceStep(hi - lo, target);
    const pad = (hi - lo) * 0.04;
    const y0 = Math.floor((lo - pad) / step) * step;
    const y1 = Math.ceil((hi + pad) / step) * step;
    const ticks = [];
    for (let v = y0; v <= y1 + step / 2; v += step) ticks.push(+v.toFixed(8));
    return { y0, y1, step, ticks, digits: decimalsFor(step) };
  }

  // ------------------------------------------------------------ preferences

  function loadPrefs(context) {
    const base = DEFAULTS[context] || DEFAULTS.default;
    let saved = null;
    try { saved = JSON.parse(store.get(`quanta_chart_prefs_${context}`) || 'null'); } catch (_) { saved = null; }
    const known = new Set(STUDIES.map((s) => s.key));
    return {
      type: saved && ['line', 'candles'].includes(saved.type) ? saved.type : base.type,
      studies: new Set(saved && Array.isArray(saved.studies) ? saved.studies.filter((k) => known.has(k)) : base.studies),
      compare: Boolean(saved && saved.compare),
    };
  }
  function savePrefs(context, prefs) {
    store.set(`quanta_chart_prefs_${context}`, JSON.stringify({ type: prefs.type, studies: [...prefs.studies], compare: prefs.compare }));
  }

  // --------------------------------------------------------------- compute

  // Every study over lead + points, sliced back to the plotted points.
  function computeSeries(data, z) {
    const lead = Array.isArray(data.lead) ? data.lead : [];
    const all = lead.concat(data.points);
    const closes = all.map((b) => b[1]);
    const off = lead.length;
    const cut = (arr) => arr.slice(off);
    const bb = bollinger(closes, 20, 2);
    const m = macd(closes);
    const intraday = ['1d', '5d', '1mo'].includes(data.range);
    const outliers = intraday ? volumeOutliers(data.points) : new Set();
    return {
      outliers,
      sma20: cut(sma(closes, 20)),
      sma50: cut(sma(closes, 50)),
      sma200: cut(sma(closes, 200)),
      ema21: cut(ema(closes, 21)),
      bbUpper: cut(bb.upper),
      bbLower: cut(bb.lower),
      bbMid: cut(bb.mid),
      vwap: intraday ? vwap(data.points, z.dayKey, outliers) : null,
      rsi: cut(rsi(closes, 14)),
      macd: cut(m.line),
      macdSignal: cut(m.signal),
      macdHist: cut(m.hist),
    };
  }

  // Benchmark closes aligned to each plotted bar: the latest benchmark bar at or before it.
  function alignBenchmark(points, bench) {
    const out = new Array(points.length).fill(null);
    if (!bench || !bench.points || !bench.points.length) return out;
    const b = bench.points;
    let j = 0;
    for (let i = 0; i < points.length; i += 1) {
      const t = points[i][0];
      while (j + 1 < b.length && b[j + 1][0] <= t + 60000) j += 1;
      if (b[j][0] <= t + 60000) out[i] = b[j][1];
    }
    return out;
  }

  // ---------------------------------------------------------------- render

  function render(state) {
    const { data, opts, prefs } = state;
    const z = zoned(data.timezone);
    const full = data.points;
    const N = full.length;
    if (!state.view || state.view.i1 > N - 1 || state.view.i0 < 0) state.view = { i0: 0, i1: N - 1 };
    const { i0, i1 } = state.view;
    const zoomed = i0 > 0 || i1 < N - 1;
    const pts = full.slice(i0, i1 + 1);
    const n = pts.length;
    const cut = (arr) => (arr ? arr.slice(i0, i1 + 1) : null);
    const S = state.series;
    const series = {};
    Object.keys(S).forEach((k) => { if (Array.isArray(S[k])) series[k] = cut(S[k]); });
    const unusual = new Set([...(S.outliers || [])].filter((i) => i >= i0 && i <= i1).map((i) => i - i0));
    const is1d = data.range === '1d';
    const intraday = ['1d', '5d', '1mo'].includes(data.range);
    const on = (k) => prefs.studies.has(k) && !(k === 'vwap' && !intraday);
    const compare = prefs.compare && state.bench && benchmarkFor(data.symbol);
    const candles = prefs.type === 'candles' && !compare;

    // Percent mode re-expresses every price relative to the first visible bar.
    const closesRaw = pts.map((p) => p[1]);
    const base0 = closesRaw[0];
    const tf = compare ? (v) => (finite(v) ? ((v / base0) - 1) * 100 : null) : (v) => v;
    const closes = closesRaw.map(tf);
    const opens = pts.map((p) => tf(finite(p[3]) ? p[3] : p[1]));
    const highs = pts.map((p) => tf(finite(p[4]) ? p[4] : p[1]));
    const lows = pts.map((p) => tf(finite(p[5]) ? p[5] : p[1]));
    let benchPct = null;
    if (compare) {
      const aligned = cut(state.benchAligned);
      const b0 = aligned.find(finite);
      benchPct = aligned.map((v) => (finite(v) && finite(b0) ? ((v / b0) - 1) * 100 : null));
    }

    const prevClose = is1d && finite(data.previous_close) ? data.previous_close : null;
    // The day's reference is the previous close even in a zoomed window.
    const base = prevClose !== null ? prevClose : closesRaw[0];
    const last = closesRaw[n - 1];
    const up = last >= base;
    const good = isInverse(data) ? !up : up;
    const cur = compare || isLevel(data) ? '' : symOf(data.currency);
    const unit = compare ? '%' : '';

    // Overlays (in price units, transformed in percent mode).
    const overlays = [];
    if (!compare) {
      [['sma20', 1], ['sma50', 2], ['sma200', 3], ['ema21', 4]].forEach(([k, tone]) => {
        if (on(k)) overlays.push({ key: k, label: STUDIES.find((s) => s.key === k).label, tone, values: series[k] });
      });
      if (on('vwap') && series.vwap) overlays.push({ key: 'vwap', label: 'VWAP', tone: 4, values: series.vwap, dashed: true });
    }
    const bbOn = on('bb') && !compare;

    // Reference lines that fit near the data are drawn; the rest are listed.
    const span = Math.max(...highs.filter(finite)) - Math.min(...lows.filter(finite));
    const hiP = Math.max(...highs.filter(finite));
    const loP = Math.min(...lows.filter(finite));
    const near = (v) => finite(v) && v >= loP - span * 0.35 && v <= hiP + span * 0.35;
    const levels = (opts.levels || []).filter((l) => finite(l.value)).map((l) => ({ ...l, plot: tf(l.value) }));
    const shown = levels.filter((l) => near(l.plot));
    const off = levels.filter((l) => !near(l.plot));
    const refPlot = prevClose !== null && !compare && !zoomed ? prevClose : null;
    const yVals = [].concat(candles ? highs.concat(lows) : closes, refPlot !== null ? [refPlot] : [], shown.map((l) => l.plot),
      benchPct || [], overlays.flatMap((o) => o.values.filter(finite)), bbOn ? series.bbUpper.concat(series.bbLower).filter(finite) : []);
    const ym = yModel(yVals);
    const vols = pts.map((p) => p[2]);
    const hasVol = on('vol') && vols.some((v) => finite(v) && v > 0);
    const priceH = hasVol ? H * (1 - VOL_SHARE) - 6 : H;

    // Panes under the price pane: their height in viewBox units matches their CSS
    // share of the frame, so a pane of PANE_PX pixels is drawn PANE_PX tall.
    const panes = ['rsi', 'macd'].filter(on);
    const lcH = state.lcH || 320;
    const SUB = (PANE_PX / lcH) * H;
    const totalH = H + panes.length * SUB;
    const y = (v) => ((ym.y1 - v) / (ym.y1 - ym.y0)) * priceH;
    const pctOf = (v) => `${((v / totalH) * 100).toFixed(3)}%`;
    const xm = xModel(zoomed ? { ...data, points: pts, session: is1d ? (data.session || {}) : {} } : { ...data, points: pts }, z);
    if (zoomed && is1d) {
      // A zoomed 1D window spans its own bars, not the whole trading day.
      const lo = pts[0][0];
      const hi = Math.max(pts[n - 1][0], lo + 60000);
      const x = (t) => ((t - lo) / (hi - lo)) * W;
      const zx = xModel({ ...data, points: pts, session: {} }, z);
      xm.x = (i) => x(pts[i][0]);
      xm.ticks = zx.ticks;
      xm.ext = (data.session ? [data.session.pre, data.session.post] : []).filter(Boolean)
        .map(([a, b]) => [x(Math.max(a, lo)), x(Math.min(b, hi))]).filter(([a, b]) => b > a);
    }
    // As many time labels as the plot's real width holds, about one per 72px.
    const plotPx = Math.max(160, (state.host.clientWidth || 600) - 70);
    const room = Math.max(2, Math.floor(plotPx / 72));
    if (xm.ticks.length > room) {
      const k = Math.ceil(xm.ticks.length / room);
      xm.ticks = xm.ticks.filter((_, i) => i % k === 0);
    }
    const xs = pts.map((_, i) => xm.x(i));
    const bw = Math.max(1, Math.min(14, (W / Math.max(n, 1)) * 0.68));

    const path = (vals, yf) => {
      let d = '';
      let pen = false;
      vals.forEach((v, i) => {
        if (!finite(v)) { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${xs[i].toFixed(1)},${yf(v).toFixed(1)}`;
        pen = true;
      });
      return d;
    };
    const hline = (cls, yy, x1 = 0, x2 = W) => `<line class="${cls}" x1="${x1}" x2="${x2}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}"/>`;

    const grid = ym.ticks.map((v) => hline('lc-grid', y(v))).join('');
    const vgrid = xm.ticks.map((t) => `<line class="lc-vgrid" x1="${t.x.toFixed(1)}" x2="${t.x.toFixed(1)}" y1="0" y2="${totalH.toFixed(1)}"/>`).join('');
    const ext = xm.ext.map(([a, b]) => `<rect class="lc-ext" x="${a.toFixed(1)}" y="0" width="${Math.max(0, b - a).toFixed(1)}" height="${H}"/>`).join('');
    let volBars = '';
    if (hasVol) {
      // Unusual prints are drawn at the top of the scale, marked, not to scale.
      // Scaled to the 98th percentile, so an opening auction or closing cross does
      // not flatten every other bar; the few taller bars reach the top of the band.
      const usual = vols.filter((v, i) => finite(v) && v > 0 && !unusual.has(i)).sort((a, b) => a - b);
      const vmax = usual.length ? Math.max(1, usual[Math.min(usual.length - 1, Math.floor(usual.length * 0.98))]) : 1;
      volBars = pts.map((p, i) => {
        if (!finite(p[2]) || p[2] <= 0) return '';
        const odd = unusual.has(i);
        const h = Math.min(1, p[2] / vmax) * (H * VOL_SHARE);
        const rising = i === 0 ? p[1] >= (finite(p[3]) ? p[3] : p[1]) : p[1] >= pts[i - 1][1];
        return `<rect class="lc-vol ${rising ? 'is-up' : 'is-down'}${odd ? ' is-unusual' : ''}" x="${(xs[i] - bw / 2).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"/>`;
      }).join('');
    }

    let priceMarks = '';
    if (candles) {
      priceMarks = pts.map((p, i) => {
        const o = opens[i]; const c = closes[i]; const h = highs[i]; const l = lows[i];
        const rising = c >= o;
        const top = y(Math.max(o, c));
        const bot = y(Math.min(o, c));
        const bodyH = Math.max(0.8, bot - top);
        return `<g class="lc-candle ${rising ? 'is-up' : 'is-down'}"><line class="lc-wick" x1="${xs[i].toFixed(1)}" x2="${xs[i].toFixed(1)}" y1="${y(h).toFixed(1)}" y2="${y(l).toFixed(1)}"/>`
          + `<rect class="lc-body" x="${(xs[i] - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${bodyH.toFixed(1)}"/></g>`;
      }).join('');
    } else {
      const line = path(closes, y);
      const area = `${line}L${xs[n - 1].toFixed(1)},${priceH.toFixed(1)}L${xs[0].toFixed(1)},${priceH.toFixed(1)}Z`;
      const gid = `lc-fill-${state.uid}`;
      priceMarks = `<defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" class="lc-stop-a"/><stop offset="1" class="lc-stop-b"/></linearGradient></defs>`
        + (compare ? '' : `<path class="lc-area" d="${area}" fill="url(#${gid})"/>`)
        + `<path class="lc-line" d="${line}"/>`;
    }
    const benchLine = benchPct ? `<path class="lc-bench" d="${path(benchPct, y)}"/>` : '';
    const zeroLine = compare ? hline('lc-ref', y(0)) : '';
    let bbMarks = '';
    if (bbOn) {
      const upper = series.bbUpper; const lower = series.bbLower;
      const idx = upper.map((v, i) => (finite(v) && finite(lower[i]) ? i : -1)).filter((i) => i >= 0);
      if (idx.length > 1) {
        const top = idx.map((i, j) => `${j ? 'L' : 'M'}${xs[i].toFixed(1)},${y(upper[i]).toFixed(1)}`).join('');
        const bottom = idx.slice().reverse().map((i) => `L${xs[i].toFixed(1)},${y(lower[i]).toFixed(1)}`).join('');
        bbMarks = `<path class="lc-band" d="${top}${bottom}Z"/><path class="lc-study lc-study--1 is-thin" d="${path(upper, y)}"/><path class="lc-study lc-study--1 is-thin" d="${path(lower, y)}"/><path class="lc-study lc-study--1 is-thin is-dashed" d="${path(series.bbMid, y)}"/>`;
      }
    }
    const studyMarks = overlays.map((o) => `<path class="lc-study lc-study--${o.tone}${o.dashed ? ' is-dashed' : ''}" d="${path(o.values, y)}"/>`).join('');
    const refLine = refPlot !== null ? hline('lc-ref', y(refPlot)) : '';
    const lvLines = shown.map((l) => hline(`lc-level lc-level--${esc(l.tone || 'neutral')}`, y(l.plot))).join('');

    // Dividend and split markers, and any events the page passed in (earnings).
    const allEvents = [].concat(data.events || [], opts.events || []).filter((e) => finite(e.t));
    const evs = allEvents.filter((e) => e.t >= pts[0][0] && e.t <= pts[n - 1][0] + 86400000);
    const upcoming = allEvents.filter((e) => e.t > full[N - 1][0] + 86400000).sort((a, b) => a.t - b.t);
    const nearestIdx = (t) => {
      let lo = 0; let hi = n - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (pts[mid][0] < t) lo = mid; else hi = mid; }
      return Math.abs(pts[lo][0] - t) <= Math.abs(pts[hi][0] - t) ? lo : hi;
    };
    const evLetter = { dividend: 'D', split: 'S', earnings: 'E' };
    const evMarks = evs.map((e) => {
      const i = nearestIdx(e.t);
      const what = e.kind === 'dividend' ? `Dividend ${esc(symOf(data.currency))}${fmtNum(e.value, 2)}`
        : e.kind === 'split' ? `Split ${fmtNum(e.value, e.value % 1 ? 2 : 0)} for 1` : esc(e.label || 'Earnings');
      return `<span class="lc-event lc-event--${esc(e.kind)}" style="left:${((xs[i] / W) * 100).toFixed(3)}%;top:${pctOf(priceH - 2)}" title="${what}, ${esc(z.full(pts[i][0]))}">${evLetter[e.kind] || '•'}</span>`;
    }).join('');

    // Sub-panes.
    let paneSvg = '';
    let paneLabels = '';
    let paneLegends = '';
    const paneModels = {};
    panes.forEach((k, pi) => {
      const top = H + pi * SUB;
      const inner = SUB - 10;
      const pTop = top + 6;
      let pm;
      if (k === 'rsi') {
        pm = { y0: 0, y1: 100, ticks: [30, 70] };
        const py = (v) => pTop + ((pm.y1 - v) / (pm.y1 - pm.y0)) * inner;
        paneModels.rsi = py;
        paneSvg += hline('lc-pane-sep', top) + `<rect class="lc-rsi-zone" x="0" y="${py(70).toFixed(1)}" width="${W}" height="${(py(30) - py(70)).toFixed(1)}"/>`
          + hline('lc-rsi-guide', py(70)) + hline('lc-rsi-guide', py(30)) + `<path class="lc-study lc-study--rsi" d="${path(series.rsi, py)}"/>`;
        paneLabels += pm.ticks.map((v) => `<span class="lc-ytick lc-ytick--pane" style="top:${pctOf(py(v))}">${v}</span>`).join('');
      } else {
        const vals = [].concat(series.macd, series.macdSignal, series.macdHist).filter(finite);
        const amp = Math.max(1e-9, ...vals.map(Math.abs));
        pm = { y0: -amp * 1.1, y1: amp * 1.1 };
        const py = (v) => pTop + ((pm.y1 - v) / (pm.y1 - pm.y0)) * inner;
        paneModels.macd = py;
        const hist = series.macdHist.map((v, i) => (finite(v)
          ? `<rect class="lc-hist ${v >= 0 ? 'is-up' : 'is-down'}" x="${(xs[i] - bw / 2).toFixed(1)}" y="${Math.min(py(v), py(0)).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.6, Math.abs(py(v) - py(0))).toFixed(1)}"/>` : '')).join('');
        paneSvg += hline('lc-pane-sep', top) + hline('lc-grid', py(0)) + hist
          + `<path class="lc-study lc-study--1" d="${path(series.macd, py)}"/><path class="lc-study lc-study--2" d="${path(series.macdSignal, py)}"/>`;
        paneLabels += `<span class="lc-ytick lc-ytick--pane" style="top:${pctOf(py(0))}">0</span>`;
      }
      paneLegends += `<div class="lc-legend lc-legend--pane" data-pane="${k}" style="top:${pctOf(top + 2)}"></div>`;
    });

    // Tick labels step aside for the price tags (last price, previous close).
    const framePx = (state.frame && state.frame.clientHeight) || 320;
    const clear = (16 / Math.max(120, framePx - 22)) * totalH;
    const lastPlot = closes[n - 1];
    const tags = [lastPlot].concat(refPlot !== null ? [refPlot] : []).map((v) => y(v));
    const fmtY = (v) => `${fmtNum(v, compare ? Math.max(0, ym.digits) : ym.digits)}${unit}`;
    const yLabels = ym.ticks.filter((v) => tags.every((t) => Math.abs(y(v) - t) >= clear) && y(v) <= priceH - (hasVol ? 8 : -1))
      .map((v) => `<span class="lc-ytick" style="top:${pctOf(y(v))}">${fmtY(v)}</span>`).join('');
    const badges = [
      refPlot !== null ? `<span class="lc-badge lc-badge--ref" style="top:${pctOf(y(refPlot))}" title="Previous close">${fmtNum(refPlot, priceDigits(refPlot))}</span>` : '',
      `<span class="lc-badge lc-badge--last ${good ? 'is-up' : 'is-down'}" style="top:${pctOf(y(lastPlot))}" title="${compare ? 'Change from the first bar' : 'Last price'}">${compare ? fmtSigned(lastPlot) + '%' : fmtNum(last, priceDigits(last))}</span>`,
    ].join('');
    const lvLabels = shown.map((l) => `<span class="lc-lvlabel lc-lvlabel--${esc(l.tone || 'neutral')}" style="top:${pctOf(y(l.plot))}">${esc(l.label)} ${esc(symOf(data.currency))}${fmtNum(l.value, priceDigits(l.value))}</span>`).join('')
      + (refPlot !== null ? `<span class="lc-lvlabel lc-lvlabel--ref" style="top:${pctOf(y(refPlot))}">Previous close</span>` : '');
    const xLabels = xm.ticks.map((t) => `<span class="lc-xtick${t.x < W * 0.04 ? ' is-edge' : t.x > W * 0.96 ? ' is-edge-end' : ''}" style="left:${((t.x / W) * 100).toFixed(3)}%">${esc(t.label)}</span>`).join('');

    const hi = Math.max(...highs.filter(finite));
    const lo = Math.min(...lows.filter(finite));
    const studyWords = [...prefs.studies].filter((k) => on(k) && k !== 'vol').map((k) => STUDIES.find((s) => s.key === k).label);
    const summary = compare
      ? `${data.symbol} against ${benchmarkFor(data.symbol)}, ${RANGE_WORDS[data.range] || data.range}: ${fmtSigned(lastPlot)}% against ${fmtSigned(benchPct.filter(finite).slice(-1)[0])}%. Use the arrow keys to read each bar.`
      : `${data.symbol} ${RANGE_WORDS[data.range] || data.range}${zoomed ? ', zoomed' : ''}, ${candles ? 'candles' : 'line'}: ${fmtNum(closesRaw[0], priceDigits(closesRaw[0]))} to ${fmtNum(last, priceDigits(last))} (${fmtSigned(((last - base) / base) * 100)}% vs ${refPlot !== null ? 'previous close' : 'first bar'}), high ${fmtNum(hi, priceDigits(hi))}, low ${fmtNum(lo, priceDigits(lo))}${studyWords.length ? `; studies ${studyWords.join(', ')}` : ''}. Use the arrow keys to read each bar; plus and minus zoom.`;

    state.model = { unusual, z, xs, y, closes, closesRaw, opens, highs, lows, base, cur, unit, hasVol, priceH, totalH, pts, series, overlays, bbOn, benchPct, compare, paneModels, panes, candles, pctOf };
    state.frame.style.setProperty('--lc-panes', String(panes.length));
    state.frame.classList.toggle('is-zoomed', zoomed);
    state.frame.innerHTML = `
      <div class="lc-plot">
        <svg class="lc-svg ${good ? 'is-up' : 'is-down'}${candles ? ' is-candles' : ''}" viewBox="0 0 ${W} ${totalH.toFixed(1)}" preserveAspectRatio="none" tabindex="0" role="img" aria-label="${esc(summary)}" focusable="true">
          ${ext}${vgrid}${grid}${volBars}${bbMarks}
          ${zeroLine}${refLine}${lvLines}
          ${priceMarks}${benchLine}${studyMarks}
          ${paneSvg}
          <line class="lc-cross" x1="0" x2="0" y1="0" y2="${totalH.toFixed(1)}" visibility="hidden"/>
          <line class="lc-cross lc-cross--h" x1="0" x2="${W}" y1="0" y2="0" visibility="hidden"/>
        </svg>
        <div class="lc-overlay" aria-hidden="true">
          <div class="lc-legend" data-pane="price"></div>${paneLegends}
          ${lvLabels}${evMarks}<span class="lc-dot" hidden></span>
        </div>
      </div>
      <div class="lc-yaxis" aria-hidden="true">${yLabels}${paneLabels}${badges}<span class="lc-badge lc-badge--hover" hidden></span></div>
      <div class="lc-xaxis" aria-hidden="true">${xLabels}<span class="lc-xbadge" hidden></span></div>
      <p class="sr-only lc-live" aria-live="polite"></p>`;
    if (upcoming.length) {
      const days = (t) => Math.max(1, Math.round((t - Date.now()) / 86400000));
      state.frame.insertAdjacentHTML('beforeend', `<p class="lc-upcoming">${upcoming.slice(0, 2).map((e) => `<span class="lc-event lc-event--${esc(e.kind)} is-inline" aria-hidden="true">${evLetter[e.kind] || '•'}</span>${esc(e.label || e.kind)} ${esc(z.full(e.t))} (in ${days(e.t)} day${days(e.t) === 1 ? '' : 's'})`).join('; ')}</p>`);
    }
    if (off.length) {
      state.frame.insertAdjacentHTML('beforeend', `<p class="lc-offscale">${off.map((l) => `<span class="lc-lvlabel--${esc(l.tone || 'neutral')}">${esc(l.label)} ${esc(symOf(data.currency))}${fmtNum(l.value, priceDigits(l.value))}</span> ${l.value > last ? 'above' : 'below'} this range`).join('; ')}</p>`);
    }
    bindCrosshair(state);
    writeLegends(state, n - 1);
    renderHead(state, { last, base, up: good });
    renderTools(state);
  }

  // The legend reads one bar: OHLC, change, volume and each study's value.
  function writeLegends(state, idx) {
    const m = state.model;
    const legend = state.frame.querySelector('.lc-legend[data-pane="price"]');
    if (!legend) return;
    const p = m.pts[idx];
    const d = priceDigits(m.closesRaw[idx]);
    const prev = idx > 0 ? m.closesRaw[idx - 1] : null;
    const chg = prev !== null ? m.closesRaw[idx] - prev : null;
    const parts = [];
    // A narrow chart (the Research column, a phone) reads the close and its
    // change; the full open, high, low and volume need a wide plot.
    const narrow = (state.host.clientWidth || 600) < 640;
    if (m.compare) {
      parts.push(`<b>${esc(state.data.symbol)}</b> <span class="${m.closes[idx] >= 0 ? 'bull' : 'bear'}">${fmtSigned(m.closes[idx])}%</span>`);
      if (m.benchPct && finite(m.benchPct[idx])) parts.push(`<span class="lc-legend-bench">${esc(benchmarkFor(state.data.symbol))} ${fmtSigned(m.benchPct[idx])}%</span>`);
    } else {
      const ohlc = (narrow ? [['C', p[1]]] : [['O', p[3]], ['H', p[4]], ['L', p[5]], ['C', p[1]]])
        .map(([k, v]) => `<span class="lc-legend-k">${k}</span>${fmtNum(finite(v) ? v : p[1], d)}`).join(' ');
      parts.push(ohlc);
      if (finite(chg)) parts.push(`<span class="${chg >= 0 ? 'bull' : 'bear'}">${fmtSigned(chg, d)} (${fmtSigned((chg / prev) * 100)}%)</span>`);
      if (!narrow && m.hasVol && finite(p[2])) parts.push(`<span class="lc-legend-k">Vol</span>${fmtVol(p[2])}${m.unusual && m.unusual.has(idx) ? ' <span class="lc-legend-warn">unusual print, left out of VWAP</span>' : ''}`);
    }
    const studies = m.overlays.map((o) => (finite(o.values[idx]) ? `<span class="lc-legend-study lc-legend-study--${o.tone}">${esc(o.label)} ${fmtNum(o.values[idx], d)}</span>` : ''))
      .concat(m.bbOn && finite(m.series.bbUpper[idx]) ? [`<span class="lc-legend-study lc-legend-study--1">BB ${fmtNum(m.series.bbUpper[idx], d)} / ${fmtNum(m.series.bbLower[idx], d)}</span>`] : [])
      .filter(Boolean).slice(0, narrow ? 2 : 6).join('');
    legend.innerHTML = `<div class="lc-legend-row">${parts.join(' ')}</div>${studies ? `<div class="lc-legend-row">${studies}</div>` : ''}`;
    m.panes.forEach((k) => {
      const el = state.frame.querySelector(`.lc-legend[data-pane="${k}"]`);
      if (!el) return;
      if (k === 'rsi') {
        const v = m.series.rsi[idx];
        el.innerHTML = `<span class="lc-legend-study lc-legend-study--rsi">RSI 14 ${finite(v) ? fmtNum(v, 1) : '--'}</span>`;
      } else {
        const a = m.series.macd[idx]; const s = m.series.macdSignal[idx]; const h = m.series.macdHist[idx];
        const dd = Math.abs(a || 0) < 1 ? 3 : 2;
        el.innerHTML = `<span class="lc-legend-study lc-legend-study--1">MACD ${finite(a) ? fmtNum(a, dd) : '--'}</span><span class="lc-legend-study lc-legend-study--2">Signal ${finite(s) ? fmtNum(s, dd) : '--'}</span><span class="${(h || 0) >= 0 ? 'bull' : 'bear'}">Hist ${finite(h) ? fmtSigned(h, dd) : '--'}</span>`;
      }
    });
  }

  function renderHead(state, { last, base, up }) {
    const { data, opts } = state;
    const z = state.model.z;
    const cur = isLevel(data) ? '' : symOf(data.currency);
    const is1d = data.range === '1d';
    let quote = '';
    if (opts.showQuote) {
      const regular = finite(data.regular_price) ? data.regular_price : last;
      const prev = finite(data.previous_close) ? data.previous_close : null;
      const dayChg = is1d && prev ? regular - prev : null;
      const rangeChg = !is1d ? last - base : null;
      const chg = is1d ? dayChg : rangeChg;
      const chgPct = is1d ? (prev ? (dayChg / prev) * 100 : null) : (base ? (rangeChg / base) * 100 : null);
      const tone = finite(chg) ? ((chg >= 0) !== isInverse(data) ? 'bull' : 'bear') : '';
      const asOf = is1d
        ? (data.market_open ? `Market open, as of ${z.time(data.regular_time || data.retrieved_at)}` : `At close: ${z.day(data.regular_time || data.retrieved_at)}, ${z.time(data.regular_time || data.retrieved_at)}`)
        : `Change ${RANGE_WORDS[data.range] || ''}, last bar ${z.full(state.data.points[state.data.points.length - 1][0])}`;
      const extd = is1d && data.extended && finite(data.extended.price) && Math.abs(data.extended.price - regular) >= 0.005
        ? (() => {
          const e = data.extended;
          const c = e.price - regular;
          return `<div class="lc-q lc-q--ext"><span class="lc-px lc-px--sm">${fmtNum(e.price, priceDigits(e.price))}</span><span class="lc-chg ${(c >= 0) !== isInverse(data) ? 'bull' : 'bear'}">${fmtSigned(c)} (${fmtSigned((c / regular) * 100)}%)</span><span class="lc-asof">${e.session === 'pre' ? 'Pre-market' : 'After hours'}: ${esc(z.time(e.time))}</span></div>`;
        })() : '';
      const shownPx = is1d ? regular : last;
      quote = `
        <div class="lc-q"><span class="lc-px${state.flash ? ` is-flash-${state.flash}` : ''}">${esc(cur)}${fmtNum(shownPx, priceDigits(shownPx))}</span><span class="lc-chg ${tone}">${fmtSigned(chg)} (${fmtSigned(chgPct)}%)</span><span class="lc-asof">${esc(asOf)}</span></div>${extd}`;
    } else {
      const chgPct = base ? ((last - base) / base) * 100 : null;
      const when = data.market_open && (is1d || state.livePatched) ? `Live, ${z.time(data.retrieved_at)}` : `Last bar ${is1d ? z.time(data.points[data.points.length - 1][0]) : z.full(data.points[data.points.length - 1][0])}`;
      quote = `<div class="lc-q lc-q--compact"><span class="lc-px lc-px--sm${state.flash ? ` is-flash-${state.flash}` : ''}">${esc(cur)}${fmtNum(last, priceDigits(last))}</span><span class="lc-chg ${up ? 'bull' : 'bear'}">${fmtSigned(chgPct)}% ${esc(is1d && finite(data.previous_close) ? 'vs previous close' : RANGE_WORDS[data.range] || '')}</span><span class="lc-asof">${esc(when)}</span></div>`;
    }
    state.quote.innerHTML = quote;
    state.flash = '';
    const liveNow = isLive(state);
    const every = REFRESH_MS[data.range] ? `every ${REFRESH_MS[data.range] / 1000} seconds` : 'the last bar every minute';
    const updated = state.lastUpdate ? `, updated ${z.seconds(state.lastUpdate)}` : '';
    state.status.innerHTML = `${liveNow ? `<span class="lc-livedot" aria-hidden="true"></span>Live: refreshes ${every}${esc(updated)}` : ''}${data.stale ? '<span class="lc-stale">Provider busy: showing the last bars fetched</span>' : ''}`;
    state.status.hidden = !liveNow && !data.stale;
  }

  function isLive(state) {
    const d = state.data;
    if (!d) return false;
    if (d.range === '1d') {
      const sess = d.session || {};
      const start = (sess.pre && sess.pre[0]) || (sess.regular && sess.regular[0]);
      const end = (sess.post && sess.post[1]) || (sess.regular && sess.regular[1]);
      return Boolean(d.market_open || (start && end && Date.now() >= start && Date.now() < end));
    }
    return Boolean(d.market_open);
  }

  // The toolbar: chart type, studies, compare and zoom. Rebuilt with each render
  // so it always says what the chart shows.
  function renderTools(state) {
    const { prefs, data } = state;
    const bench = benchmarkFor(data.symbol);
    const intraday = ['1d', '5d', '1mo'].includes(data.range);
    const zoomed = state.view && (state.view.i0 > 0 || state.view.i1 < data.points.length - 1);
    const count = [...prefs.studies].filter((k) => k !== 'vol' && !(k === 'vwap' && !intraday)).length;
    const open = state.tools.querySelector('.lc-studies') && state.tools.querySelector('.lc-studies').open;
    state.tools.innerHTML = `
      <div class="lc-seg" role="group" aria-label="Chart type">
        <button type="button" class="lc-tool" data-lc-type="line" aria-pressed="${prefs.type === 'line'}"${prefs.compare && bench ? ' disabled' : ''}>Line</button>
        <button type="button" class="lc-tool" data-lc-type="candles" aria-pressed="${prefs.type === 'candles'}"${prefs.compare && bench ? ' disabled' : ''}>Candles</button>
      </div>
      <details class="lc-studies"${open ? ' open' : ''}>
        <summary class="lc-tool">Indicators${count ? ` <span class="lc-count">${count}</span>` : ''}</summary>
        <div class="lc-studies-menu" role="group" aria-label="Indicators">
          ${STUDIES.map((s) => {
            const disabled = (s.intraday && !intraday) || (prefs.compare && bench && s.overlay);
            const note = s.intraday && !intraday ? ' (intraday ranges)' : prefs.compare && bench && s.overlay ? ' (off while comparing)' : '';
            return `<label class="lc-check${disabled ? ' is-disabled' : ''}"><input type="checkbox" data-lc-study="${s.key}"${prefs.studies.has(s.key) ? ' checked' : ''}${disabled ? ' disabled' : ''}>`
              + `${s.tone ? `<i class="lc-swatch lc-swatch--${s.tone}" aria-hidden="true"></i>` : ''}${esc(s.label)}<span class="lc-check-note">${esc(note)}</span></label>`;
          }).join('')}
        </div>
      </details>
      ${bench ? `<button type="button" class="lc-tool" data-lc-compare aria-pressed="${prefs.compare}" title="Percent change against ${esc(bench)} from the first visible bar">Compare ${esc(bench === '^NSEI' ? 'Nifty 50' : bench)}</button>` : ''}
      <div class="lc-seg lc-zoom" role="group" aria-label="Zoom">
        <button type="button" class="lc-tool" data-lc-zoom="in" aria-label="Zoom in" title="Zoom in (+, or Ctrl and scroll)">+</button>
        <button type="button" class="lc-tool" data-lc-zoom="out" aria-label="Zoom out" title="Zoom out (-)"${zoomed ? '' : ' disabled'}>&minus;</button>
        <button type="button" class="lc-tool" data-lc-zoom="reset" title="Show the whole range (0)"${zoomed ? '' : ' disabled'}>Reset</button>
      </div>`;
  }

  // ------------------------------------------------------------ interaction

  function zoom(state, factor, centre) {
    const N = state.data.points.length;
    const { i0, i1 } = state.view;
    const span = i1 - i0;
    const c = centre == null ? i1 : centre;
    let next = Math.round(span * factor);
    next = Math.max(Math.min(MIN_VIEW, N - 1), Math.min(N - 1, next));
    // Keep the bar under the pointer (or the latest bar) where it is.
    const share = span ? (c - i0) / span : 1;
    let a = Math.round(c - share * next);
    a = Math.max(0, Math.min(N - 1 - next, a));
    state.view = { i0: a, i1: a + next };
    render(state);
  }

  function pan(state, bars) {
    const N = state.data.points.length;
    const { i0, i1 } = state.view;
    const span = i1 - i0;
    const a = Math.max(0, Math.min(N - 1 - span, i0 + bars));
    if (a === i0) return;
    state.view = { i0: a, i1: a + span };
    render(state);
  }

  function bindCrosshair(state) {
    const svg = state.frame.querySelector('.lc-svg');
    const cross = svg.querySelectorAll('.lc-cross')[0];
    const crossH = svg.querySelector('.lc-cross--h');
    const dot = state.frame.querySelector('.lc-dot');
    const hoverBadge = state.frame.querySelector('.lc-badge--hover');
    const xBadge = state.frame.querySelector('.lc-xbadge');
    const live = state.frame.querySelector('.lc-live');
    const m = state.model;
    const { xs, y, closes, cur, z, pts, unit } = m;
    const is1d = state.data.range === '1d';
    const intraday = ['1d', '5d', '1mo'].includes(state.data.range);
    let idx = pts.length - 1;
    const when = (i) => (intraday ? (is1d ? z.time(pts[i][0]) : `${z.day(pts[i][0])}, ${z.time(pts[i][0])}`) : z.full(pts[i][0]));
    const show = (i, announce, yUnits) => {
      idx = Math.max(0, Math.min(pts.length - 1, i));
      const xp = (xs[idx] / W) * 100;
      const yp = (y(closes[idx]) / m.totalH) * 100;
      cross.setAttribute('x1', xs[idx]); cross.setAttribute('x2', xs[idx]); cross.setAttribute('visibility', 'visible');
      dot.hidden = m.candles; dot.style.left = `${xp}%`; dot.style.top = `${yp}%`;
      // The horizontal hair follows the pointer inside the price pane, else the close.
      const hy = finite(yUnits) && yUnits <= m.priceH ? yUnits : y(closes[idx]);
      crossH.setAttribute('y1', hy); crossH.setAttribute('y2', hy); crossH.setAttribute('visibility', 'visible');
      const hv = finite(yUnits) && yUnits <= m.priceH ? (state.model.yInv ? state.model.yInv(yUnits) : closes[idx]) : closes[idx];
      hoverBadge.hidden = false; hoverBadge.style.top = `${(hy / m.totalH) * 100}%`;
      hoverBadge.textContent = `${fmtNum(hv, m.compare ? 2 : priceDigits(hv))}${unit}`;
      xBadge.hidden = false; xBadge.style.left = `${xp}%`; xBadge.textContent = when(idx);
      xBadge.classList.toggle('is-edge', xp < 8); xBadge.classList.toggle('is-edge-end', xp > 92);
      writeLegends(state, idx);
      if (announce && live) {
        const p = pts[idx];
        live.textContent = m.compare
          ? `${when(idx)}: ${fmtSigned(closes[idx])}%`
          : `${when(idx)}: open ${cur}${fmtNum(p[3])}, high ${fmtNum(p[4])}, low ${fmtNum(p[5])}, close ${cur}${fmtNum(p[1], priceDigits(p[1]))}`;
      }
    };
    const hide = () => {
      cross.setAttribute('visibility', 'hidden'); crossH.setAttribute('visibility', 'hidden');
      dot.hidden = true; hoverBadge.hidden = true; xBadge.hidden = true;
      writeLegends(state, pts.length - 1);
    };
    // Pointer y (viewBox units) back to a plotted value, for the hover badge.
    const ym = (() => {
      const a = y(1); const b = y(0);
      return (yy) => (b === a ? closes[idx] : (b - yy) / (b - a));
    })();
    state.model.yInv = ym;
    const toLocal = (ev) => {
      const r = svg.getBoundingClientRect();
      return { vx: ((ev.clientX - r.left) / r.width) * W, vy: ((ev.clientY - r.top) / r.height) * m.totalH, r };
    };
    const nearest = (vx) => {
      let lo = 0; let hi = xs.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < vx) lo = mid; else hi = mid; }
      return Math.abs(xs[lo] - vx) <= Math.abs(xs[hi] - vx) ? lo : hi;
    };
    let drag = null;
    svg.addEventListener('pointermove', (ev) => {
      const { vx, vy, r } = toLocal(ev);
      if (drag) {
        const barsPerPx = (state.view.i1 - state.view.i0) / Math.max(1, r.width);
        const moved = Math.round((drag.x - ev.clientX) * barsPerPx);
        if (moved !== 0) { drag.x = ev.clientX; pan(state, moved); }
        return;
      }
      show(nearest(vx), false, vy);
    });
    svg.addEventListener('pointerdown', (ev) => {
      const { vx, vy } = toLocal(ev);
      const zoomedNow = state.view.i0 > 0 || state.view.i1 < state.data.points.length - 1;
      if (zoomedNow && ev.pointerType !== 'touch') {
        drag = { x: ev.clientX };
        try { svg.setPointerCapture(ev.pointerId); } catch (_) { /* old browsers */ }
        state.frame.classList.add('is-panning');
      } else {
        show(nearest(vx), false, vy);
      }
    });
    const endDrag = (ev) => {
      if (!drag) return;
      drag = null;
      state.frame.classList.remove('is-panning');
      try { svg.releasePointerCapture(ev.pointerId); } catch (_) { /* not captured */ }
    };
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);
    svg.addEventListener('pointerleave', () => { if (!drag && document.activeElement !== svg) hide(); });
    svg.addEventListener('wheel', (ev) => {
      // Plain scrolling scrolls the page; Ctrl + scroll (and trackpad pinch) zooms.
      if (!ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault();
      const { vx } = toLocal(ev);
      zoom(state, ev.deltaY > 0 ? 1.25 : 0.8, state.view.i0 + nearest(vx));
    }, { passive: false });
    svg.addEventListener('dblclick', () => { state.view = null; render(state); });
    svg.addEventListener('focus', () => show(idx, true));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (ev) => {
      if (ev.key === '+' || ev.key === '=') { ev.preventDefault(); zoom(state, 0.7, state.view.i0 + idx); state.frame.querySelector('.lc-svg').focus(); return; }
      if (ev.key === '-' || ev.key === '_') { ev.preventDefault(); zoom(state, 1.4, state.view.i0 + idx); state.frame.querySelector('.lc-svg').focus(); return; }
      if (ev.key === '0') { ev.preventDefault(); state.view = null; render(state); state.frame.querySelector('.lc-svg').focus(); return; }
      const step = ev.shiftKey ? 10 : 1;
      const map = { ArrowLeft: idx - step, ArrowRight: idx + step, Home: 0, End: pts.length - 1 };
      if (!(ev.key in map)) return;
      ev.preventDefault();
      show(map[ev.key], true);
    });
  }

  // ----------------------------------------------------------------- live

  // Fold the live quote into the last bar of a longer range (or open a new bar).
  function patchLastBar(data, quote) {
    const price = quote && finite(quote.regular_price) ? quote.regular_price : null;
    const t = quote && finite(quote.regular_time) ? quote.regular_time : null;
    if (price === null || t === null || !data.points.length) return false;
    const z = zoned(data.timezone);
    const pts = data.points;
    const lastBar = pts[pts.length - 1];
    let same;
    if (INTERVAL_MS[data.interval]) same = t - lastBar[0] < INTERVAL_MS[data.interval];
    else if (data.interval === '1d') same = z.dayKey(t) === z.dayKey(lastBar[0]);
    else if (data.interval === '1wk') same = t - lastBar[0] < 7 * 86400000;
    else { const a = z.get(t); const b = z.get(lastBar[0]); same = a.y === b.y && a.m === b.m; }
    if (t < lastBar[0]) return false;
    if (same) {
      if (lastBar[1] === price) return false;
      lastBar[1] = price;
      lastBar[4] = Math.max(finite(lastBar[4]) ? lastBar[4] : price, price);
      lastBar[5] = Math.min(finite(lastBar[5]) ? lastBar[5] : price, price);
    } else {
      // Today's regular-session bars give the new bar's open, high, low and volume.
      const reg = (quote.points || []).filter((p) => !quote.session || !quote.session.regular
        || (p[0] >= quote.session.regular[0] && p[0] < quote.session.regular[1]));
      const o = reg.length ? (finite(reg[0][3]) ? reg[0][3] : reg[0][1]) : price;
      const h = reg.length ? Math.max(...reg.map((p) => (finite(p[4]) ? p[4] : p[1]))) : price;
      const l = reg.length ? Math.min(...reg.map((p) => (finite(p[5]) ? p[5] : p[1]))) : price;
      const v = reg.reduce((s, p) => s + (finite(p[2]) ? p[2] : 0), 0);
      const stamp = INTERVAL_MS[data.interval] ? t - (t % INTERVAL_MS[data.interval]) : t;
      pts.push([stamp, price, v || null, o, Math.max(h, price), Math.min(l, price)]);
    }
    data.market_open = Boolean(quote.market_open);
    data.retrieved_at = quote.retrieved_at || Date.now();
    return true;
  }

  // ---------------------------------------------------------------- mount

  function mount(host, options) {
    if (!host) return null;
    if (host._liveChart) host._liveChart.destroy();
    const opts = { context: 'default', showQuote: true, levels: [], events: [], ...options };
    const saved = store.get(`quanta_chart_range_${opts.context}`);
    const state = {
      host, opts, uid: Math.random().toString(36).slice(2, 8),
      range: RANGES.some(([k]) => k === saved) ? saved : (opts.range || '1d'),
      symbol: String(opts.symbol || '').toUpperCase(), data: null, timer: 0, liveTimer: 0, req: 0,
      prefs: loadPrefs(opts.context), view: null, series: {}, bench: null, benchAligned: null,
      lastUpdate: 0, flash: '', livePatched: false,
    };
    host.classList.add('lc');
    host.innerHTML = `
      <div class="lc-head">
        <div class="lc-quote"></div>
        <div class="lc-ranges" role="tablist" aria-label="Chart range">${RANGES.map(([k, l]) => `<button type="button" class="lc-range" role="tab" data-range="${k}" aria-selected="${k === state.range}" tabindex="${k === state.range ? 0 : -1}">${l}</button>`).join('')}</div>
      </div>
      <div class="lc-tools"></div>
      <div class="lc-frame" aria-busy="true"><div class="lc-loading">Loading price bars</div></div>
      <p class="lc-status" hidden></p>`;
    state.quote = host.querySelector('.lc-quote');
    state.tools = host.querySelector('.lc-tools');
    state.frame = host.querySelector('.lc-frame');
    state.status = host.querySelector('.lc-status');
    const readHeight = () => {
      try {
        const v = parseFloat(getComputedStyle(host).getPropertyValue('--lc-h'));
        state.lcH = Number.isFinite(v) && v > 0 ? v : 320;
      } catch (_) { state.lcH = 320; }
    };
    readHeight();
    const tabs = Array.from(host.querySelectorAll('.lc-range'));
    const select = (k, focus) => {
      state.range = k;
      store.set(`quanta_chart_range_${opts.context}`, k);
      tabs.forEach((t) => { const on = t.dataset.range === k; t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1; if (on && focus) t.focus(); });
      state.view = null;
      load();
    };
    host.querySelector('.lc-ranges').addEventListener('click', (ev) => { const b = ev.target.closest('.lc-range'); if (b) select(b.dataset.range, false); });
    host.querySelector('.lc-ranges').addEventListener('keydown', (ev) => {
      const i = tabs.findIndex((t) => t.dataset.range === state.range);
      const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[ev.key];
      if (next == null) return;
      ev.preventDefault();
      select(tabs[(next + tabs.length) % tabs.length].dataset.range, true);
    });
    const commit = () => { savePrefs(opts.context, state.prefs); if (state.data) render(state); };
    state.tools.addEventListener('click', (ev) => {
      const typeBtn = ev.target.closest('[data-lc-type]');
      if (typeBtn) { state.prefs.type = typeBtn.dataset.lcType; commit(); state.tools.querySelector(`[data-lc-type="${state.prefs.type}"]`).focus(); return; }
      const cmp = ev.target.closest('[data-lc-compare]');
      if (cmp) {
        state.prefs.compare = !state.prefs.compare;
        savePrefs(opts.context, state.prefs);
        if (state.prefs.compare && !state.bench) loadBench().then(() => { if (state.data) render(state); });
        else if (state.data) render(state);
        const again = state.tools.querySelector('[data-lc-compare]');
        if (again) again.focus();
        return;
      }
      const z = ev.target.closest('[data-lc-zoom]');
      if (z && state.data) {
        const kind = z.dataset.lcZoom;
        if (kind === 'reset') state.view = null;
        if (kind === 'reset') render(state); else zoom(state, kind === 'in' ? 0.7 : 1.4);
        const again = state.tools.querySelector(`[data-lc-zoom="${kind}"]`);
        if (again && !again.disabled) again.focus(); else { const svg = state.frame.querySelector('.lc-svg'); if (svg) svg.focus(); }
      }
    });
    state.tools.addEventListener('change', (ev) => {
      const box = ev.target.closest('[data-lc-study]');
      if (!box) return;
      if (box.checked) state.prefs.studies.add(box.dataset.lcStudy); else state.prefs.studies.delete(box.dataset.lcStudy);
      commit();
      const again = state.tools.querySelector(`[data-lc-study="${box.dataset.lcStudy}"]`);
      if (again) again.focus();
    });
    // A click anywhere else closes the indicators menu.
    const onOutside = (ev) => {
      const d = state.tools.querySelector('.lc-studies');
      if (d && d.open && !d.contains(ev.target)) d.open = false;
    };
    if (typeof document !== 'undefined') document.addEventListener('pointerdown', onOutside);
    state.tools.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Escape') return;
      const d = state.tools.querySelector('.lc-studies');
      if (d && d.open) { d.open = false; d.querySelector('summary').focus(); }
    });

    const visible = () => !document.hidden && host.offsetParent !== null;

    function schedule() {
      clearTimeout(state.timer);
      clearTimeout(state.liveTimer);
      const d = state.data;
      if (!d || !isLive(state)) return;
      const every = REFRESH_MS[d.range];
      if (every) {
        state.timer = setTimeout(() => {
          if (!host.isConnected) return;
          // Another page is open (the view is display:none) or the tab is hidden:
          // wait, rather than spend provider calls on a chart nobody sees.
          if (!visible()) { schedule(); return; }
          load(true);
        }, every);
        return;
      }
      state.liveTimer = setTimeout(async () => {
        if (!host.isConnected) return;
        if (!visible()) { schedule(); return; }
        try {
          const res = await fetch(`/api/chart?symbol=${encodeURIComponent(state.symbol)}&range=1d`);
          const quote = await res.json();
          if (quote && quote.available && state.data && state.data.range === d.range && state.data.symbol === quote.symbol) {
            const before = state.data.points[state.data.points.length - 1][1];
            if (patchLastBar(state.data, quote)) {
              const after = state.data.points[state.data.points.length - 1][1];
              state.flash = after > before ? 'up' : after < before ? 'down' : '';
              state.livePatched = true;
              state.lastUpdate = Date.now();
              const keepEnd = state.view && state.view.i1 >= state.data.points.length - 2;
              state.series = computeSeries(state.data, zoned(state.data.timezone));
              if (state.bench) state.benchAligned = alignBenchmark(state.data.points, state.bench);
              if (keepEnd && state.view) { const span = state.view.i1 - state.view.i0; state.view = { i0: state.data.points.length - 1 - span, i1: state.data.points.length - 1 }; }
              render(state);
            } else {
              state.data.market_open = Boolean(quote.market_open);
              state.lastUpdate = Date.now();
              renderHead(state, { last: state.model.closesRaw[state.model.closesRaw.length - 1], base: state.model.base, up: true });
            }
          }
        } catch (_) { /* keep the bars we have; try again next minute */ }
        schedule();
      }, LIVE_PATCH_MS);
    }

    async function loadBench() {
      const sym = benchmarkFor(state.symbol);
      if (!sym || !state.data) return;
      const want = state.range;
      try {
        const res = await fetch(`/api/chart?symbol=${encodeURIComponent(sym)}&range=${encodeURIComponent(want)}`);
        const b = await res.json();
        if (b && b.available && state.range === want) {
          state.bench = b;
          state.benchAligned = alignBenchmark(state.data.points, b);
        }
      } catch (_) { state.bench = null; }
    }

    async function load(quiet) {
      if (!state.symbol) return;
      const my = ++state.req;
      if (!quiet) state.frame.setAttribute('aria-busy', 'true');
      state.frame.classList.toggle('is-loading', !quiet);
      let data = null;
      let reason = '';
      try {
        const res = await fetch(`/api/chart?symbol=${encodeURIComponent(state.symbol)}&range=${encodeURIComponent(state.range)}`);
        data = await res.json();
        if (!data || !data.available) reason = (data && (data.reason || data.error)) || 'No price bars returned.';
      } catch (_) {
        reason = 'The QUANTA server could not be reached for price bars.';
      }
      if (my !== state.req || !host.isConnected) return;
      state.frame.classList.remove('is-loading');
      state.frame.setAttribute('aria-busy', 'false');
      if (reason) {
        if (quiet && state.data) { schedule(); return; }
        state.frame.innerHTML = `<div class="lc-error" role="status"><span>${esc(reason)}</span><button type="button" class="q-btn q-btn--sm" data-lc-retry>Retry</button></div>`;
        state.quote.innerHTML = '';
        state.tools.innerHTML = '';
        state.frame.querySelector('[data-lc-retry]').addEventListener('click', () => load());
        return;
      }
      const prevLast = state.data && state.data.symbol === data.symbol && state.data.range === data.range
        ? state.data.points[state.data.points.length - 1][1] : null;
      if (quiet && state.view && state.data && state.data.range === data.range) {
        // A refresh keeps a zoomed window, sliding it if it ended at the latest bar.
        const N = data.points.length;
        const span = state.view.i1 - state.view.i0;
        const atEnd = state.view.i1 >= state.data.points.length - 1;
        state.view = atEnd ? { i0: Math.max(0, N - 1 - span), i1: N - 1 } : { i0: Math.min(state.view.i0, N - 1 - Math.min(span, N - 1)), i1: Math.min(state.view.i1, N - 1) };
      } else if (!quiet) {
        state.view = null;
      }
      const newLast = data.points[data.points.length - 1][1];
      state.flash = prevLast === null ? '' : newLast > prevLast ? 'up' : newLast < prevLast ? 'down' : '';
      state.data = data;
      state.livePatched = false;
      state.lastUpdate = Date.now();
      state.series = computeSeries(data, zoned(data.timezone));
      if (!state.bench || state.bench.range !== data.range || !quiet) {
        state.bench = null; state.benchAligned = null;
        if (state.prefs.compare && benchmarkFor(state.symbol)) await loadBench();
        if (my !== state.req || !host.isConnected) return;
      } else {
        state.benchAligned = alignBenchmark(data.points, state.bench);
      }
      readHeight();
      render(state);
      if (typeof opts.onData === 'function') opts.onData(data);
      schedule();
    }

    state.setSymbol = (sym) => { state.symbol = String(sym || '').toUpperCase(); state.data = null; state.bench = null; state.view = null; load(); };
    state.setLevels = (levels) => { opts.levels = levels || []; if (state.data) render(state); };
    state.setEvents = (events) => { opts.events = events || []; if (state.data) render(state); };
    // Label counts follow the chart's width: redraw after a real width change.
    let drawnW = 0;
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
      const w = host.clientWidth;
      if (state.data && Math.abs(w - drawnW) > 60) { drawnW = w; readHeight(); render(state); }
      else if (!drawnW) drawnW = w;
    }) : null;
    if (ro) ro.observe(host);
    // A tab coming back into view refreshes at once rather than waiting a cycle.
    const onVisible = () => { if (!document.hidden && state.data && isLive(state) && host.isConnected) load(true); };
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisible);
    state.destroy = () => {
      clearTimeout(state.timer); clearTimeout(state.liveTimer); state.req += 1;
      if (ro) ro.disconnect();
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisible);
        document.removeEventListener('pointerdown', onOutside);
      }
      host._liveChart = null;
    };
    host._liveChart = state;
    load();
    return state;
  }

  const indicators = { sma, ema, bollinger, rsi, macd, vwap, volumeOutliers };
  const api = { mount, niceStep, yModel, xModel, zoned, RANGES, STUDIES, isLevel, isInverse, benchmarkFor, indicators, computeSeries, alignBenchmark, patchLastBar };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.QuantaLiveChart = api;
}(typeof window !== 'undefined' ? window : globalThis));
