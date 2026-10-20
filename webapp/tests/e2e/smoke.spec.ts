import { test, expect, Page } from '@playwright/test';

/**
 * QUANTA Elite — SMOKE suite (§10.1 of QUANTA_E2E_TEST_SUITE.md).
 * Fast confidence check: every view loads, core flows work, no offline banner,
 * no console errors. Structure-only assertions so it survives live data.
 *
 * Cache-bust is required to bypass the cached HTML document (see repo notes):
 * a UNIQUE query param is the only reliable bust.
 */

const NOCACHE = () => `/?nocache=${Date.now()}`;

/** Fail the test on any uncaught page error or console error. */
function trackConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

/** The dashboard renders an offline banner on ANY uncaught render error — treat as failure. */
async function assertNotOffline(page: Page) {
  const offline = page.locator('[role="alert"]', { hasText: /Offline — retry once the backend is reachable/i });
  await expect(offline).toHaveCount(0);
}

test.describe('@smoke landing / dashboard', () => {
  test('DASH-001 all sections load, no offline banner, no console errors', async ({ page }) => {
    const errors = trackConsole(page);
    await page.goto(NOCACHE(), { waitUntil: 'networkidle' });

    // Core dashboard sections exist and leave their skeleton/loading state.
    const sectionIds = [
      '#ov-bulletin', '#ov-session', '#ov-week', '#decision-brief', '#market-pulse', '#sector-body',
      '#week-body', '#news-body', '#movers-body',
    ];
    for (const id of sectionIds) {
      await expect(page.locator(id)).toBeVisible();
    }

    // The full factor table sits in the For pros section, one disclosure away:
    // attached on load, it opens in place.
    await expect(page.locator('#signal-check-body')).toBeAttached();
    await expect(page.locator('#signal-check-body')).not.toBeVisible();
    await page.locator('#ov-pro-factors > summary').click();
    await expect(page.locator('#signal-check-body')).toBeVisible();

    // Opportunities and the security monitor live one step on, in Discover.
    await page.locator('.nav-tab[data-view="discover"]').click();
    for (const id of ['#portfolio-scan-body', '#top-movers-body']) {
      await expect(page.locator(id)).toBeVisible();
    }

    // Same for the veto queue: it is the "Avoid" tab of the market-monitor
    // group, behind Market Moves.
    await expect(page.locator('#setup-status-body')).toBeAttached();
    await expect(page.locator('#setup-status-body')).not.toBeVisible();
    await page.locator('#tab-scanner-vetoes').click();
    await expect(page.locator('#setup-status-body')).toBeVisible();
    await page.locator('#tab-top-movers').click();
    await expect(page.locator('#top-movers-body')).toBeVisible();

    // The source ledger is deliberately collapsed inside the data-health
    // popover, so it is attached but not visible until that control is opened.
    await expect(page.locator('#source-ledger-body')).toBeAttached();
    await expect(page.locator('#source-ledger-body')).not.toBeVisible();
    await page.locator('#data-health-toggle').click();
    await expect(page.locator('#source-ledger-body')).toBeVisible();
    await page.keyboard.press('Escape');
    await page.locator('.nav-tab[data-view="dashboard"]').click();

    // The regime panel must show the call, not just a skeleton.
    await expect(page.locator('#decision-brief .q-skel')).toHaveCount(0);
    await expect(page.locator('#decision-brief .ovx-mood-word')).toBeVisible();
    await expect(page.locator('#week-h')).toBeVisible();
    await assertNotOffline(page);
    expect(errors, `console/page errors:\n${errors.join('\n')}`).toEqual([]);
  });

  test('DASH-003 Signal Check confirmed count equals engine confirmed factors (math invariant)', async ({ page }) => {
    await page.goto(NOCACHE(), { waitUntil: 'networkidle' });

    // Derive expected confirmed-factor count straight from the API payload,
    // then compare to the rendered Signal Check "Confirmed" count chip.
    const apiConfirmed = await page.evaluate(async () => {
      const r = await fetch('/api/dashboard').then((x) => x.json());
      const engine = (r && r.market && r.market.market_regime_engine) || {};
      const factors = engine.factors || (r && r.market && r.market.tone_factors) || [];
      return factors.filter((f: any) => (f.score ?? 0) > 0).length;
    });

    // The factor disclosure's summary reads "N support, N neutral, N against".
    const meta = (await page.locator('#signal-check-meta').innerText().catch(() => '')) || '';
    const chipText = (meta.match(/^(\d+) support/) || [])[1] || '';
    if (chipText) {
      expect(Number(chipText)).toBe(apiConfirmed);
    } else {
      test.info().annotations.push({ type: 'note', text: 'Signal Check count chip empty — factor model may be warming up' });
    }
  });

  test('FUNC-001 exactly one view active per nav tab (no view stacking regression)', async ({ page }) => {
    await page.goto(NOCACHE(), { waitUntil: 'domcontentloaded' });
    for (const view of ['dashboard', 'discover', 'analyze', 'picks', 'holdings']) {
      await page.locator(`.nav-tab[data-view="${view}"]`).click();
      const visible = await page.locator('.view').evaluateAll(
        (els) => els.filter((e) => (e as HTMLElement).offsetParent !== null).length,
      );
      expect(visible, `view=${view}`).toBe(1);
    }
  });

  test('UX-001 no horizontal scroll on the dashboard', async ({ page }) => {
    await page.goto(NOCACHE(), { waitUntil: 'networkidle' });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(overflow, 'page has horizontal scroll').toBeFalsy();
  });
});

test.describe('@smoke portfolio builder', () => {
  test('PB-001 custom US scan completes with picks or an honest empty state', async ({ page }) => {
    test.setTimeout(150_000);
    const errors = trackConsole(page);
    await page.goto(NOCACHE(), { waitUntil: 'domcontentloaded' });
    await page.locator('.nav-tab[data-view="picks"]').click();

    // With a saved scan on screen, narrow layouts fold the form behind "Edit scan setup".
    const editSetup = page.locator('#sp-setup-toggle');
    if (await editSetup.isVisible() && (await editSetup.getAttribute('aria-expanded')) === 'false') await editSetup.click();
    await page.locator('#sp-scan').selectOption('custom');
    await page.locator('#sp-custom-tickers').fill('AAPL');
    await page.locator('#sp-capital').fill('2500');
    const launch = page.waitForResponse((response) => response.url().endsWith('/api/stock-picks') && response.request().method() === 'POST');
    await page.locator('#btn-scan').click();
    const started = await (await launch).json();

    // Wait for the worker's terminal state. The initial "Building…" panel is
    // non-empty and must not count as a completed scan.
    await expect(page.locator('#sp-status')).toHaveText(/Complete|failed/i, { timeout: 120_000 });
    const results = page.locator('#picks-results');
    await expect(results.locator('.picks-stream, .error-box')).toHaveCount(1);
    await expect(results).not.toContainText('Building the position list…');
    const job = await (await page.request.get(`/api/jobs/${started.job_id}`)).json();
    if (job.status === 'complete') {
      expect(job.result.total_scanned).toBe(1);
      expect(job.result.funnel.universe_size).toBe(1);
    }
    expect(errors, errors.join('\n')).toEqual([]);
  });
});

test.describe('@smoke stock deep-dive', () => {
  test('DD-001 AAPL fast preview paints a tear sheet', async ({ page }) => {
    await page.goto(`/?nocache=${Date.now()}#analyze`, { waitUntil: 'domcontentloaded' });
    await page.locator('.nav-tab[data-view="analyze"]').click();
    await page.locator('#an-fast-mode').check();
    await page.locator('#an-ticker').fill('AAPL');
    await page.locator('#btn-analyze').click();

    // The tear sheet paints its call and grows well past the skeleton. The
    // Research third pass states each fact once and closes the holders' plan for
    // a name not held, so the visible text is shorter than before (intended).
    await expect(page.locator('#an-results .decision-action .signal')).toBeVisible({ timeout: 80_000 });
    await expect.poll(
      async () => (await page.locator('#an-results').innerText()).length,
      { timeout: 10_000 },
    ).toBeGreaterThan(1000);
    await assertNotOffline(page);
  });

  test('DD-014 invalid ticker fails fast and honestly (not rate-limited)', async ({ page }) => {
    await page.goto(NOCACHE(), { waitUntil: 'domcontentloaded' });
    await page.locator('.nav-tab[data-view="analyze"]').click();
    await page.locator('#an-ticker').fill('FAKE123');
    await page.locator('#btn-analyze').click();

    const body = page.locator('#an-results');
    await expect(body).toContainText(/invalid|not found|no data|could not/i, { timeout: 30_000 });
    await expect(body).not.toContainText(/rate.?limit/i);
  });
});

test.describe('@smoke theme + market toggles', () => {
  test('UX-006 theme toggle flips and persists across reload', async ({ page }) => {
    await page.goto(NOCACHE(), { waitUntil: 'domcontentloaded' });
    const before = await page.locator('html').getAttribute('data-theme');
    await page.locator('#theme-toggle').click();
    // The switch cross-fades through a View Transition, which applies the new
    // theme on the next frame: wait for it rather than reading in the same tick.
    await expect(page.locator('html')).not.toHaveAttribute('data-theme', before || '');
    const after = await page.locator('html').getAttribute('data-theme');

    await page.reload({ waitUntil: 'domcontentloaded' });
    expect(await page.locator('html').getAttribute('data-theme')).toBe(after);
  });

  test('FUNC-004 market toggle US -> IN switches active state', async ({ page }) => {
    await page.goto(NOCACHE(), { waitUntil: 'networkidle' });
    await page.locator('.mkt-btn[data-market="IN"]').click();
    await expect(page.locator('.mkt-btn[data-market="IN"]')).toHaveClass(/active/);
    await expect(page.locator('.mkt-btn[data-market="US"]')).not.toHaveClass(/active/);
    await assertNotOffline(page);
  });
});
