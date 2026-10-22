import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// A year of synthetic daily bars (with warm-up) so the chart never touches Yahoo.
function chartPayload(symbol: string, range: string) {
  const day = 86400000;
  const start = Date.parse('2025-01-02T05:00:00Z');
  const bar = (i: number) => {
    const c = 100 + 20 * Math.sin(i / 25) + i * 0.05;
    return [start + i * day, +c.toFixed(2), 1_000_000 + (i % 7) * 50_000, +(c - 0.6).toFixed(2), +(c + 1.2).toFixed(2), +(c - 1.4).toFixed(2)];
  };
  const all = Array.from({ length: 500 }, (_, i) => bar(i));
  return {
    available: true, symbol, name: `${symbol} Inc.`, exchange: 'NasdaqGS', currency: 'USD', instrument_type: 'EQUITY',
    timezone: 'America/New_York', range, interval: '1d', points: all.slice(250), lead: all.slice(0, 250),
    events: [{ t: all[400][0], kind: 'dividend', value: 0.25 }], previous_close: null, regular_price: all[499][1],
    regular_time: all[499][0], session: {}, extended: null, market_open: false, retrieved_at: Date.now(),
  };
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/chart?**', async (route) => {
    const url = new URL(route.request().url());
    await route.fulfill({ json: chartPayload(url.searchParams.get('symbol') || 'AAA', url.searchParams.get('range') || '1y') });
  });
});

async function mountChart(page) {
  await page.goto('/#analyze', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => (window as any).QuantaLiveChart);
  await page.evaluate(() => {
    localStorage.setItem('quanta_chart_range_e2e', '1y');
    localStorage.removeItem('quanta_chart_prefs_e2e');
    const host = document.createElement('div');
    host.id = 'e2e-chart';
    host.style.cssText = '--lc-h: 300px; padding: 12px; max-width: 1000px';
    document.querySelector('.app-main')!.prepend(host);
    (window as any).QuantaLiveChart.mount(host, { symbol: 'AAA', context: 'e2e', showQuote: true,
      levels: [{ label: 'Stop', value: 95, tone: 'stop' }, { label: 'Target', value: 140, tone: 'target' }] });
  });
  await expect(page.locator('#e2e-chart .lc-svg')).toBeVisible();
}

test('the live chart switches to candles, adds an RSI pane and zooms', async ({ page }) => {
  await mountChart(page);
  const chart = page.locator('#e2e-chart');
  await chart.getByRole('button', { name: 'Candles' }).click();
  await expect(chart.locator('.lc-candle').first()).toBeVisible();
  await chart.locator('.lc-studies summary').click();
  await chart.locator('[data-lc-study="rsi"]').check();
  await expect(chart.locator('.lc-legend[data-pane="rsi"]')).toContainText('RSI 14');
  await expect(chart.locator('[data-lc-study="sma200"]')).toBeEnabled();
  await chart.locator('[data-lc-study="sma200"]').check();
  // Warm-up bars make the 200-bar average exist from the first plotted bar.
  await expect(chart.locator('.lc-legend[data-pane="price"]')).toContainText('SMA 200');
  await page.keyboard.press('Escape');
  const before = await chart.locator('.lc-candle').count();
  await chart.getByRole('button', { name: 'Zoom in' }).click();
  expect(await chart.locator('.lc-candle').count()).toBeLessThan(before);
  await chart.getByRole('button', { name: 'Reset' }).click();
  expect(await chart.locator('.lc-candle').count()).toBe(before);
  await expect(chart.locator('.lc-event--dividend')).toHaveCount(1);
  const svg = chart.locator('.lc-svg');
  await svg.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(chart.locator('.lc-live')).toContainText('open');
  const axe = await new AxeBuilder({ page }).include('#e2e-chart').withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(axe.violations.map((v) => v.id)).toEqual([]);
});

test('My list: the bell adds a name, holds an alert, and closes with Escape', async ({ page }) => {
  await page.goto('/#dashboard', { waitUntil: 'domcontentloaded' });
  const bell = page.locator('#ml-toggle');
  await bell.click();
  const panel = page.locator('#ml-panel');
  await expect(panel).toBeVisible();
  const sym = `Z${Date.now() % 100000}`;
  await panel.locator('#ml-add-input').fill(sym);
  // A poll can complete between typing and submitting; retain the draft and focus.
  await page.evaluate(async () => { await (window as any).QuantaMyList.refresh(); });
  await expect(panel.locator('#ml-add-input')).toHaveValue(sym);
  await expect(panel.locator('#ml-add-input')).toBeFocused();
  await panel.getByRole('button', { name: 'Add' }).click();
  await expect(panel.locator(`[data-ml-open="${sym}"]`)).toBeVisible();
  await expect(panel.locator('#ml-add-input')).toHaveValue('');
  await page.evaluate(async (s) => {
    await fetch('/api/alerts', { method: 'POST', headers: { 'X-QUANTA-Request': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: s, direction: 'below', level: 5, label: 'Stop' }) });
    await (window as any).QuantaMyList.refresh();
  }, sym);
  await expect(panel.locator('.ml-row--alert', { hasText: sym })).toContainText('at or below 5.00');
  const axe = await new AxeBuilder({ page }).include('#ml-panel').withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(axe.violations.map((v) => v.id)).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(bell).toBeFocused();
  // Clean up the scratch store.
  await page.evaluate(async (s) => {
    const h = { 'X-QUANTA-Request': '1' };
    const d = await (await fetch('/api/my-list')).json();
    for (const a of d.alerts.filter((x: any) => x.symbol === s)) await fetch(`/api/alerts/${a.id}`, { method: 'DELETE', headers: h });
    await fetch(`/api/my-list/${s}`, { method: 'DELETE', headers: h });
  }, sym);
});
