import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import * as fs from 'fs';
import * as path from 'path';

// WCAG 2.1 AA on the two views no other spec audits: the Research tear sheet
// and the Evidence page. The tear sheet is the recorded AAPL golden result
// (tests/golden/expected), served as a finished job, so no market data is read.
const golden = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', '..', 'tests', 'golden', 'expected', 'AAPL.json'), 'utf8'));

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];
const violations = (r: { violations: { id: string; nodes: { target: unknown }[] }[] }) =>
  r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`);

function bars() {
  const day = 86400000;
  const start = Date.parse('2025-01-02T05:00:00Z');
  const bar = (i: number) => { const c = 300 + 10 * Math.sin(i / 20); return [start + i * day, c, 1e6, c - 1, c + 2, c - 2]; };
  const all = Array.from({ length: 400 }, (_, i) => bar(i));
  return { available: true, symbol: 'AAPL', currency: 'USD', timezone: 'America/New_York', range: '1y', interval: '1d',
    points: all.slice(150), lead: all.slice(0, 150), events: [], session: {}, market_open: false, retrieved_at: Date.now() };
}

test('the Research tear sheet meets WCAG 2.1 AA', async ({ page }) => {
  const done = { status: 'complete', progress: 100, message: 'Complete', result: golden.result };
  await page.route('**/api/analyze', (route) => route.fulfill({ status: 202, json: { job_id: 'e2e-aapl', status: 'running' } }));
  await page.route('**/api/jobs/e2e-aapl/stream', (route) => route.fulfill({
    status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: `data: ${JSON.stringify({ status: 'complete', progress: 100 })}\n\n` }));
  await page.route('**/api/jobs/e2e-aapl', (route) => route.fulfill({ json: done }));
  await page.route('**/api/chart?**', (route) => route.fulfill({ json: bars() }));
  await page.goto('/#analyze', { waitUntil: 'domcontentloaded' });
  await page.locator('#an-ticker').fill('AAPL');
  await page.locator('#btn-analyze').click();
  await expect(page.locator('#an-results .decision-action')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#ts-chart .lc-svg')).toBeVisible();
  await expect(page.locator('#an-results [data-ml-slot] [data-ml-save]')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  const results = await new AxeBuilder({ page }).include('#an-results').withTags(TAGS).analyze();
  expect(violations(results)).toEqual([]);
});

test('the Evidence page meets WCAG 2.1 AA', async ({ page }) => {
  await page.goto('/#track', { waitUntil: 'domcontentloaded' });
  const view = page.locator('#view-track');
  await expect(view).toBeVisible();
  await expect(view).toContainText(/no edge|not yet validated|Tested/i, { timeout: 30_000 });
  const results = await new AxeBuilder({ page }).include('#view-track').withTags(TAGS).analyze();
  expect(violations(results)).toEqual([]);
});
