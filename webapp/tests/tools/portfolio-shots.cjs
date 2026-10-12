// Screenshots of My Portfolio against the synthetic preview server: one full
// page per tab, width and theme, plus layout checks (horizontal overflow, text
// clipped by its box) and each tab's page height. For visual review only.
//   node tests/tools/portfolio-shots.cjs [widths=1440,1024,768,390] [themes=dark,light] [tabs=overview,holdings,activity,review,news]
// Env: BASE_URL (default http://127.0.0.1:5617), OUT_DIR, TAG, HIDE=1 (hide amounts), DRAWER=<symbol>,
// NOW=<iso> pins the page clock (the activity fixtures' "now"), HASH=<suffix> appended to #holdings.
const { chromium } = require('playwright');
const path = require('node:path');
const fs = require('node:fs');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5617';
const OUT = process.env.OUT_DIR || path.resolve(__dirname, '../../../.playwright-mcp');
const TAG = process.env.TAG || 'shot';
const widths = (process.argv[2] || '1440,1024,768,390').split(',').map(Number);
const themes = (process.argv[3] || 'dark,light').split(',');
const tabs = (process.argv[4] || 'overview,holdings,activity,review,news').split(',');

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  for (const theme of themes) {
    for (const width of widths) {
      const ctx = await browser.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: width < 800 ? 2 : 1, colorScheme: theme });
      await ctx.addInitScript(([t, hide]) => {
        try { localStorage.setItem('quanta_theme', t); localStorage.setItem('quanta.holdings.plan', JSON.stringify({ profile: { contribution: 2000, liquidity_reserve: 3000 }, candidates: [] })); if (hide) localStorage.setItem('quanta.holdings.hideAmounts', '1'); } catch (_) { /* preview only */ }
      }, [theme, process.env.HIDE === '1']);
      const page = await ctx.newPage();
      if (process.env.NOW) await page.clock.install({ time: new Date(process.env.NOW) });
      const errors = [];
      page.on('pageerror', (e) => errors.push(String(e)));
      await page.goto(`${BASE}/#holdings${process.env.HASH || ''}`, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('.hp-tabs', { timeout: 20000 });
      await page.waitForTimeout(1200);
      for (const tab of tabs) {
        await page.click(`[data-hp-tab="${tab}"]`);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(900);
        const name = `${TAG}-${tab}-${width}-${theme}`;
        await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
        const report = await page.evaluate(() => {
          const overflowX = document.documentElement.scrollWidth - window.innerWidth;
          const clipped = [];
          document.querySelectorAll('#view-holdings .hp-tabpanel:not([hidden]) *').forEach((el) => {
            const cs = getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden' || el.closest('.sr-only')) return;
            const text = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
            if (!text || el.closest('.hp-table-scroll')) return;
            if (el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== 'visible') clipped.push(`${el.className || el.tagName}: ${el.textContent.trim().slice(0, 30)}`);
          });
          const main = document.querySelector('.app-main');
          return { overflowX, clipped: clipped.slice(0, 10), height: Math.round(document.documentElement.scrollHeight), content: main ? Math.round(document.querySelector('#view-holdings').getBoundingClientRect().bottom + window.scrollY) : null };
        });
        console.log(`${name}: page=${report.height}px (${(report.content / 900).toFixed(2)} viewports to end of content) overflowX=${report.overflowX}${report.clipped.length ? `\n  clipped: ${report.clipped.join(' | ')}` : ''}`);
      }
      if (process.env.DRAWER) {
        await page.click('[data-hp-tab="holdings"]');
        await page.click(`[data-hp-toggle="${process.env.DRAWER}"]`);
        await page.waitForTimeout(300);
        await page.screenshot({ path: path.join(OUT, `${TAG}-drawer-${width}-${theme}.png`) });
        await page.keyboard.press('Escape');
      }
      if (errors.length) console.log(`  errors: ${errors.join(' | ')}`);
      await ctx.close();
    }
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
