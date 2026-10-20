import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * Discover: tickers open TradingView in a new tab, no control is nested inside another,
 * and the page has no WCAG 2.1 A/AA violation in either theme.
 */
for (const theme of ['dark', 'light'] as const) {
  test(`DISC-A11Y-${theme.toUpperCase()} Discover has zero WCAG 2.1 AA violations`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.goto('/#discover', { waitUntil: 'domcontentloaded' });
    await page.locator('#view-discover.active').waitFor();
    await page.locator('#top-movers-body .q-board-row, #top-movers-body .q-empty').first().waitFor({ timeout: 60_000 });
    const results = await new AxeBuilder({ page }).include('#view-discover').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    const summary = results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`);
    expect(summary, summary.join('\n')).toEqual([]);
  });
}

test('DISC-TV every Discover ticker link points at its TradingView chart and opens a new tab', async ({ page }) => {
  await page.goto('/#discover', { waitUntil: 'domcontentloaded' });
  await page.locator('#top-movers-body .q-board-row, #top-movers-body .q-empty').first().waitFor({ timeout: 60_000 });
  const links = page.locator('#view-discover a[data-tv-link]');
  const n = await links.count();
  for (let i = 0; i < n; i++) {
    const a = links.nth(i);
    await expect(a).toHaveAttribute('target', '_blank');
    await expect(a).toHaveAttribute('href', /^https:\/\/www\.tradingview\.com\/chart\/\?symbol=/);
  }
});
