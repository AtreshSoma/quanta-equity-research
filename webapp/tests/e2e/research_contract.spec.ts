import { test, expect, Page } from '@playwright/test';

/**
 * Research tear sheet = backend Decision, field for field.
 *
 * Runs live analyses through the UI and compares what the hero renders with
 * the Decision the job returned: action label, model stance, new-money
 * eligibility, holder action, primary blocker and remedy, horizon, target
 * basis, sizing and evidence. Nothing on the hero may be a browser verdict.
 */

const STANCE: Record<string, RegExp> = { BULLISH: /Bullish score/, NEUTRAL: /Neutral score/, BEARISH: /Bearish score/, NONE: /No score/ };
const NEW_MONEY: Record<string, RegExp> = { ELIGIBLE: /Eligible/, WATCH: /Not yet/, AVOID: /Avoid/, NONE: /No call/ };
const HOLDER: Record<string, RegExp> = { HOLD: /Hold, keep the stop/, TRIM: /Trim into strength/, EXIT: /Exit on strength/, 'N/A': /No call/ };

async function analyze(page: Page, ticker: string, fast: boolean) {
  await page.goto(`/?nocache=${Date.now()}#analyze`, { waitUntil: 'domcontentloaded' });
  await page.locator('.nav-tab[data-view="analyze"]').click();
  const toggle = page.locator('#an-fast-mode');
  if (fast) await toggle.check(); else await toggle.uncheck();
  await page.locator('#an-ticker').fill(ticker);
  const started = page.waitForResponse((r) => r.url().endsWith('/api/analyze') && r.request().method() === 'POST');
  await page.locator('#btn-analyze').click();
  const { job_id: jobId } = await (await started).json();
  await expect(page.locator('#an-results .decision-action .signal')).toBeVisible({ timeout: 85_000 });
  const job = await (await page.request.get(`/api/jobs/${jobId}`)).json();
  expect(job.status).toBe('complete');
  return job.result;
}

for (const [ticker, fast] of [['NVDA', false], ['HD', false], ['SPY', true], ['JPM', true]] as const) {
  test(`RC-${ticker}${fast ? '-fast' : ''} hero renders the backend Decision exactly`, async ({ page }) => {
    const result = await analyze(page, ticker, fast);
    const d = result.decision;
    expect(d, 'job returned a Decision').toBeTruthy();
    const hero = page.locator('#an-results .decision-action');

    await expect(hero.locator('.signal')).toHaveText(d.label);
    await expect(hero.locator('[data-contract="model_stance"]')).toContainText(STANCE[d.model_stance]);
    await expect(hero.locator('[data-contract="new_money"]')).toContainText(NEW_MONEY[d.new_money]);
    await expect(hero.locator('[data-contract="holder_action"]')).toContainText(HOLDER[d.holder_action]);
    await expect(hero).toContainText(d.horizon.label);

    const funded = ['STRONG_BUY', 'BUY', 'ACCUMULATE'].includes(d.action);
    expect(d.new_money === 'ELIGIBLE').toBe(funded);
    if (!funded) {
      expect(d.sizing.shares, 'no executable size without new-money eligibility').toBe(0);
      const blocker = page.locator('#an-results [data-primary-blocker]');
      await expect(blocker).toHaveAttribute('data-primary-blocker', d.primary_blocker.code);
      await expect(blocker).toContainText(d.primary_blocker.text);
    } else {
      await expect(page.locator('#an-results [data-primary-blocker]')).toHaveCount(0);
    }

    // One horizon: R/R is measured to the Decision's own target (its horizon in
    // months); the 12-month valuation target is shown beside it as context.
    const levels = page.locator('#an-results .decision-levels');
    if (d.levels.target) {
      expect(d.levels.target_horizon_days).toBe(d.horizon.trading_days);
      await expect(levels).toContainText(`${Math.round(d.levels.target_horizon_days / 21)}-month target`);
      if (d.levels.valuation_target) await expect(levels).toContainText('12-month valuation target');
    } else {
      await expect(levels).toContainText('none published by this Decision');
    }
    // Evidence status is the Decision's own.
    expect(['UNTESTED', 'PARTIAL', 'NO_EDGE', 'STALE', 'VALIDATED']).toContain(d.evidence.status);

    // No horizontal overflow on this viewport.
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await page.screenshot({ path: test.info().outputPath(`${ticker}-hero.png`), fullPage: false });
  });
}

async function run(page: Page, ticker: string, market: 'US' | 'IN', fast = true) {
  await page.goto(`/?nocache=${Date.now()}#analyze`, { waitUntil: 'domcontentloaded' });
  await page.evaluate((m) => localStorage.setItem('quanta_market', m), market);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('.nav-tab[data-view="analyze"]').click();
  const toggle = page.locator('#an-fast-mode');
  if (fast) await toggle.check(); else await toggle.uncheck();
  await page.locator('#an-ticker').fill(ticker);
  const started = page.waitForResponse((r) => r.url().endsWith('/api/analyze') && r.request().method() === 'POST');
  await page.locator('#btn-analyze').click();
  const resp = await started;
  expect(resp.status(), `${ticker} request accepted`).toBe(202);
  await expect(page.locator('#an-results .decision-action, #an-results .research-diagnostic').first())
    .toBeVisible({ timeout: 85_000 });
}

test('RC-DIAG new listing in India mode: too new to score, with the facts and the US listing named', async ({ page }) => {
  await run(page, 'RVII', 'IN');
  const card = page.locator('#an-results [data-diagnostic="insufficient_data"]');
  await expect(card).toBeVisible();
  await expect(card).toContainText('Robinhood Ventures Fund II');
  await expect(card).toContainText(/trading sessions since 2026-08-13/);
  await expect(card).toContainText('No NSE or BSE listing found');
  await expect(card).not.toContainText('did not respond');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test('RC-DIAG an index is unsupported and offers its tracking ETF, which then runs', async ({ page }) => {
  await run(page, '^GSPC', 'US');
  const card = page.locator('#an-results [data-diagnostic="unsupported_instrument"]');
  await expect(card).toContainText('not a stock or fund');
  await card.locator('[data-diag-action="ticker"][data-ticker="SPY"]').click();
  await expect(page.locator('#an-results .decision-action .signal')).toBeVisible({ timeout: 85_000 });
  await expect(page.locator('#an-results .decision-ticker')).toHaveText('SPY');
});

for (const [ticker, market, expected] of [['BRK.B', 'US', 'BRK-B'], ['M&M.NS', 'IN', 'M&M.NS'], ['RELIANCE', 'IN', 'RELIANCE.NS']] as const) {
  test(`RC-RESOLVE ${ticker} (${market}) analyses ${expected}`, async ({ page }) => {
    await run(page, ticker, market);
    await expect(page.locator('#an-results .decision-ticker')).toHaveText(expected);
    if (ticker !== expected) await expect(page.locator('#an-results [data-symbol-note]')).toBeVisible();
  });
}
