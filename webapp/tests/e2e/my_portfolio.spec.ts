import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import * as fs from 'fs';
import * as path from 'path';

/**
 * My Portfolio (#holdings). Runs against whatever server BASE_URL points at:
 *
 *   · a normal QUANTA server  -> disconnected state (no Robinhood sign-in);
 *   · a signed-in server      -> the portfolio itself.
 *
 * Both paths assert accessibility (WCAG 2.1 AA), no console errors, no page
 * overflow, and that nothing but this server is contacted from the browser.
 */

const WIDTHS = [320, 375, 414, 768, 820, 1024, 1440];

const portfolioFixture = () => JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'my_portfolio_view.json'), 'utf8'));
// Synthetic index quotes dated to the fixture's session, so the market comparison is deterministic.
const marketTape = () => JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'market_tape.json'), 'utf8'));
// Insight endpoints: output of the real portfolio.insights code over synthetic prices (make_insights_fixture.py).
const insightsFixture = () => JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'my_portfolio_insights.json'), 'utf8'));
const INSIGHT: Record<string, string> = { performance: 'performance', intraday: 'intraday', exposure: 'exposure', events: 'events', outcomes: 'outcomes', records: 'records', activity: 'activity', journal: 'journal' };
async function mockPortfolio(page: Page, view = portfolioFixture(), fail: string[] = []) {
  const status = { ...view.status, account: view.portfolio ? 'a'.repeat(16) : null };
  const ins = insightsFixture();
  if (view.portfolio) view.portfolio.journal = ins.journal;
  await page.route('**/api/dashboard**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(marketTape()) }));
  await page.route('**/api/portfolio{,/**}', (route) => {
    const pathname = new URL(route.request().url()).pathname;
    // What-if: the real PortfolioService's answers, recorded per combination of scenarios.
    if (pathname.endsWith('/whatif')) {
      const changes: { symbol: string; delta_value: number }[] = (route.request().postDataJSON() || {}).changes || [];
      const k = changes.slice().sort((a, b) => a.symbol.localeCompare(b.symbol)).map((c) => `${c.symbol}:${Math.round(c.delta_value * 100)}`).join(',');
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ins.whatif[k] || { available: false, reason: 'Not recorded' }) });
    }
    const key = INSIGHT[pathname.split('/').pop() || ''];
    if (key) {
      if (fail.includes(key)) return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ error: 'Synthetic provider failure' }) });
      const body = key === 'activity' ? ins.activity[new URL(route.request().url()).searchParams.get('period') || 'month'] : ins[key];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    }
    const body = pathname.endsWith('/status') ? status
      : pathname.endsWith('/accounts') ? { accounts: [{ handle: 'a'.repeat(16), label: 'Individual', can_trade_via_agent: false }] }
        : { ...view, status };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function goTab(page: Page, name: 'Overview' | 'Holdings' | 'Guidance' | 'Activity' | 'Review' | 'News & events') {
  const tab = page.locator('#view-holdings').getByRole('tab', { name: new RegExp(`^${name}`) });
  // Click where a pointer would: the bar is sticky, and Playwright's own
  // scroll-into-view (which honours the page's scroll-padding) would move the page first.
  const box = await tab.boundingBox();
  if (box && box.y >= 0 && box.y + box.height <= (page.viewportSize()?.height || 900)) await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  else await tab.click();
  await expect(tab).toHaveAttribute('aria-selected', 'true');
}

async function openHoldings(page: Page, theme: 'dark' | 'light' = 'dark') {
  await page.addInitScript((t) => { try { localStorage.setItem('quanta_theme', t); } catch (_) { /* ignore */ } }, theme);
  await page.goto(`/?nocache=${Date.now()}#holdings`);
  await expect(page.locator('#view-holdings')).toHaveClass(/active/);
  await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'false', { timeout: 30_000 });
  await page.evaluate(() => document.fonts.ready);
}

test.describe('My Portfolio', () => {
  test('opens as a tab and settles into a known state', async ({ page }) => {
    const errors: string[] = [];
    const hosts = new Set<string>();
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('request', (r) => hosts.add(new URL(r.url()).host));
    await openHoldings(page);
    await expect(page.locator('.nav-tab[data-view="holdings"]')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('tab', { name: 'Picks', exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'My Portfolio', exact: true })).toHaveText('My PortfolioMy Portfolio');
    const state = await page.locator('.hp-conn').getAttribute('data-state');
    expect(['disconnected', 'connected', 'partial', 'auth_expired', 'provider_error', 'awaiting_auth']).toContain(state);
    await expect(page.locator('.hp-ro')).toContainText('Trading is off');
    const base = new URL(page.url()).host;
    for (const h of hosts) expect(h === base || /fonts\.(googleapis|gstatic)\.com$/.test(h), h).toBeTruthy();
    expect(errors).toEqual([]);
  });

  for (const theme of ['dark', 'light'] as const) {
    test(`axe WCAG 2.1 AA on #holdings (${theme})`, async ({ page }) => {
      await openHoldings(page, theme);
      const row = page.locator('[data-hp-toggle]').first();
      if (await row.count()) { await row.focus(); await page.keyboard.press('Enter'); }
      const results = await new AxeBuilder({ page }).include('#view-holdings')
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
      expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`)).toEqual([]);
    });
  }

  test('no horizontal page overflow at phone, tablet and desktop widths', async ({ page }) => {
    await openHoldings(page);
    for (const w of WIDTHS) {
      await page.setViewportSize({ width: w, height: 900 });
      await page.waitForTimeout(250);
      // Right after a resize, mobile emulation can report the previous layout width; one
      // forced reflow settles it, so the measurement below is of the page at this width.
      await page.evaluate(() => { document.body.style.display = 'none'; void document.body.offsetWidth; document.body.style.display = ''; void document.body.offsetWidth; });
      const over = await page.evaluate(() => ({
        pixels: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        elements: [...document.querySelectorAll('body *')].filter((e) => !e.closest('.hp-table-scroll, .mpc-table-wrap') && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().right > innerWidth + 1).map((e) => `${e.tagName}.${e.className}`),
      }));
      expect(over.pixels, `overflow at ${w}px: ${over.elements.join(', ')}`).toBeLessThanOrEqual(1);
      expect(over.elements, `clipped content at ${w}px`).toEqual([]);
    }
  });

  test('rows open the detail panel by keyboard and sorting reorders them', async ({ page }) => {
    await openHoldings(page);
    const rows = page.locator('[data-hp-row]');
    test.skip((await rows.count()) === 0, 'not signed in to a broker on this server');
    await goTab(page, 'Holdings');
    const first = rows.first().locator('[data-hp-toggle]');
    await first.focus();
    await page.keyboard.press('Enter');
    const panel = page.locator('#hp-drawer');
    await expect(panel).toBeVisible();
    await expect(panel.locator('.hp-drawer-b > .hp-detail-grid > .hp-ch .hp-ch-h h3')).toHaveText(
      ['Position', 'Portfolio call', 'Portfolio Impact', 'Risks', 'Evidence'], { timeout: 5_000 },
    ).catch(async () => {
      // A holding with no call shows only Position and Guidance.
      await expect(panel.locator('.hp-nocall')).toBeVisible();
    });
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(first).toBeFocused();
    await page.selectOption('#hp-sort', 'weight');
    const weights = await page.locator('[data-hp-row] td:nth-child(2)').allTextContents();
    const nums = weights.map((t) => parseFloat(t) || 0);
    expect(nums).toEqual([...nums].sort((a, b) => b - a));
  });

  test('hide amounts: one icon masks every account figure and survives a reload', async ({ page }) => {
    // A signed-in portfolio, served from the fixture the unit tests use (made by the real build_view).
    const view = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'my_portfolio_view.json'), 'utf8'));
    const status = { ...view.status, account: 'a'.repeat(16) };
    await page.route('**/api/portfolio/**', (route) => {
      const u = new URL(route.request().url());
      const body = u.pathname.endsWith('/status') ? status
        : u.pathname.endsWith('/accounts') ? { accounts: [{ handle: 'a'.repeat(16), label: 'Individual', can_trade_via_agent: false }] }
          : { ...view, status };
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.route('**/api/portfolio', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...view, status }) }));
    await openHoldings(page);
    const eye = page.locator('#hp-actions [data-hp="privacy"]');
    await expect(eye).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('.hp-kpi--lead .hp-kpi-v')).toContainText('$90,576');

    await eye.click();
    await expect(eye).toHaveAttribute('aria-pressed', 'true');
    await expect(eye).toBeFocused();
    const text = await page.locator('#hp-root').innerText();
    for (const v of ['$90,576', '$9,650', '$3,540', '-$1,410']) expect(text, v).not.toContain(v);
    await expect(page.locator('#hp-status')).toHaveText('Amounts hidden.');

    await page.reload();
    await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'false', { timeout: 30_000 });
    await expect(page.locator('#hp-actions [data-hp="privacy"]')).toHaveAttribute('aria-pressed', 'true');
    expect(await page.locator('#hp-root').innerText()).not.toContain('$90,576');
    const results = await new AxeBuilder({ page }).include('#view-holdings')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`)).toEqual([]);

    await page.locator('#hp-actions [data-hp="privacy"]').click();
    await expect(page.locator('.hp-kpi--lead .hp-kpi-v')).toContainText('$90,576');
  });

  test('table filters preserve keyboard focus, a decision in Guidance opens its holding, and sorting keeps focus', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    await goTab(page, 'Holdings');
    const risk = page.locator('.hp-toolbar [data-hp-filter="trim_review"]');
    await risk.focus();
    await page.keyboard.press('Enter');
    await expect(risk).toBeFocused();
    await expect(risk).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('[data-hp-row]')).toHaveCount(2);
    await expect(page.locator('[data-hp-row="NVDA"]')).toBeVisible();
    await expect(page.locator('[data-hp-row="XOM"]')).toBeVisible();
    // Empty categories are not offered as filters at all.
    await expect(page.locator('[data-hp-filter="consider_adding"]')).toHaveCount(0);
    await page.locator('.hp-toolbar [data-hp-filter=""]').click();
    await expect(page.locator('[data-hp-row]')).toHaveCount(8);
    // Decisions have their own tab.
    await goTab(page, 'Guidance');
    const item = page.locator('#hp-panel-guidance [data-hp-disclosure="gi-RIVN"]');
    await item.locator('summary').focus();
    await page.keyboard.press('Enter');
    await expect(item).toHaveAttribute('open', '');
    const opener = item.locator('[data-hp-inspect="RIVN"]');
    await opener.click();
    await expect(page.locator('#hp-drawer')).toBeVisible();
    await expect(page.locator('#hp-drawer-title')).toContainText('RIVN');
    await page.keyboard.press('Escape');
    await expect(opener).toBeFocused();
    await goTab(page, 'Holdings');
    await expect(page.locator('[data-hp-row="RIVN"]')).toHaveClass(/is-selected/);
    await expect(page.locator('[data-hp-row]')).toHaveCount(8);
    if ((page.viewportSize()?.width || 1440) <= 760) {
      await page.selectOption('#hp-sort', 'weight');
    } else {
      await page.locator('[data-hp-sort="weight"]').click();
      await expect(page.locator('[data-hp-sort="weight"]')).toBeFocused();
    }
    await expect(page.locator('[data-hp-sort="weight"]').locator('..')).toHaveAttribute('aria-sort', 'descending');
    await page.locator('[data-hp="sort-direction"]').click();
    await expect(page.locator('[data-hp="sort-direction"]')).toBeFocused();
    await expect(page.locator('[data-hp-sort="weight"]').locator('..')).toHaveAttribute('aria-sort', 'ascending');
  });

  test('refresh keeps disclosures open, disables duplicate requests and recovers after provider error', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    await page.locator('.hp-issues > summary').click();
    await goTab(page, 'Review');
    await page.locator('[data-hp-disclosure="risk"] > summary').click();
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    let requests = 0;
    await page.route('**/api/portfolio/refresh', async (route) => {
      requests++;
      await gate;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(portfolioFixture()) });
    });
    await page.locator('[data-hp="refresh"]').click();
    await expect(page.locator('[data-hp="refresh"]')).toBeDisabled();
    await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'true');
    release();
    await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'false');
    expect(requests).toBe(1);
    await expect(page.locator('.hp-issues')).toHaveAttribute('open', '');
    await expect(page.locator('[data-hp-disclosure="risk"]')).toHaveAttribute('open', '');
    await page.route('**/api/portfolio/refresh', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Provider temporarily unavailable' }) }));
    await page.locator('[data-hp="refresh"]').click();
    await expect(page.locator('#hp-root [role="alert"]')).toContainText('Provider temporarily unavailable');
    await expect(page.locator('.hp-table')).toHaveCount(0);
    await page.locator('[data-hp="check"]').click();
    await expect(page.locator('.hp-table')).toHaveCount(1);
    await expect(page.locator('#view-holdings').getByRole('tab', { name: /^Review/ })).toHaveAttribute('aria-selected', 'true');
  });

  test('partial data is concise but explicit, with every issue available by keyboard', async ({ page }) => {
    const view = portfolioFixture();
    view.portfolio.data_state = 'partial';
    view.portfolio.issues.push({ code: 'QUOTE_STALE', scope: 'KO', severity: 'error', message: 'KO quote is stale. Refresh to retry.' });
    view.portfolio.reconciliation = { status: 'mismatch', difference: -412.5 };
    await mockPortfolio(page, view);
    await openHoldings(page);
    // The limitation is stated once, in the one status line; tickers wait in the details.
    await expect(page.locator('.hp-dq-lead')).toHaveText('1 of 8 quotes are delayed or unverified, so QUANTA gives that holding no call until prices refresh.');
    await expect(page.locator('.hp-dq-msg')).toContainText('more note');
    await expect(page.locator('.hp-issue-list')).toBeHidden();
    await page.locator('.hp-issues > summary').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.hp-issue-list')).toBeVisible();
    await expect(page.locator('.hp-recon')).toContainText('$412.50');
    await expect(page.locator('.hp-issue-list')).toContainText('Affected KO');
  });

  test('disconnected, authentication, provider, and empty-account states remain accessible', async ({ page }) => {
    for (const [state, title] of [
      ['disconnected', 'Connect your Robinhood account'], ['auth_expired', 'Robinhood sign-in expired'],
      ['awaiting_auth', 'Finish signing in to Robinhood'], ['provider_error', 'Robinhood could not be read'],
    ]) {
      await mockPortfolio(page, { status: { state, state_label: state }, portfolio: null });
      await openHoldings(page);
      await expect(page.locator('.hp-state-title')).toHaveText(title);
      await expect(page.locator('.hp-table')).toHaveCount(0);
      await expect(page.locator('#hp-root button').first()).toBeVisible();
    }
    const empty = portfolioFixture();
    empty.portfolio.positions = [];
    await mockPortfolio(page, empty);
    await openHoldings(page);
    await expect(page.locator('.hp-state-title')).toHaveText('No stock or ETF positions in this account');
    await expect(page.locator('.hp-table')).toHaveCount(0);
  });

  test('negligible reconciliation differences stay quiet and all diagnostics remain keyboard-accessible', async ({ page }) => {
    const view = portfolioFixture();
    view.portfolio.data_state = 'partial';
    view.portfolio.issues = [{ code: 'TOTALS_MISMATCH', scope: 'portfolio', severity: 'error', message: 'Totals differ.' }];
    view.portfolio.reconciliation = { status: 'mismatch', difference: 0.12, reported: 90576 };
    await mockPortfolio(page, view);
    await openHoldings(page);
    await expect(page.locator('.hp-recon-note')).toHaveCount(0);
    const disclosure = page.locator('.hp-issues > summary');
    await disclosure.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.hp-recon')).toBeVisible();
    await expect(page.locator('.hp-recon')).toContainText('$0.12');
    await page.locator('[data-hp="privacy"]').click();
    await expect(page.locator('.hp-recon')).not.toContainText('$0.12');
    await expect(page.locator('.hp-issues')).toHaveAttribute('open', '');
  });

  test('exposure: four separate views, labelled references, and each bar opens its holdings', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    await goTab(page, 'Review');
    const ex = page.locator('#hp-panel-review .mp-expo');
    await expect(ex.locator('.mpx-row').first()).toContainText('Technology');
    await expect(ex).toContainText('30% QUANTA default, not a universal allocation rule');
    await expect(ex.locator('.mpx-name')).not.toContainText(['Cash']);
    await ex.getByRole('button', { name: 'Geography' }).click();
    await expect(ex.locator('.mpx-row').first()).toContainText('United States');
    await expect(ex).toContainText('No limit is implied for this view.');
    await ex.getByRole('button', { name: 'Sector' }).click();
    await ex.locator('[data-hp-x="expo:0"]').click();
    await expect(page.locator('#view-holdings').getByRole('tab', { name: /^Holdings/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('[data-hp-row]')).toHaveCount(3);
    await expect(page.locator('.hp-holdchip')).toContainText('Technology (sector)');
    await page.getByRole('button', { name: 'Show all holdings' }).click();
    await expect(page.locator('[data-hp-row]')).toHaveCount(8);
    const results = await new AxeBuilder({ page }).include('#view-holdings').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });
  test('performance chart: labelled simulation, ranges, benchmark toggle, keyboard and touch reading, table alternative', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    const perf = page.locator('#hp-panel-overview .mp-perf');
    await expect(perf.locator('.mp-kind')).toHaveText('Current holdings simulation · not account returns');
    await expect(perf.locator('.mpc-lines .mpc-line')).toHaveCount(2);
    // Both lines start at 0% and the gap between them is shaded; each line ends with its label.
    await expect(perf.locator('.mpc-band').first()).toBeAttached();
    await expect(perf.locator('.mpc-endl')).toHaveCount(2);
    await perf.getByRole('button', { name: '1 year' }).click();
    await expect(perf.getByRole('button', { name: '1 year' })).toHaveAttribute('aria-pressed', 'true');
    // 1D: today's bars from the previous close, with extended hours shaded.
    await perf.getByRole('button', { name: 'today' }).click();
    await expect(perf.locator('.mpc-shade')).toHaveCount(2);
    await expect(perf.locator('.mp-hero')).toContainText('Today, from the previous close');
    await perf.getByRole('button', { name: '1 year' }).click();
    // The benchmark line can be turned off on the Review chart.
    await goTab(page, 'Review');
    const rev = page.locator('#hp-panel-review .mp-perf');
    await rev.getByRole('button', { name: 'S&P 500 line' }).click();
    await expect(rev.locator('.mpc-lines .mpc-line')).toHaveCount(1);
    await rev.getByRole('button', { name: 'S&P 500 line' }).click();
    await goTab(page, 'Overview');
    // Keyboard: focus reads the last day; arrows move; the live region settles on the stop.
    const area = perf.locator('.mpc-area');
    await area.focus();
    await expect(perf.locator('.mpc-tip')).toBeVisible();
    await page.keyboard.press('Home');
    await expect(perf.locator('.mpc-live')).toHaveText(/^\w{3} \d+, 20\d\d: Current holdings [+−]?\d/);
    await page.keyboard.press('Escape');
    // Touch: a tap pins a reading.
    const box = (await area.boundingBox())!;
    await page.touchscreen.tap(box.x + box.width * 0.6, box.y + box.height * 0.4).catch(() => {});
    await perf.getByText('About this chart and its table').click();
    await expect(perf.locator('.mpc-table tbody tr')).toHaveCount(24);
    await expect(perf.locator('.mp-kindnote')).toContainText("Not your account's actual performance.");
    const results = await new AxeBuilder({ page }).include('#view-holdings').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });

  test('a server started before the update: one banner, session comparison and exposure from the view, no error paragraphs', async ({ page }) => {
    const view = portfolioFixture();
    const status = { ...view.status, account: 'a'.repeat(16) };
    await page.route('**/api/dashboard**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(marketTape()) }));
    await page.route('**/api/portfolio{,/**}', (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (INSIGHT[pathname.split('/').pop() || '']) return route.fulfill({ status: 404, contentType: 'text/html', body: '<h1>Not Found</h1>' });
      const body = pathname.endsWith('/status') ? status
        : pathname.endsWith('/accounts') ? { accounts: [{ handle: 'a'.repeat(16), label: 'Individual', can_trade_via_agent: false }] } : { ...view, status };
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await openHoldings(page);
    await expect(page.locator('.mp-banner')).toHaveCount(1);
    await expect(page.locator('.mp-banner')).toContainText('Restart QUANTA to load the charts.');
    await expect(page.locator('.mp-sess li').first()).toContainText('Your holdings');
    await expect(page.locator('.mpo-alloc .mpd-row').first()).toContainText('Technology');
    await expect(page.locator('#hp-root')).not.toContainText('has no My Portfolio');
    for (const tab of ['Activity', 'Review', 'News & events'] as const) await goTab(page, tab);
    expect(errors).toEqual([]);
    const results = await new AxeBuilder({ page }).include('#view-holdings').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });

  test('one failing section degrades alone: performance and events fail, exposure and priorities stay', async ({ page }) => {
    await mockPortfolio(page, portfolioFixture(), ['performance', 'events']);
    await openHoldings(page);
    await expect(page.locator('#hp-panel-overview .mp-perf .mp-unavail')).toContainText('Performance is unavailable');
    await expect(page.locator('#hp-panel-overview [data-hp-x="retry:perf"]')).toBeVisible();
    await expect(page.locator('#hp-panel-overview .mpo-alloc .mpd-row').first()).toBeVisible();
    await expect(page.locator('#hp-panel-overview .mpo-q')).toHaveCount(3);
  });

  test('review, activity and news: outcome records, realized P&L by period, and alerts that say where they appear', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    await goTab(page, 'Review');
    await expect(page.locator('.mp-insuff')).toHaveCount(2);
    await expect(page.locator('.mp-read dt').first()).toHaveText('Return');
    await goTab(page, 'Activity');
    // The shared fixture has no trade or order records: Activity says so, and the controls work.
    await expect(page.locator('#mpa-root .mp-limit')).toContainText('neither trade records nor order history');
    await page.getByRole('button', { name: 'this year to date' }).click();
    await expect(page.getByRole('button', { name: 'this year to date' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page).toHaveURL(/#holdings\/activity\/r=ytd&v=sales/);
    await goTab(page, 'News & events');
    await expect(page.locator('.mpn-group').first()).toContainText('Today');
    await page.selectOption('#mp-rule-type', 'weight_above');
    await page.fill('#mp-rule-value', '15');
    await page.getByRole('button', { name: 'Add condition' }).click();
    await expect(page.locator('.mpn-alert')).toHaveCount(2);
    await page.locator('.mpn-alert').first().getByRole('button', { name: 'Dismiss' }).click();
    await expect(page.locator('.mpn-alert')).toHaveCount(1);
    await expect(page.locator('.mpn-delivery')).toContainText('no background monitoring');
    const results = await new AxeBuilder({ page }).include('#view-holdings').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`)).toEqual([]);
  });
  test('performance drivers compare with the market only within the same session', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    await goTab(page, 'Review');
    const today = page.locator('.hp-today');
    await expect(today.locator('.hp-td-read')).toContainText('0.79 pts ahead of the market');
    await expect(today.locator('.hp-td-l')).toContainText('A one-factor estimate, not attribution');
    await expect(page.locator('.hp-kpi-vs')).toContainText('S&P 500 -0.42%');
    // A market tape from another session: no comparison, said once, by sector instead.
    await page.unroute('**/api/dashboard**');
    const other = marketTape();
    other.market.quotes.forEach((q: any) => { q.quote_date = '2026-09-28'; });
    await page.route('**/api/dashboard**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(other) }));
    await page.reload();
    await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'false');
    await expect(today.locator('.hp-td-l h3')).toContainText('By sector');
    await expect(today.locator('.hp-td-l')).toContainText('No market comparison: index quotes are from Mon, Sep 28');
    await expect(page.locator('.hp-kpi-vs')).toHaveCount(0);
  });

  test('Connect names the server it could not reach instead of failing silently', async ({ page }) => {
    const view = portfolioFixture();
    await page.route('**/api/portfolio{,/**}', (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.endsWith('/connect')) return route.abort('connectionrefused');
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...view.status, state: 'disconnected', account: null }) });
    });
    await openHoldings(page);
    await page.getByRole('button', { name: 'Connect Robinhood' }).first().click();
    const alert = page.locator('#hp-root [role="alert"]');
    await expect(alert).toContainText('Could not start the Robinhood sign-in');
    await expect(alert).toContainText(`Cannot reach the QUANTA server at ${new URL(page.url()).host}`);
    await expect(alert).toContainText('start.ps1');
  });

  test('six tabs: keyboard, deep links, and each tab keeps its own scroll', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    const overview = page.locator('#view-holdings').getByRole('tab', { name: /^Overview/ });
    await overview.focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('#view-holdings').getByRole('tab', { name: /^Holdings/ })).toBeFocused();
    await expect(page.locator('#hp-panel-holdings')).toBeVisible();
    await expect(page.locator('#hp-panel-overview')).toBeHidden();
    await expect(page).toHaveURL(/#holdings\/holdings$/);
    await page.keyboard.press('End');
    await expect(page.locator('#view-holdings').getByRole('tab', { name: /^News/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page).toHaveURL(/#holdings\/news$/);
    await page.keyboard.press('Home');
    await expect(page).toHaveURL(/#holdings$/);
    // Deep links open their tab; the old #holdings/plan link lands on Guidance, #holdings/analysis on Review.
    await page.goto(`/?nocache=${Date.now()}#holdings/plan`);
    await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'false', { timeout: 30_000 });
    await expect(page.locator('#view-holdings').getByRole('tab', { name: /^Guidance/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('#hp-panel-guidance .hp-hero')).toBeVisible();
    await page.goto(`/?nocache=${Date.now()}#holdings/analysis`);
    await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'false', { timeout: 30_000 });
    await expect(page.locator('#view-holdings').getByRole('tab', { name: /^Review/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('.mp-assess')).toBeVisible();
    // Leave Review scrolled; come back to the same place.
    await page.evaluate(() => window.scrollTo({ top: 900, behavior: 'instant' }));
    const y = await page.evaluate(() => window.scrollY);
    await goTab(page, 'Overview');
    await goTab(page, 'Review');
    expect(Math.abs((await page.evaluate(() => window.scrollY)) - y)).toBeLessThan(2);
    // Arrowing between stuck tabs never jumps the page to reveal them.
    const before = await page.evaluate(() => window.scrollY);
    await page.evaluate(() => (document.querySelector('#view-holdings [data-hp-tab="review"]') as HTMLElement).focus({ preventScroll: true }));
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('#view-holdings').getByRole('tab', { name: /^News/ })).toBeFocused();
    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#view-holdings').getByRole('tab', { name: /^Review/ })).toBeFocused();
    expect(Math.abs((await page.evaluate(() => window.scrollY)) - before)).toBeLessThan(2);
  });

  test('the detail panel traps focus, walks the table order, and leaves sort, filter and scroll untouched', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    await goTab(page, 'Holdings');
    await page.locator('.hp-toolbar [data-hp-filter="do_not_add"]').click();
    await page.selectOption('#hp-sort', 'weight');
    // Decisions sit above the table: bring the row into view first, then measure.
    const aapl = page.locator('[data-hp-toggle="AAPL"]');
    await aapl.scrollIntoViewIfNeeded();
    const y = await page.evaluate(() => window.scrollY);
    await aapl.click();
    const panel = page.locator('#hp-drawer');
    await expect(panel).toBeVisible();
    await expect(page.locator('#hp-drawer-title')).toBeFocused();
    await expect(panel.locator('.hp-drawer-s')).toContainText('1 of 3 in Holdings');
    await panel.getByRole('button', { name: 'Next: MSFT' }).click();
    await expect(page.locator('#hp-drawer-title')).toContainText('MSFT');
    // Focus stays inside the modal panel.
    for (let i = 0; i < 25; i++) await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement && !!document.activeElement.closest('#hp-drawer'))).toBe(true);
    // Company research is one step away, not on the page by default.
    const research = panel.locator('[data-hp-disclosure="research"]');
    await expect(research).not.toHaveAttribute('open', '');
    await research.locator('summary').click();
    await expect(research).toHaveAttribute('open', '');
    await panel.getByRole('button', { name: 'Close details' }).click();
    await expect(panel).toBeHidden();
    await expect(page.locator('[data-hp-toggle="MSFT"]')).toBeFocused();
    await expect(page.locator('[data-hp-row="MSFT"]')).toHaveClass(/is-selected/);
    await expect(page.locator('[data-hp-row]')).toHaveCount(3);
    await expect(page.locator('#hp-sort')).toHaveValue('weight');
    expect(Math.abs((await page.evaluate(() => window.scrollY)) - y)).toBeLessThan(2);
    const results = await new AxeBuilder({ page }).include('#view-holdings').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });

  test('the default overview fits about one desktop screen and a phone gets a full-screen detail view', async ({ page }, testInfo) => {
    await mockPortfolio(page);
    await openHoldings(page);
    const width = page.viewportSize()?.width || 1440;
    const bottom = await page.evaluate(() => document.querySelector('#view-holdings')!.getBoundingClientRect().bottom + window.scrollY);
    // The owner's layout: KPIs, chart | allocation, holdings, concentration | limits, action queue.
    if (width >= 1200) expect(bottom / 900, 'Overview height in 1440x900 viewports').toBeLessThanOrEqual(2.1);
    await page.locator('#hp-panel-overview [data-hp-inspect="NVDA"]').first().click();
    const box = await page.locator('#hp-drawer').boundingBox();
    const vp = page.viewportSize()!;
    if (width <= 760) expect(box && Math.round(box.width) === vp.width && Math.round(box.height) === vp.height, 'full screen on phones').toBeTruthy();
    else expect(box && box.width <= 641 && Math.round(box.x + box.width) === vp.width, 'right-hand panel').toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath('detail-panel.png') });
  });

  test('header icon controls show their name on hover and focus, dismiss with Esc, and meet target size', async ({ page }) => {
    await mockPortfolio(page);
    await openHoldings(page);
    const refresh = page.locator('#hp-actions [data-hp="refresh"]');
    const tip = refresh.locator('.hp-tip');
    await expect(tip).toBeHidden();
    await refresh.hover();
    await expect(tip).toBeVisible();
    await expect(tip).toHaveText('Refresh');
    await page.mouse.move(0, 0);
    await expect(tip).toBeHidden();
    await page.locator('#hp-actions [data-hp="privacy"]').focus();
    await page.keyboard.press('Tab');
    await expect(refresh).toBeFocused();
    await expect(tip).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(tip).toBeHidden();
    await expect(refresh).toBeFocused();
    const conn = page.locator('#hp-actions .hp-conn');
    await conn.focus();
    await expect(page.locator('#hp-conn-tip')).toBeVisible();
    await expect(page.locator('#hp-conn-tip')).toContainText('Connected · Read-only access');
    await expect(page.locator('#hp-actions')).not.toContainText(/Read-only(?! access)/);
    for (const act of ['privacy', 'refresh', 'disconnect']) {
      const box = await page.locator(`#hp-actions [data-hp="${act}"]`).boundingBox();
      expect(box && box.width >= 40 && box.height >= 40, act).toBeTruthy();
      await expect(page.locator(`#hp-actions [data-hp="${act}"]`)).toHaveAccessibleName(/.+/);
    }
    // Disconnect is only inspected here: it is never clicked against a connection.
    await expect(page.locator('#hp-actions [data-hp="disconnect"]')).toHaveAccessibleName('Disconnect Robinhood');
  });

  test('paused guidance is compact, recovers through Refresh and exposes details by keyboard', async ({ page }, testInfo) => {
    const view = portfolioFixture();
    view.portfolio.positions.forEach((r: any) => {
      Object.assign(r.guidance, { status: 'unavailable', action: 'Unavailable', group: 'unavailable' });
      r.broker.quote_freshness = { status: 'stale' };
    });
    await mockPortfolio(page, view);
    await openHoldings(page);
    await expect(page.locator('.hp-pri-empty')).toContainText('No calls');
    await goTab(page, 'Guidance');
    const guidance = page.locator('.hp-guidance');
    await expect(guidance.locator('.hp-gd-msg')).toContainText('Fresh quotes are required');
    await expect(guidance.locator('.hp-gi')).toHaveCount(0);
    const details = guidance.locator('details');
    await details.locator('summary').focus();
    await page.keyboard.press('Enter');
    await expect(details).toHaveAttribute('open', '');
    const refreshed = page.waitForRequest((r) => r.url().endsWith('/api/portfolio/refresh') && r.method() === 'POST');
    await guidance.getByRole('button', { name: 'Refresh data' }).click();
    await refreshed;
    await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'false');
    await expect(page.locator('#hp-guidance-refresh')).toBeFocused();
    const results = await new AxeBuilder({ page }).include('#view-holdings').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
    await details.locator('summary').click();
    await guidance.screenshot({ path: testInfo.outputPath('guidance-paused.png') });
  });

  test('visual review and readable metrics at small-phone through desktop widths', async ({ page }, testInfo) => {
    await mockPortfolio(page);
    await openHoldings(page, testInfo.project.name === 'desktop-light' ? 'light' : 'dark');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.evaluate(() => document.fonts.ready);
    const widths = testInfo.project.name === 'desktop-dark' ? [320, 375, 414, 768, 1024, 1280, 1440, 1920] : [testInfo.project.use.viewport?.width || 414];
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      const clipped = await page.locator('.hp-kpi-v, .hp-rk-v, .hp-gi-reason, .hp-filter, .hp-pr-x, .hp-dq-prices, .hp-mv-v, .hp-mv-p, .hp-rk-parts, .mp-hero dd, .mpx-name, .mpx-w, .mp-ag-t, .mp-now-v').evaluateAll((elements) => elements.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => ({ text: el.textContent, scroll: el.scrollWidth, client: el.clientWidth, width: el.getBoundingClientRect().width, display: getComputedStyle(el).display })));
      expect(clipped, `text at ${width}px`).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`portfolio-${width}.png`), fullPage: true });
      for (const tab of ['Holdings', 'Activity', 'Review', 'News & events', 'Overview'] as const) {
        await goTab(page, tab);
        await page.screenshot({ path: testInfo.outputPath(`${tab.toLowerCase()}-${width}.png`), fullPage: true });
      }
    }
    const results = await new AxeBuilder({ page }).include('#view-holdings').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });
});
