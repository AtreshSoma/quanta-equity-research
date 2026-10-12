/* My Portfolio chart primitives — dependency-free SVG + HTML.
 *
 * Shapes are SVG stretched over a fixed viewBox (strokes keep their width with
 * vector-effect); every piece of TEXT is HTML positioned by percentage, so type
 * never scales with the chart. One shared crosshair drives the tooltip, the dots
 * on each series, the drawdown panel under the main chart and a polite live
 * region, so pointer, touch and keyboard readers get the same reading.
 *
 * Nothing here decides what a number means: callers pass finished values and
 * formatters. Missing points (null) break the line; they are never bridged.
 *
 * Colour roles (CSS): blue = the portfolio, gray = the benchmark, green/red = gain
 * and loss (and the band where the portfolio leads or lags), amber = a limit,
 * red in the risk gauge's last zone = severe risk. Categorical slots only in the
 * allocation views.
 */
(function (root) {
  'use strict';

  const W = 1000;
  const H = 280;
  const DD_H = 70;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const f1 = (v) => v.toFixed(1);

  function niceStep(span, target) {
    const raw = span / Math.max(1, target);
    const pow = 10 ** Math.floor(Math.log10(raw));
    const f = raw / pow;
    return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * pow;
  }

  // Auto-fit: the data's own range plus a small pad, snapped to round ticks. Zero is
  // included only when asked (a return chart), so the lines use the height they have.
  function extent(series, includeZero) {
    let lo = Infinity;
    let hi = -Infinity;
    series.forEach((s) => s.values.forEach((v) => { if (finite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
    if (!Number.isFinite(lo)) return null;
    if (includeZero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.06;
    const step = niceStep(hi - lo + 2 * pad, 4);
    return { lo: Math.floor((lo - pad) / step) * step, hi: Math.ceil((hi + pad) / step) * step, step };
  }

  const xOf = (i, n) => (n <= 1 ? 0 : (i / (n - 1)) * W);
  const pathOf = (values, n, y) => {
    let d = '';
    let pen = false;
    for (let i = 0; i < n; i += 1) {
      const v = values[i];
      if (!finite(v)) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${f1(xOf(i, n))} ${f1(y(v))}`;
      pen = true;
    }
    return d;
  };

  // The gap between two series as closed runs: a run ends where the lines cross (at the
  // crossing point, so the fill never overshoots) or where either series is missing.
  function bandRuns(a, b, n) {
    const runs = [];
    let cur = null;
    const close = () => { if (cur && cur.top.length > 1) runs.push(cur); cur = null; };
    for (let i = 0; i < n; i += 1) {
      if (!finite(a[i]) || !finite(b[i])) { close(); continue; }
      const d = a[i] - b[i];
      const s = d > 0 ? 1 : d < 0 ? -1 : 0;
      const x = xOf(i, n);
      if (!cur) { cur = { s, top: [[x, a[i]]], bot: [[x, b[i]]] }; continue; }
      if (s && cur.s && s !== cur.s) {
        const d0 = a[i - 1] - b[i - 1];
        const t = d0 / (d0 - d);
        const xm = xOf(i - 1, n) + (x - xOf(i - 1, n)) * t;
        const vm = a[i - 1] + (a[i] - a[i - 1]) * t;
        cur.top.push([xm, vm]); cur.bot.push([xm, vm]);
        close();
        cur = { s, top: [[xm, vm]], bot: [[xm, vm]] };
      }
      if (!cur.s) cur.s = s;
      cur.top.push([x, a[i]]); cur.bot.push([x, b[i]]);
    }
    close();
    return runs;
  }
  function bandHtml(a, b, n, y) {
    const paths = { 1: '', '-1': '' };
    bandRuns(a, b, n).forEach((r) => {
      if (!r.s) return;
      const pts = [...r.top, ...r.bot.slice().reverse()];
      paths[r.s] += `M${pts.map(([x, v]) => `${f1(x)} ${f1(y(v))}`).join('L')}Z`;
    });
    return `${paths[1] ? `<path class="mpc-band is-lead" d="${paths[1]}"/>` : ''}${paths[-1] ? `<path class="mpc-band is-lag" d="${paths[-1]}"/>` : ''}`;
  }

  function xLabels(dates, fmtTick, picks) {
    const n = dates.length;
    if (n < 2) return '';
    let idx = picks;
    if (!idx) {
      const want = n > 200 ? 6 : n > 90 ? 5 : 4;
      idx = [];
      for (let k = 0; k < want; k += 1) idx.push(Math.round((k / (want - 1)) * (n - 1)));
    }
    return [...new Set(idx)].map((i, k, arr) => {
      const edge = k === 0 && i === 0 ? ' is-first' : k === arr.length - 1 && i === n - 1 ? ' is-last' : '';
      return `<span class="mpc-xl${edge}" style="left:${(i / (n - 1) * 100).toFixed(2)}%">${esc(fmtTick(dates[i]))}</span>`;
    }).join('');
  }

  // End-of-line labels sit in a right gutter at each series' last value; two labels
  // closer than `gap` percent of the height are pushed apart around their midpoint.
  function endLabels(spec, ext) {
    const gap = 9;
    const items = spec.series.map((s) => {
      let i = s.values.length - 1;
      while (i >= 0 && !finite(s.values[i])) i -= 1;
      if (i < 0) return null;
      const v = s.values[i];
      return { s, v, top: ((ext.hi - v) / (ext.hi - ext.lo)) * 100 };
    }).filter(Boolean).sort((a, b) => a.top - b.top);
    for (let k = 1; k < items.length; k += 1) {
      const d = items[k].top - items[k - 1].top;
      if (d < gap) { const mid = (items[k].top + items[k - 1].top) / 2; items[k - 1].top = mid - gap / 2; items[k].top = mid + gap / 2; }
    }
    return `<div class="mpc-end" aria-hidden="true">${items.map((x) => `<span class="mpc-endl mpc-endl--${esc(x.s.cls || x.s.key)}" style="top:${Math.max(0, Math.min(100, x.top)).toFixed(2)}%">${esc(spec.fmt.end ? spec.fmt.end(x.v, x.s) : spec.fmt.value(x.v))}</span>`).join('')}</div>`;
  }

  /* spec: { id, dates[], series:[{key,label,values[],cls,area}], fmt:{value,axis,tick,date,end},
   *         zero (include 0 and draw it dashed; default true), baseline (a dotted reference value),
   *         band:true (shade the gap between series 0 and 1: lead / lag),
   *         endLabels:true, xticks:[indices], shade:[{from,to,cls,label}] (session bands),
   *         markers:[{date,label,kind}], showMarkers, markersOnLine (dots on series 0),
   *         drawdown:{label,values[],bench[]}|null, tip(i)->html, speak(i), ariaLabel, unitLabel } */
  function render(spec) {
    const n = spec.dates.length;
    const ext = extent(spec.series, spec.zero !== false);
    if (!n || !ext) return '<p class="mpc-empty">No data to draw.</p>';
    const y = (v) => H - ((v - ext.lo) / (ext.hi - ext.lo)) * H;
    const ticks = [];
    for (let t = ext.lo; t <= ext.hi + 1e-9; t += ext.step) ticks.push(Math.round(t / ext.step) * ext.step);
    const grid = ticks.map((t) => `<line class="mpc-grid${spec.zero !== false && Math.abs(t) < 1e-9 ? ' is-zero' : ''}" x1="0" x2="${W}" y1="${f1(y(t))}" y2="${f1(y(t))}"/>`).join('')
      + (finite(spec.baseline) ? `<line class="mpc-base" x1="0" x2="${W}" y1="${f1(y(spec.baseline))}" y2="${f1(y(spec.baseline))}" vector-effect="non-scaling-stroke"/>` : '');
    const ylab = ticks.map((t) => `<span class="mpc-yl" style="top:${(y(t) / H * 100).toFixed(2)}%">${esc(spec.fmt.axis ? spec.fmt.axis(t) : spec.fmt.value(t))}</span>`).join('');
    const gid = `mpcg-${String(spec.id).replace(/[^a-z0-9-]/gi, '')}`;
    const band = spec.band && spec.series.length > 1 ? bandHtml(spec.series[0].values, spec.series[1].values, n, y) : '';
    const lines = spec.series.map((s) => {
      const d = pathOf(s.values, n, y);
      if (!d) return '';
      // A soft vertical fade under the lead series, to the bottom of the plot (only where unbroken).
      const unbroken = s.area && s.values.every(finite);
      const area = unbroken ? `<path class="mpc-wash" d="${d} L${W} ${H} L0 ${H} Z" fill="url(#${gid})"/>` : '';
      return `${area}<path class="mpc-line mpc-line--${esc(s.cls || s.key)}" d="${d}" vector-effect="non-scaling-stroke"/>`;
    }).join('');
    const defs = `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity="0.20"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>`;
    const dots = spec.series.map((s) => `<i class="mpc-dot mpc-dot--${esc(s.cls || s.key)}" data-series="${esc(s.key)}" hidden></i>`).join('');
    let dd = '';
    if (spec.drawdown) {
      const vals = spec.drawdown.values;
      const bench = spec.drawdown.bench || [];
      const lo = Math.min(-0.001, ...vals.filter(finite), ...bench.filter(finite));
      const dy = (v) => (v / lo) * DD_H;               // 0 at top, deepest at bottom
      const area = `M0 0 ${vals.map((v, i) => `L${f1(xOf(i, n))} ${f1(dy(v))}`).join(' ')} L${W} 0 Z`;
      dd = `<div class="mpc-ddwrap"><span class="mpc-ddl">${esc(spec.drawdown.label || 'Drawdown from peak')}</span>
        <div class="mpc-dd"><svg viewBox="0 0 ${W} ${DD_H}" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <path class="mpc-ddarea" d="${area}"/>
          ${bench.length ? `<path class="mpc-line mpc-line--b mpc-line--thin" d="${pathOf(bench.map((v) => (finite(v) ? dy(v) : null)), n, (v) => v)}" vector-effect="non-scaling-stroke"/>` : ''}
          <line class="mpc-cross" x1="0" x2="0" y1="0" y2="${DD_H}" vector-effect="non-scaling-stroke" hidden/></svg>
          <span class="mpc-ddmin">${esc(spec.fmt.dd ? spec.fmt.dd(Math.min(...vals)) : '')}</span></div></div>`;
    }
    const leftOf = (i) => (i / Math.max(1, n - 1) * 100).toFixed(2);
    let markers = '';
    if (spec.showMarkers && spec.markers && spec.markers.length) {
      const lead = spec.series[0].values;
      markers = spec.markersOnLine
        ? spec.markers.map((m) => {
          const i = spec.dates.findIndex((d) => d >= m.date);
          if (i < 0 || !finite(lead[i])) return '';
          return `<i class="mpc-pin mpc-pin--${esc(m.kind || 'event')}" style="left:${leftOf(i)}%;top:${(((ext.hi - lead[i]) / (ext.hi - ext.lo)) * 100).toFixed(2)}%" title="${esc(`${spec.fmt.date(m.date)}: ${m.label}`)}"></i>`;
        }).join('')
        : `<div class="mpc-markers">${spec.markers.map((m) => {
          const i = spec.dates.findIndex((d) => d >= m.date);
          if (i < 0) return '';
          return `<i class="mpc-mk mpc-mk--${esc(m.kind || 'event')}" style="left:${leftOf(i)}%" title="${esc(`${spec.fmt.date(m.date)}: ${m.label}`)}"></i>`;
        }).join('')}</div>`;
    }
    const shade = (spec.shade || []).map((s) => `<div class="mpc-shade mpc-shade--${esc(s.cls)}" style="left:${leftOf(s.from)}%;width:${(Math.max(0, s.to - s.from) / Math.max(1, n - 1) * 100).toFixed(2)}%"><span>${esc(s.label || '')}</span></div>`).join('');
    const end = spec.endLabels ? endLabels(spec, ext) : '';
    return `<figure class="mpc${spec.tone ? ` is-${esc(spec.tone)}` : ''}${end ? ' has-end' : ''}" data-chart="${esc(spec.id)}">
      <div class="mpc-area" tabindex="0" role="group" aria-roledescription="interactive chart" aria-label="${esc(spec.ariaLabel)}">
        <div class="mpc-y" aria-hidden="true">${ylab}</div>
        <div class="mpc-plot">
          ${shade}
          <svg class="mpc-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true" focusable="false">${grid}</svg>
          <svg class="mpc-svg mpc-lines" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true" focusable="false">${defs}${band}${lines}</svg>
          <svg class="mpc-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true" focusable="false"><line class="mpc-cross" x1="0" x2="0" y1="0" y2="${H}" vector-effect="non-scaling-stroke" hidden/></svg>
          ${dots}${markers}
          <div class="mpc-tip" role="presentation" hidden></div>
        </div>
        ${end}
        <div class="mpc-x" aria-hidden="true">${xLabels(spec.dates, spec.fmt.tick, spec.xticks)}</div>
      </div>
      ${dd}
      <p class="sr-only mpc-live" aria-live="polite"></p>
      ${spec.unitLabel ? `<figcaption class="mpc-cap">${esc(spec.unitLabel)}</figcaption>` : ''}
    </figure>`;
  }

  function bind(host, spec) {
    const fig = host.querySelector('.mpc');
    if (!fig) return null;
    const area = fig.querySelector('.mpc-area');
    const plot = fig.querySelector('.mpc-plot');
    const tip = fig.querySelector('.mpc-tip');
    const live = fig.querySelector('.mpc-live');
    const crosses = [...fig.querySelectorAll('.mpc-cross')];
    const dots = [...fig.querySelectorAll('.mpc-dot')];
    const n = spec.dates.length;
    const ext = extent(spec.series, spec.zero !== false);
    let cur = -1;
    let pinned = false;
    let lastSpoken = 0;

    function show(i, speak) {
      i = Math.max(0, Math.min(n - 1, i));
      if (i === cur && !speak) return;
      cur = i;
      const px = n > 1 ? i / (n - 1) : 0;
      crosses.forEach((c) => { c.removeAttribute('hidden'); c.setAttribute('x1', f1(px * W)); c.setAttribute('x2', f1(px * W)); });
      spec.series.forEach((s, k) => {
        const dot = dots[k];
        if (!dot) return;
        const v = s.values[i];
        if (!finite(v)) { dot.hidden = true; return; }
        dot.hidden = false;
        dot.style.left = `${(px * 100).toFixed(2)}%`;
        dot.style.top = `${(((ext.hi - v) / (ext.hi - ext.lo)) * 100).toFixed(2)}%`;
      });
      tip.hidden = false;
      tip.innerHTML = spec.tip(i);
      // Flip to the left of the crosshair in the right half of the plot.
      tip.classList.toggle('is-left', px > 0.55);
      tip.style.left = `${(px * 100).toFixed(2)}%`;
      if (speak) {
        // Trailing debounce: a held arrow key announces the point it stops on, once.
        clearTimeout(lastSpoken);
        lastSpoken = setTimeout(() => { live.textContent = spec.speak ? spec.speak(cur) : tip.textContent; }, 180);
      }
    }
    function hide() {
      if (pinned) return;
      cur = -1;
      tip.hidden = true;
      crosses.forEach((c) => c.setAttribute('hidden', ''));
      dots.forEach((d) => { d.hidden = true; });
    }
    const indexAt = (clientX) => {
      const r = plot.getBoundingClientRect();
      const t = r.width ? (clientX - r.left) / r.width : 0;
      return Math.round(Math.max(0, Math.min(1, t)) * (n - 1));
    };
    area.addEventListener('pointermove', (e) => { if (e.pointerType === 'mouse' && e.buttons === 0 || e.pointerType !== 'mouse') show(indexAt(e.clientX)); });
    area.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse') { pinned = true; show(indexAt(e.clientX), true); } });
    area.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') { pinned = false; if (document.activeElement !== area) hide(); } });
    area.addEventListener('focus', () => { pinned = false; show(cur < 0 ? n - 1 : cur, true); });
    area.addEventListener('blur', () => { pinned = false; hide(); });
    area.addEventListener('keydown', (e) => {
      const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : e.key === 'PageUp' ? -10 : e.key === 'PageDown' ? 10 : 0;
      if (step) { show((cur < 0 ? n - 1 : cur) + step, true); e.preventDefault(); return; }
      if (e.key === 'Home') { show(0, true); e.preventDefault(); } else if (e.key === 'End') { show(n - 1, true); e.preventDefault(); } else if (e.key === 'Escape') { pinned = false; hide(); }
    });
    // A tap elsewhere releases a touch-pinned reading.
    const release = (e) => { if (pinned && !fig.contains(e.target)) { pinned = false; hide(); } };
    document.addEventListener('pointerdown', release);
    fig._mpc = { show, hide, get index() { return cur; }, release: () => document.removeEventListener('pointerdown', release) };
    // Draw-in: the lines are revealed left to right once, so the time axis reads as time.
    // Only when the data or range just changed; a re-render for any other reason is static.
    if (spec.animate === false || typeof requestAnimationFrame !== 'function') fig.classList.add('is-drawn', 'is-static');
    else requestAnimationFrame(() => fig.classList.add('is-drawn'));
    return fig._mpc;
  }

  function mount(host, spec) {
    if (host.firstElementChild && host.firstElementChild._mpc) host.firstElementChild._mpc.release();
    host.innerHTML = render(spec);
    return bind(host, spec);
  }

  /* Accessible table alternative: first, last and evenly spaced rows. */
  function tableHtml(spec, maxRows) {
    const n = spec.dates.length;
    const cap = Math.max(2, maxRows || 24);
    const picks = n <= cap ? [...Array(n).keys()] : [...new Set([...Array(cap).keys()].map((k) => Math.round(k / (cap - 1) * (n - 1))))];
    const head = spec.table.columns.map((c) => `<th scope="col"${c.num ? ' class="num"' : ''}>${esc(c.label)}</th>`).join('');
    const body = picks.map((i) => `<tr><th scope="row">${esc(spec.fmt.date(spec.dates[i]))}</th>${spec.table.cells(i).map((c) => `<td class="num">${c}</td>`).join('')}</tr>`).join('');
    return `<div class="mpc-table-wrap" tabindex="0" role="region" aria-label="${esc(spec.table.caption)}"><table class="mpc-table"><caption>${esc(spec.table.caption)}${n > cap ? ` Showing ${picks.length} of ${n} points, evenly spaced.` : ''}</caption><thead><tr><th scope="col">${esc(spec.table.first || 'Date')}</th>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  /* A half-dial for one rate, with its interval as a lighter band. Neutral colour:
   * a hit rate is a record, not a grade. */
  function gaugeHtml(o) {
    const R = 76;
    const cx = 100;
    const cy = 96;
    const pt = (f) => { const a = Math.PI * (1 - f); return [cx + R * Math.cos(a), cy - R * Math.sin(a)]; };
    const arc = (a, b) => { const [x1, y1] = pt(a); const [x2, y2] = pt(b); return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${R} ${R} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`; };
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const [x1, y1] = pt(f);
      const a = Math.PI * (1 - f);
      const x2 = cx + (R - 9) * Math.cos(a);
      const y2 = cy - (R - 9) * Math.sin(a);
      return `<line class="mpg-tick" x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}"/>`;
    }).join('');
    const [nx, ny] = pt(o.rate);
    return `<div class="mpg" role="img" aria-label="${esc(o.ariaLabel)}">
      <svg viewBox="0 0 200 112" aria-hidden="true" focusable="false">
        <path class="mpg-track" d="${arc(0, 1)}" pathLength="1"/>
        ${o.lo != null ? `<path class="mpg-band" d="${arc(o.lo, o.hi)}"/>` : ''}
        <path class="mpg-value" d="${arc(0, Math.max(0.001, o.rate))}" pathLength="1"/>
        ${ticks}
        <circle class="mpg-knob" cx="${nx.toFixed(2)}" cy="${ny.toFixed(2)}" r="4.5"/>
      </svg>
      <div class="mpg-read"><strong>${esc(o.text)}</strong><span>${esc(o.sub || '')}</span></div>
    </div>`;
  }

  /* The portfolio risk rule as a dial: four zones of equal width (the rule's bands),
   * the needle at the published points within its band. o: {points, zones:[{key,label,
   * from,to}] (inclusive point ranges), level, ariaLabel, size:'sm'|'md', ghost (points,
   * drawn as a faint second needle: the value before a what-if)}. */
  function riskGaugeHtml(o) {
    const zones = o.zones;
    const R = 80;
    const r = 58;
    const cx = 100;
    const cy = 96;
    const gap = 0.012;
    const pt = (rad, f) => { const a = Math.PI * (1 - f); return [cx + rad * Math.cos(a), cy - rad * Math.sin(a)]; };
    const seg = (f0, f1_) => {
      const [x1, y1] = pt(R, f0); const [x2, y2] = pt(R, f1_); const [x3, y3] = pt(r, f1_); const [x4, y4] = pt(r, f0);
      return `M${x1.toFixed(2)} ${y1.toFixed(2)}A${R} ${R} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}L${x3.toFixed(2)} ${y3.toFixed(2)}A${r} ${r} 0 0 0 ${x4.toFixed(2)} ${y4.toFixed(2)}Z`;
    };
    const frac = (p) => {
      const k = zones.findIndex((z) => p >= z.from && p <= z.to);
      if (k < 0) return p < zones[0].from ? 0 : 1;
      const z = zones[k];
      return (k + (p - z.from + 0.5) / (z.to - z.from + 1)) / zones.length;
    };
    const cur = finite(o.points) ? zones.findIndex((z) => o.points >= z.from && o.points <= z.to) : -1;
    const arcs = zones.map((z, k) => `<path class="mprg-z mprg-z--${esc(z.key)}${k === cur ? ' is-on' : ''}" d="${seg(k / zones.length + gap / 2, (k + 1) / zones.length - gap / 2)}"/>`).join('');
    const needle = (p, cls) => {
      const f = frac(p);
      const [x1, y1] = pt(18, f); const [x2, y2] = pt(r - 4, f);
      return `<line class="mprg-n${cls}" x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}"/>`;
    };
    return `<div class="mprg${o.size ? ` mprg--${esc(o.size)}` : ''}" role="img" aria-label="${esc(o.ariaLabel)}">
      <svg viewBox="0 0 200 104" aria-hidden="true" focusable="false">${arcs}
        ${finite(o.ghost) && o.ghost !== o.points ? needle(o.ghost, ' is-ghost') : ''}
        ${finite(o.points) ? `${needle(o.points, '')}<circle class="mprg-hub" cx="${cx}" cy="${cy}" r="6"/>` : ''}</svg>
    </div>`;
  }

  /* A donut of shares of one whole. segs: [{key, label, value, slot, over, limit}] with
   * value in [0,1]; slot is a categorical slot (1-8) or 'cash' / 'other'. Arcs keep a
   * 2px surface gap. A slice over its limit gets an amber outer ring from the limit to
   * its end and a tick at the limit. Identity never rests on colour: the legend beside
   * it names each slice with its share. */
  function donutHtml(segs, o) {
    const R = 60;
    const r = 40;
    const C = 70;
    const total = segs.reduce((t, x) => t + Math.max(0, x.value || 0), 0);
    if (!(total > 0)) return '';
    let a0 = -Math.PI / 2;
    const gap = segs.length > 1 ? 0.018 : 0;
    const pt = (rad, a) => [C + rad * Math.cos(a), C + rad * Math.sin(a)];
    const ring = (rOut, rIn, s0, s1) => {
      const large = s1 - s0 > Math.PI ? 1 : 0;
      const [x1, y1] = pt(rOut, s0); const [x2, y2] = pt(rOut, s1); const [x3, y3] = pt(rIn, s1); const [x4, y4] = pt(rIn, s0);
      return `M${x1.toFixed(2)} ${y1.toFixed(2)}A${rOut} ${rOut} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}L${x3.toFixed(2)} ${y3.toFixed(2)}A${rIn} ${rIn} 0 ${large} 0 ${x4.toFixed(2)} ${y4.toFixed(2)}Z`;
    };
    let extra = '';
    const arcs = segs.map((x) => {
      const sweep = (Math.max(0, x.value) / total) * Math.PI * 2;
      const start = a0;
      const s0 = a0 + gap / 2;
      const s1 = a0 + sweep - gap / 2;
      a0 += sweep;
      if (s1 <= s0) return '';
      const d = sweep >= Math.PI * 2 - 1e-6
        ? `M${C - R} ${C}a${R} ${R} 0 1 0 ${2 * R} 0a${R} ${R} 0 1 0 ${-2 * R} 0M${C - r} ${C}a${r} ${r} 0 1 1 ${2 * r} 0a${r} ${r} 0 1 1 ${-2 * r} 0`
        : ring(R, r, s0, s1);
      if (x.over && finite(x.limit) && x.limit > 0 && x.limit < x.value) {
        const lim = start + (x.limit / total) * Math.PI * 2;
        extra += `<path class="mpd-over" data-seg="${esc(x.key)}" d="${ring(R + 6, R + 2, lim, s1)}"/>`;
        const [t1x, t1y] = pt(r - 3, lim); const [t2x, t2y] = pt(R + 7, lim);
        extra += `<line class="mpd-lim" x1="${t1x.toFixed(2)}" y1="${t1y.toFixed(2)}" x2="${t2x.toFixed(2)}" y2="${t2y.toFixed(2)}"/>`;
      }
      return `<path class="mpd-seg mpd-s-${esc(x.slot)}" data-seg="${esc(x.key)}" d="${d}" fill-rule="evenodd"/>`;
    }).join('');
    return `<div class="mpd"><svg viewBox="-8 -8 156 156" aria-hidden="true" focusable="false">${arcs}${extra}</svg>
      <div class="mpd-c" aria-hidden="true"><b>${esc(o.centerValue || '')}</b><span>${esc(o.centerLabel || '')}</span></div></div>`;
  }

  /* A trend line for one holding: values as given, scaled to the box, no axes. */
  function sparkHtml(values, tone) {
    const v = (values || []).filter(finite);
    if (v.length < 2) return '';
    const lo = Math.min(...v);
    const hi = Math.max(...v);
    const span = hi - lo || 1;
    const pts = v.map((x, i) => `${(i / (v.length - 1) * 100).toFixed(1)},${(22 - ((x - lo) / span) * 20 - 1).toFixed(1)}`).join(' ');
    return `<svg class="mps-spark${tone ? ` is-${tone}` : ''}" viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true" focusable="false"><polyline points="${pts}" vector-effect="non-scaling-stroke"/></svg>`;
  }

  /* An area sparkline: a 2px line with a soft fade beneath, scaled to its own range. */
  function areaSparkHtml(values, id) {
    const v = (values || []).filter(finite);
    if (v.length < 2) return '';
    const lo = Math.min(...v);
    const hi = Math.max(...v);
    const span = hi - lo || 1;
    const pts = v.map((x, i) => [i / (v.length - 1) * 100, 30 - ((x - lo) / span) * 26 - 2]);
    const line = `M${pts.map(([x, yy]) => `${x.toFixed(2)} ${yy.toFixed(2)}`).join('L')}`;
    const gid = `mpsa-${String(id || 'x').replace(/[^a-z0-9-]/gi, '')}`;
    return `<svg class="mps-area" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity="0.26"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>
      <path d="${line}L100 32L0 32Z" fill="url(#${gid})"/><path class="mps-area-l" d="${line}" vector-effect="non-scaling-stroke"/></svg>`;
  }

  /* Bars around a zero line: gains up (green), losses down (red), 2px gaps, rounded
   * ends; a missing value is a gap, never a zero-height bar. */
  function miniBarsHtml(values) {
    const n = values.length;
    if (!n) return '';
    const known = values.filter(finite);
    const max = Math.max(...known.map(Math.abs), 1e-9);
    const up = Math.max(...known, 0) / max;
    const down = -Math.min(...known, 0) / max;
    const span = up + down || 1;
    const zero = (up / span) * 100;
    // The gap between bars narrows as their number grows, so any count fits the width.
    const gap = n > 40 ? 0 : n > 20 ? 1 : 2;
    return `<span class="mpb" aria-hidden="true" style="--z:${zero.toFixed(2)}%;gap:${gap}px">${values.map((v) => (finite(v)
      ? `<i class="${v < 0 ? 'is-down' : v > 0 ? 'is-up' : 'is-flat'}" style="--h:${(Math.abs(v) / max / span * 100).toFixed(2)}%"></i>`
      : '<i class="is-gap"></i>')).join('')}<b></b></span>`;
  }

  /* Squarified treemap (Bruls et al.). items: [{key, value}] (value > 0), sorted by the
   * caller or not; returns [{key, x, y, w, h}] in percent of a W x H box. */
  function squarify(items, w, h) {
    const list = items.filter((d) => d.value > 0).slice().sort((a, b) => b.value - a.value);
    const total = list.reduce((t, d) => t + d.value, 0);
    if (!total) return [];
    const scale = (w * h) / total;
    const nodes = list.map((d) => ({ key: d.key, area: d.value * scale }));
    const out = [];
    let x = 0; let y = 0; let cw = w; let ch = h;
    const worst = (row, side) => {
      const s = row.reduce((t, d) => t + d.area, 0);
      const mx = Math.max(...row.map((d) => d.area)); const mn = Math.min(...row.map((d) => d.area));
      return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
    };
    const layout = (row) => {
      const s = row.reduce((t, d) => t + d.area, 0);
      if (cw >= ch) {
        const rw = s / ch; let yy = y;
        row.forEach((d) => { const hh = d.area / rw; out.push({ key: d.key, x, y: yy, w: rw, h: hh }); yy += hh; });
        x += rw; cw -= rw;
      } else {
        const rh = s / cw; let xx = x;
        row.forEach((d) => { const ww = d.area / rh; out.push({ key: d.key, x: xx, y, w: ww, h: rh }); xx += ww; });
        y += rh; ch -= rh;
      }
    };
    let row = [];
    nodes.forEach((d) => {
      const side = Math.min(cw, ch);
      if (!row.length || worst([...row, d], side) <= worst(row, side)) row.push(d);
      else { layout(row); row = [d]; }
    });
    if (row.length) layout(row);
    return out;
  }

  const api = { render, bind, mount, tableHtml, gaugeHtml, riskGaugeHtml, donutHtml, sparkHtml, areaSparkHtml, miniBarsHtml, squarify, _extent: extent, _niceStep: niceStep, _bandRuns: bandRuns };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.QuantaCharts = api;
}(typeof window !== 'undefined' ? window : globalThis));
