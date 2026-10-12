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
 * Colour roles: the line is green when the last price is at or above the
 * reference (previous close on 1D, the first bar otherwise) and red below it;
 * gold marks a price target, red a stop, gray the previous close and volume.
 * The 1D chart refreshes every minute while the session (or its extended
 * hours) is open and the page is visible.
 */
(function (root) {
  'use strict';

  const W = 1000;
  const H = 300;
  const VOL_SHARE = 0.16;
  const RANGES = [['1d', '1D'], ['5d', '5D'], ['1mo', '1M'], ['6mo', '6M'], ['ytd', 'YTD'], ['1y', '1Y'], ['5y', '5Y'], ['max', 'Max']];
  const RANGE_WORDS = { '1d': 'today', '5d': 'past 5 days', '1mo': 'past month', '6mo': 'past 6 months', ytd: 'year to date', '1y': 'past year', '5y': 'past 5 years', max: 'all time' };
  const REFRESH_MS = 60000;

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const CURRENCY = { USD: '$', INR: '₹', EUR: '€', GBP: '£', JPY: '¥' };
  const symOf = (cur) => CURRENCY[String(cur || '').toUpperCase()] || '';
  // An index (^VIX, ^TNX, ^GSPC) is a level, not a price: no currency symbol.
  const isLevel = (data) => String(data.symbol || '').startsWith('^') || /^INDEX$/i.test(String(data.instrument_type || ''));
  // Volatility indices are read inversely: a falling VIX supports risk, so it
  // takes the supportive colour and a rising one the warning colour.
  const isInverse = (data) => /^\^(VIX|VXN|VVIX|RVX|INDIAVIX)/i.test(String(data.symbol || ''));
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
  function priceDigits(v) { return Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 2 : 4; }
  function fmtSigned(v, d = 2) { return finite(v) ? `${v >= 0 ? '+' : '-'}${fmtNum(Math.abs(v), d)}` : '--'; }
  function fmtVol(v) {
    if (!finite(v)) return '--';
    const a = Math.abs(v);
    return a >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : a >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(Math.round(v));
  }

  // Parts of a timestamp in the exchange's time zone.
  function zoned(tz) {
    const opts = (o) => { try { return new Intl.DateTimeFormat('en-US', { timeZone: tz || undefined, ...o }); } catch (_) { return new Intl.DateTimeFormat('en-US', o); } };
    const parts = opts({ year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', hourCycle: 'h23', minute: 'numeric', weekday: 'short' });
    const fTime = opts({ hour: 'numeric', minute: '2-digit' });
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
      time: (t) => `${fTime.format(new Date(t))} ${zoneName(t)}`.trim(),
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
      const every = spanH > 12 ? 2 : 1;
      // Step by quarter hours so a zone offset by 30 or 45 minutes still lands on
      // its own whole hours.
      const q = hourMs / 4;
      for (let t = Math.ceil(lo / q) * q; t <= hi; t += q) {
        const p = z.get(t);
        if (p.min === 0 && p.h % every === 0) ticks.push({ x: x(t), label: z.hour(t) });
      }
      return { x: (i) => x(Math.min(pts[i][0], hi)), xt: x, ticks, ext: [sess.pre, sess.post].filter(Boolean).map(([a, b]) => [x(a), x(Math.min(b, hi))]).filter(([a, b]) => b > a) };
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
    const stride = Math.max(1, Math.ceil(starts.length / 7));
    const ticks = starts.filter((_, j) => j % stride === 0).map((i) => ({ x: x(i), label: label(pts[i][0]) }));
    return { x, xt: null, ticks, ext: [] };
  }

  function yModel(values) {
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    if (hi === lo) { hi += Math.abs(hi) * 0.01 || 1; lo -= Math.abs(lo) * 0.01 || 1; }
    const step = niceStep(hi - lo, 5);
    const pad = (hi - lo) * 0.04;
    const y0 = Math.floor((lo - pad) / step) * step;
    const y1 = Math.ceil((hi + pad) / step) * step;
    const ticks = [];
    for (let v = y0; v <= y1 + step / 2; v += step) ticks.push(+v.toFixed(8));
    return { y0, y1, step, ticks, digits: decimalsFor(step) };
  }

  function render(state) {
    const { data, opts, host } = state;
    const z = zoned(data.timezone);
    const pts = data.points;
    const n = pts.length;
    const closes = pts.map((p) => p[1]);
    const is1d = data.range === '1d';
    const base = is1d && finite(data.previous_close) ? data.previous_close : closes[0];
    const last = closes[n - 1];
    const up = last >= base;
    const good = isInverse(data) ? !up : up;
    const cur = isLevel(data) ? '' : symOf(data.currency);

    // Reference lines that fit near the data are drawn; the rest are listed.
    const span = Math.max(...closes) - Math.min(...closes);
    const near = (v) => finite(v) && v >= Math.min(...closes) - span * 0.35 && v <= Math.max(...closes) + span * 0.35;
    const levels = (opts.levels || []).filter((l) => finite(l.value));
    const shown = levels.filter((l) => near(l.value));
    const off = levels.filter((l) => !near(l.value));
    const yVals = closes.concat(is1d && finite(data.previous_close) ? [data.previous_close] : [], shown.map((l) => l.value));
    const ym = yModel(yVals);
    const vols = pts.map((p) => p[2]);
    const hasVol = vols.some((v) => finite(v) && v > 0);
    const priceH = hasVol ? H * (1 - VOL_SHARE) - 6 : H;
    const y = (v) => ((ym.y1 - v) / (ym.y1 - ym.y0)) * priceH;
    const pct = (v) => `${((v / H) * 100).toFixed(3)}%`;
    const xm = xModel(data, z);
    // As many time labels as the plot's real width holds, about one per 72px.
    const plotPx = Math.max(160, (state.host.clientWidth || 600) - 70);
    const room = Math.max(2, Math.floor(plotPx / 72));
    if (xm.ticks.length > room) {
      const k = Math.ceil(xm.ticks.length / room);
      xm.ticks = xm.ticks.filter((_, i) => i % k === 0);
    }
    const xs = pts.map((_, i) => xm.x(i));

    const line = xs.map((x, i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y(closes[i]).toFixed(1)}`).join('');
    const area = `${line}L${xs[n - 1].toFixed(1)},${priceH.toFixed(1)}L${xs[0].toFixed(1)},${priceH.toFixed(1)}Z`;
    const grid = ym.ticks.map((v) => `<line class="lc-grid" x1="0" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/>`).join('');
    const vgrid = xm.ticks.map((t) => `<line class="lc-vgrid" x1="${t.x.toFixed(1)}" x2="${t.x.toFixed(1)}" y1="0" y2="${H}"/>`).join('');
    const ext = xm.ext.map(([a, b]) => `<rect class="lc-ext" x="${a.toFixed(1)}" y="0" width="${Math.max(0, b - a).toFixed(1)}" height="${H}"/>`).join('');
    let volBars = '';
    if (hasVol) {
      const vmax = Math.max(...vols.filter(finite));
      const bw = Math.max(1, Math.min(14, (W / Math.max(n, 1)) * 0.7));
      volBars = pts.map((p, i) => {
        if (!finite(p[2]) || p[2] <= 0) return '';
        const h = (p[2] / vmax) * (H * VOL_SHARE);
        return `<rect class="lc-vol" x="${(xs[i] - bw / 2).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"/>`;
      }).join('');
    }
    const refLine = is1d && finite(data.previous_close)
      ? `<line class="lc-ref" x1="0" x2="${W}" y1="${y(data.previous_close).toFixed(1)}" y2="${y(data.previous_close).toFixed(1)}"/>` : '';
    const lvLines = shown.map((l) => `<line class="lc-level lc-level--${esc(l.tone || 'neutral')}" x1="0" x2="${W}" y1="${y(l.value).toFixed(1)}" y2="${y(l.value).toFixed(1)}"/>`).join('');
    const gid = `lc-fill-${state.uid}`;

    // A tick label is dropped where a price tag sits on it (the last price and
    // the previous close): about 16px of the real plot height either side.
    const framePx = (state.frame && state.frame.clientHeight) || 320;
    const clear = (16 / Math.max(120, framePx - 22)) * H;
    const tags = [last].concat(is1d && finite(data.previous_close) ? [data.previous_close] : []).map((v) => y(v));
    const yLabels = ym.ticks.filter((v) => tags.every((t) => Math.abs(y(v) - t) >= clear))
      .map((v) => `<span class="lc-ytick" style="top:${pct(y(v))}">${fmtNum(v, ym.digits)}</span>`).join('');
    const badges = [
      is1d && finite(data.previous_close) ? `<span class="lc-badge lc-badge--ref" style="top:${pct(y(data.previous_close))}" title="Previous close">${fmtNum(data.previous_close, priceDigits(data.previous_close))}</span>` : '',
      `<span class="lc-badge lc-badge--last ${good ? 'is-up' : 'is-down'}" style="top:${pct(y(last))}" title="Last price">${fmtNum(last, priceDigits(last))}</span>`,
    ].join('');
    const lvLabels = shown.map((l) => `<span class="lc-lvlabel lc-lvlabel--${esc(l.tone || 'neutral')}" style="top:${pct(y(l.value))}">${esc(l.label)} ${esc(cur)}${fmtNum(l.value, priceDigits(l.value))}</span>`).join('')
      + (is1d && finite(data.previous_close) ? `<span class="lc-lvlabel lc-lvlabel--ref" style="top:${pct(y(data.previous_close))}">Previous close</span>` : '');
    const xLabels = xm.ticks.map((t) => `<span class="lc-xtick${t.x < W * 0.04 ? ' is-edge' : t.x > W * 0.96 ? ' is-edge-end' : ''}" style="left:${((t.x / W) * 100).toFixed(3)}%">${esc(t.label)}</span>`).join('');

    const first = pts[0][0];
    const lastT = pts[n - 1][0];
    const hi = Math.max(...closes);
    const lo = Math.min(...closes);
    const summary = `${data.symbol} ${RANGE_WORDS[data.range] || data.range}: ${fmtNum(closes[0], priceDigits(closes[0]))} to ${fmtNum(last, priceDigits(last))} (${fmtSigned(((last - base) / base) * 100)}% vs ${is1d && finite(data.previous_close) ? 'previous close' : 'first bar'}), high ${fmtNum(hi, priceDigits(hi))}, low ${fmtNum(lo, priceDigits(lo))}. Use the arrow keys to read each bar.`;

    state.model = { z, xs, y, closes, base, cur, H, hasVol };
    state.frame.innerHTML = `
      <div class="lc-plot">
        <svg class="lc-svg ${good ? 'is-up' : 'is-down'}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" tabindex="0" role="img" aria-label="${esc(summary)}" focusable="true">
          <defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" class="lc-stop-a"/><stop offset="1" class="lc-stop-b"/></linearGradient></defs>
          ${ext}${vgrid}${grid}${volBars}
          <path class="lc-area" d="${area}" fill="url(#${gid})"/>
          ${refLine}${lvLines}
          <path class="lc-line" d="${line}"/>
          <line class="lc-cross" x1="0" x2="0" y1="0" y2="${H}" visibility="hidden"/>
        </svg>
        <div class="lc-overlay" aria-hidden="true">${lvLabels}<span class="lc-dot" hidden></span><div class="lc-tip" hidden></div></div>
      </div>
      <div class="lc-yaxis" aria-hidden="true">${yLabels}${badges}<span class="lc-badge lc-badge--hover" hidden></span></div>
      <div class="lc-xaxis" aria-hidden="true">${xLabels}</div>
      <p class="sr-only lc-live" aria-live="polite"></p>`;
    if (off.length) {
      state.frame.insertAdjacentHTML('beforeend', `<p class="lc-offscale">${off.map((l) => `<span class="lc-lvlabel--${esc(l.tone || 'neutral')}">${esc(l.label)} ${esc(cur)}${fmtNum(l.value, priceDigits(l.value))}</span> ${l.value > last ? 'above' : 'below'} this range`).join('; ')}</p>`);
    }
    bindCrosshair(state);
    renderHead(state, { first, lastT, last, base, up: good });
  }

  function renderHead(state, { last, base, up }) {
    const { data, opts } = state;
    const z = state.model.z;
    const cur = state.model.cur;
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
      quote = `
        <div class="lc-q"><span class="lc-px">${esc(cur)}${fmtNum(is1d ? regular : last, priceDigits(regular))}</span><span class="lc-chg ${tone}">${fmtSigned(chg)} (${fmtSigned(chgPct)}%)</span><span class="lc-asof">${esc(asOf)}</span></div>${extd}`;
    } else {
      const chgPct = base ? ((last - base) / base) * 100 : null;
      const when = is1d && data.market_open ? `Live, ${z.time(data.retrieved_at)}` : `Last bar ${is1d ? z.time(data.points[data.points.length - 1][0]) : z.full(data.points[data.points.length - 1][0])}`;
      quote = `<div class="lc-q lc-q--compact"><span class="lc-px lc-px--sm">${esc(cur)}${fmtNum(last, priceDigits(last))}</span><span class="lc-chg ${up ? 'bull' : 'bear'}">${fmtSigned(chgPct)}% ${esc(is1d && finite(data.previous_close) ? 'vs previous close' : RANGE_WORDS[data.range] || '')}</span><span class="lc-asof">${esc(when)}</span></div>`;
    }
    state.quote.innerHTML = quote;
    const liveNow = is1d && (data.market_open || (data.extended && Date.now() - data.extended.time < 20 * 60000));
    state.status.innerHTML = `${liveNow ? '<span class="lc-livedot" aria-hidden="true"></span>Refreshes every minute' : ''}${data.stale ? '<span class="lc-stale">Provider busy: showing the last bars fetched</span>' : ''}`;
    state.status.hidden = !liveNow && !data.stale;
  }

  function bindCrosshair(state) {
    const svg = state.frame.querySelector('.lc-svg');
    const cross = svg.querySelector('.lc-cross');
    const dot = state.frame.querySelector('.lc-dot');
    const tip = state.frame.querySelector('.lc-tip');
    const hoverBadge = state.frame.querySelector('.lc-badge--hover');
    const live = state.frame.querySelector('.lc-live');
    const { xs, y, closes, base, cur, z } = state.model;
    const pts = state.data.points;
    const is1d = state.data.range === '1d';
    const intraday = ['1d', '5d', '1mo'].includes(state.data.range);
    let idx = pts.length - 1;
    const show = (i, announce) => {
      idx = Math.max(0, Math.min(pts.length - 1, i));
      const xp = (xs[idx] / W) * 100;
      const yp = (y(closes[idx]) / H) * 100;
      cross.setAttribute('x1', xs[idx]); cross.setAttribute('x2', xs[idx]); cross.setAttribute('visibility', 'visible');
      dot.hidden = false; dot.style.left = `${xp}%`; dot.style.top = `${yp}%`;
      const chg = closes[idx] - base;
      const when = intraday ? (is1d ? z.time(pts[idx][0]) : `${z.day(pts[idx][0])}, ${z.time(pts[idx][0])}`) : z.full(pts[idx][0]);
      tip.innerHTML = `<span class="lc-tip-t">${esc(when)}</span><b>${esc(cur)}${fmtNum(closes[idx], priceDigits(closes[idx]))}</b><span class="${chg >= 0 ? 'bull' : 'bear'}">${fmtSigned(chg)} (${fmtSigned((chg / base) * 100)}%)</span>${finite(pts[idx][2]) && pts[idx][2] > 0 ? `<span class="lc-tip-v">Vol ${fmtVol(pts[idx][2])}</span>` : ''}`;
      tip.hidden = false;
      tip.classList.toggle('is-left', xp > 62);
      tip.style.left = `${xp}%`;
      hoverBadge.hidden = false; hoverBadge.style.top = `${yp}%`; hoverBadge.textContent = fmtNum(closes[idx], priceDigits(closes[idx]));
      if (announce && live) live.textContent = `${when}: ${cur}${fmtNum(closes[idx], priceDigits(closes[idx]))}`;
    };
    const hide = () => { cross.setAttribute('visibility', 'hidden'); dot.hidden = true; tip.hidden = true; hoverBadge.hidden = true; };
    const nearest = (clientX) => {
      const r = svg.getBoundingClientRect();
      const vx = ((clientX - r.left) / r.width) * W;
      let lo = 0; let hi = xs.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < vx) lo = mid; else hi = mid; }
      return Math.abs(xs[lo] - vx) <= Math.abs(xs[hi] - vx) ? lo : hi;
    };
    svg.addEventListener('pointermove', (ev) => show(nearest(ev.clientX), false));
    svg.addEventListener('pointerdown', (ev) => show(nearest(ev.clientX), false));
    svg.addEventListener('pointerleave', () => { if (document.activeElement !== svg) hide(); });
    svg.addEventListener('focus', () => show(idx, true));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (ev) => {
      const step = ev.shiftKey ? 10 : 1;
      const map = { ArrowLeft: idx - step, ArrowRight: idx + step, Home: 0, End: pts.length - 1 };
      if (!(ev.key in map)) return;
      ev.preventDefault();
      show(map[ev.key], true);
    });
  }

  function mount(host, options) {
    if (!host) return null;
    if (host._liveChart) host._liveChart.destroy();
    const opts = { context: 'default', showQuote: true, levels: [], ...options };
    const saved = store.get(`quanta_chart_range_${opts.context}`);
    const state = {
      host, opts, uid: Math.random().toString(36).slice(2, 8),
      range: RANGES.some(([k]) => k === saved) ? saved : (opts.range || '1d'),
      symbol: String(opts.symbol || '').toUpperCase(), data: null, timer: 0, req: 0,
    };
    host.classList.add('lc');
    host.innerHTML = `
      <div class="lc-head">
        <div class="lc-quote"></div>
        <div class="lc-ranges" role="tablist" aria-label="Chart range">${RANGES.map(([k, l]) => `<button type="button" class="lc-range" role="tab" data-range="${k}" aria-selected="${k === state.range}" tabindex="${k === state.range ? 0 : -1}">${l}</button>`).join('')}</div>
      </div>
      <div class="lc-frame" aria-busy="true"><div class="lc-loading">Loading price bars</div></div>
      <p class="lc-status" hidden></p>`;
    state.quote = host.querySelector('.lc-quote');
    state.frame = host.querySelector('.lc-frame');
    state.status = host.querySelector('.lc-status');
    const tabs = Array.from(host.querySelectorAll('.lc-range'));
    const select = (k, focus) => {
      state.range = k;
      store.set(`quanta_chart_range_${opts.context}`, k);
      tabs.forEach((t) => { const on = t.dataset.range === k; t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1; if (on && focus) t.focus(); });
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

    function schedule() {
      clearTimeout(state.timer);
      const d = state.data;
      if (!d || d.range !== '1d') return;
      const sess = d.session || {};
      const end = (sess.post && sess.post[1]) || (sess.regular && sess.regular[1]);
      const start = (sess.pre && sess.pre[0]) || (sess.regular && sess.regular[0]);
      const now = Date.now();
      if (!(start && end && now >= start && now < end) && !d.market_open) return;
      state.timer = setTimeout(() => {
        if (!host.isConnected) return;
        // Another page is open (the view is display:none) or the tab is hidden:
        // wait, rather than spend provider calls on a chart nobody sees.
        if (document.hidden || !host.offsetParent) { schedule(); return; }
        load(true);
      }, REFRESH_MS);
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
        state.frame.querySelector('[data-lc-retry]').addEventListener('click', () => load());
        return;
      }
      state.data = data;
      render(state);
      if (typeof opts.onData === 'function') opts.onData(data);
      schedule();
    }

    state.setSymbol = (sym) => { state.symbol = String(sym || '').toUpperCase(); state.data = null; load(); };
    state.setLevels = (levels) => { opts.levels = levels || []; if (state.data) render(state); };
    // Label counts follow the chart's width: redraw after a real width change.
    let drawnW = 0;
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
      const w = host.clientWidth;
      if (state.data && Math.abs(w - drawnW) > 60) { drawnW = w; render(state); }
      else if (!drawnW) drawnW = w;
    }) : null;
    if (ro) ro.observe(host);
    state.destroy = () => { clearTimeout(state.timer); state.req += 1; if (ro) ro.disconnect(); host._liveChart = null; };
    host._liveChart = state;
    load();
    return state;
  }

  const api = { mount, niceStep, yModel, xModel, zoned, RANGES, isLevel, isInverse };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.QuantaLiveChart = api;
}(typeof window !== 'undefined' ? window : globalThis));
