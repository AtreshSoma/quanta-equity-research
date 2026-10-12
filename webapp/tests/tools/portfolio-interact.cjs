// Interaction screenshots of My Portfolio against the synthetic preview server: 1D chart,
// treemap, a pinned allocation group, linked hover, and a what-if choice. Visual review only.
//   BASE_URL=http://127.0.0.1:5617 OUT_DIR=... node tests/tools/portfolio-interact.cjs [theme=dark]
const { chromium } = require('playwright');
const path = require('node:path');
const fs = require('node:fs');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5617';
const OUT = process.env.OUT_DIR || path.resolve(__dirname, '../../../.playwright-mcp');
const theme = process.argv[2] || 'dark';

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme });
  await ctx.addInitScript((t) => { try { localStorage.setItem('quanta_theme', t); } catch (_) { /* preview */ } }, theme);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const shot = async (sel, name) => { const el = await page.$(sel); if (el) await el.screenshot({ path: path.join(OUT, `ix-${name}-${theme}.png`) }); else console.log(`missing ${sel}`); };
  await page.goto(`${BASE}/#holdings`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.mpo-perf .mpc', { timeout: 20000 });
  await page.waitForTimeout(800);
  await page.click('[data-hp-x="range:1D"]');
  await page.waitForSelector('.mpo-perf .mpc-shade', { timeout: 8000 });
  await page.waitForTimeout(500);
  const plot = await page.$('.mpo-perf .mpc-plot');
  const box = await plot.boundingBox();
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height / 2);
  await page.waitForTimeout(200);
  await shot('.mpo-perf', 'perf-1D');
  await page.click('[data-hp-x="range:1Y"]');
  await page.waitForTimeout(600);
  await shot('.mpo-perf', 'perf-1Y');
  await page.hover('.mpd-row[data-seg-key="Healthcare"]');
  await page.waitForTimeout(200);
  await shot('.mpo-grid--a', 'hover-healthcare');
  await page.click('.mpd-row[data-seg-key="Technology"]');
  await page.waitForTimeout(300);
  await shot('.mpo-alloc-c', 'pin-tech');
  await page.keyboard.press('Escape');
  await page.hover('tr[data-mpo-row="JNJ"]');
  await page.waitForTimeout(200);
  await shot('.mpo', 'hover-row-jnj');
  await page.click('[data-hp-x="alloc-view:treemap"]');
  await page.waitForTimeout(300);
  await shot('.mpo-alloc-c', 'treemap');
  await page.click('[data-hp-tab="guidance"]');
  await page.waitForTimeout(600);
  await page.click('[data-hp-x="wi:AAPL:trim"]');
  await page.click('[data-hp-x="wi:TSM:all"]');
  await page.waitForTimeout(900);
  await shot('.hp-wi', 'whatif');
  if (errors.length) console.log(`errors: ${errors.join(' | ')}`);
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
