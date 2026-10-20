const test = require('node:test');
const assert = require('node:assert/strict');

const {
  fmtPct,
  upsidePct,
  computeRR,
  rrVerdict,
  splitHeadlinesByRelevance,
  computeRequiredSectionAudit,
  buildValidationIssues,
  normalizeVolumeRequirement,
  actionMeta,
  finalAllocation,
  isFundedPick,
  renderPriceChartCard,
  scanStatusMessage,
  emptyScanExplanationHtml,
  errorTypeLabel,
  tearSheetRateLimitMessage,
  tearSheetCacheBannerHtml,
  tearSheetFastModeBannerHtml,
  assetTypeMeta,
  assetApplicability,
  assetCaveatBannerHtml,
  dataQualityGate,
  dataQualityFailBannerHtml,
  summarizeValidationIssues,
  classifyFinalAction,
  whyNotBuyHtml,
  friendlyFetchError,
  computeRegimeModel,
  factorState,
  strategistBrief,
  signalCheckCounts,
  confidenceRead,
} = require('../static/app.js');

test('percentage math uses current and target prices', () => {
  const pct = upsidePct(310.59, 308.82);
  assert.ok(Math.abs(pct - 0.573) < 0.05);
  assert.equal(fmtPct(pct, 1, false), '+0.6%');
  assert.equal(fmtPct(0.7, 1, true), '+70.0%');
  assert.equal(fmtPct(0.007, 1, true), '+0.7%');
});

test('risk reward calculates when valid fallback stop exists', () => {
  const rr = computeRR(308.82, 310.59, 296.94);
  assert.ok(rr > 0);
  const verdict = rrVerdict(308.82, 310.59, 296.94);
  assert.ok(verdict);
  assert.match(verdict.text, /Unfavorable|Acceptable|Favorable/);
});

test('required-section audit flags missing sections', () => {
  const audit = computeRequiredSectionAudit(
    { ticker: 'AAPL', confidence_score: null, beginner_guidance: null, portfolio_outlook: null },
    { currentPrice: 308.82, recommendation: '', baseTgt: null, bearTgt: null, bullTgt: null, entryP: 308.82 },
  );
  assert.ok(audit.missing.includes('Recommendation'));
  assert.ok(audit.missing.includes('Confidence'));
  assert.ok(audit.missing.includes('Price Targets'));
  assert.ok(audit.missing.includes('Beginner Guidance'));
  assert.ok(audit.missing.includes('Portfolio Outlook'));
});

test('data quality FAIL when required sections are missing', () => {
  const issues = buildValidationIssues({
    current_price: 100,
    trade_setup: { target_base: 110, stop_loss: 0 },
    beginner_guidance: {},
    market_intel: { analyst_rating: 'BUY', buy_ratings: 0, hold_ratings: 0, sell_ratings: 0 },
    monte_carlo: {},
  }, { requiredMissing: ['Recommendation', 'Portfolio Outlook'] });

  assert.ok(issues.some((i) => i.severity === 'FAIL' && /Required sections missing/.test(i.text)));
});

test('data quality WARN when primary stop is missing but fallback stop exists', () => {
  const issues = buildValidationIssues({
    current_price: 100,
    signal: 'BUY',
    trade_setup: { target_base: 112, stop_loss: 0, entry_price: 100 },
    beginner_guidance: { existing_holder_stop: 95, pullback_stop: 0 },
    market_intel: {},
    monte_carlo: {},
  });

  assert.ok(issues.some((i) => i.severity === 'WARN' && /falling back/.test(i.text)));
  assert.ok(!issues.some((i) => i.severity === 'FAIL' && /No valid stop-loss/.test(i.text)));
});

test('news filtering keeps strict ticker-specific headlines only', () => {
  const split = splitHeadlinesByRelevance([
    'AAPL launches new iPhone with on-device AI features',
    'Apple: Services revenue grows again in quarter',
    'Despite Its Recent Partnership with Apple, MP Materials Stock Is Down. Here is Why.',
    'Intel plans new foundry expansion in Arizona',
  ], 'AAPL', 'Apple Inc.');

  assert.ok(split.tickerNews.length >= 1);
  assert.ok(split.marketContext.some((h) => /MP Materials|Intel/.test(h)));
});

test('volume requirement symbols normalize to ASCII >= format', () => {
  assert.equal(normalizeVolumeRequirement('!" 1.5x avg on entry day'), '>= 1.5x avg');
  assert.equal(normalizeVolumeRequirement('>= 1.5x avg on entry day'), '>= 1.5x avg');
});

test('final_action is the source of truth for the displayed action', () => {
  // Scanner said BUY but deep confirmation downgraded to WATCHLIST.
  const meta = actionMeta({ signal: 'BUY', final_action: 'WATCHLIST' });
  assert.equal(meta.label, 'WATCHLIST');
  assert.equal(meta.funded, false);
  assert.equal(meta.tone, 'warn');
});

test('display_action and underscore variants resolve correctly', () => {
  assert.equal(actionMeta({ final_action: 'STRONG_BUY' }).label, 'STRONG BUY');
  assert.equal(actionMeta({ final_action: 'HOLD_EXISTING' }).label, 'HOLD EXISTING');
  assert.equal(actionMeta({ display_action: 'ACCUMULATE' }).funded, true);
});

test('finalAllocation prefers the post-confirmation value', () => {
  assert.equal(finalAllocation({ allocation: 1000, allocation_after: 0 }), 0);
  assert.equal(finalAllocation({ allocation: 1000 }), 1000);
  assert.equal(finalAllocation({ allocation_after: 500, allocation: 1000 }), 500);
});

test('a downgraded WATCHLIST pick with zero allocation is not funded', () => {
  const watch = { final_action: 'WATCHLIST', allocation: 0, allocation_after: 0 };
  assert.equal(isFundedPick(watch), false);
});

test('a BUY pick with positive allocation is funded', () => {
  const buy = { final_action: 'BUY', allocation: 800, allocation_after: 800 };
  assert.equal(isFundedPick(buy), true);
});

test('a fundable action with zero allocation is excluded from funded set', () => {
  // ACCUMULATE is fundable, but if reconciliation zeroed the allocation it must
  // not count toward portfolio totals.
  const acc = { final_action: 'ACCUMULATE', allocation_after: 0, allocation: 500 };
  assert.equal(isFundedPick(acc), false);
});

test('portfolio totals only sum funded picks', () => {
  const picks = [
    { final_action: 'BUY', allocation_after: 1000 },
    { final_action: 'WATCHLIST', allocation_after: 0 },
    { final_action: 'ACCUMULATE', allocation_after: 500 },
    { final_action: 'AVOID', allocation_after: 0 },
  ];
  const funded = picks.filter(isFundedPick);
  const deployed = funded.reduce((a, p) => a + finalAllocation(p), 0);
  assert.equal(funded.length, 2);
  assert.equal(deployed, 1500);
});

// --------------------------------------------------------------------------
// Price-chart rendering / theme-data binding
// --------------------------------------------------------------------------
const sampleChart = {
  points: [
    { date: '2025-12-01', close: 185.0 },
    { date: '2026-01-15', close: 198.4 },
    { date: '2026-03-01', close: 210.2 },
    { date: '2026-05-27', close: 222.36 },
  ],
};

test('chart badge shows the latest close (not the target) with a clear caption', () => {
  const html = renderPriceChartCard(sampleChart, {
    ticker: 'NVDA', currentPrice: 222.13, target: 262.35, stop: 196.94, sym: '$',
  });
  // The top-right badge is the LATEST close + 6M change, explicitly labelled so
  // it is never mistaken for the analyst target.
  assert.match(html, /chart-return-cap">Close [^<]+ · 6M</);
  assert.match(html, /<strong>\$222\.36<\/strong>/);
  // Target must NOT be the value rendered inside the badge strong element.
  assert.doesNotMatch(html, /<strong>\$262\.35<\/strong>/);
});

test('chart binds current and target to the correct labelled levels (not swapped)', () => {
  const html = renderPriceChartCard(sampleChart, {
    ticker: 'NVDA', currentPrice: 222.13, target: 262.35, stop: 196.94, sym: '$',
  });
  // On-chart level labels carry the value so Target/Now are self-describing.
  assert.match(html, /Target \$262\.35/);
  assert.match(html, /Now \$222\.13/);
  // Legend pills mirror the same values.
  assert.match(html, /<span>Target \$262\.35<\/span>/);
  assert.match(html, /<span>Now \$222\.13<\/span>/);
  // Guard against a swap: the Target label must never be paired with the
  // current price, nor Now with the target price.
  assert.doesNotMatch(html, /Target \$222\.13/);
  assert.doesNotMatch(html, /Now \$262\.35/);
});

test('chart vertical scale is price-driven so a far target does not compress the line', () => {
  const html = renderPriceChartCard(sampleChart, {
    ticker: 'NVDA', currentPrice: 222.13, target: 262.35, stop: 196.94, sym: '$',
  });
  // Extract the polyline y-coordinates; the price line should span a healthy
  // fraction of the 260px tall viewBox rather than sitting in a low sliver.
  const m = html.match(/class="chart-line[^"]*" points="([^"]+)"/);
  assert.ok(m, 'polyline present');
  const ys = m[1].split(' ').map((p) => Number(p.split(',')[1]));
  const range = Math.max(...ys) - Math.min(...ys);
  assert.ok(range > 120, `price line should fill the chart vertically, got ${range}`);
});

test('chart degrades to an empty state when fewer than two valid points exist', () => {
  const html = renderPriceChartCard({ points: [{ date: '2026-01-01', close: 100 }] }, {
    ticker: 'NVDA', currentPrice: 100, target: 120, stop: 90, sym: '$',
  });
  assert.match(html, /Price chart unavailable/);
  assert.doesNotMatch(html, /<svg/);
});


// --------------------------------------------------------------------------
// [PROVIDER-RESILIENCE] Provider rate-limit scan-status messaging
// --------------------------------------------------------------------------
test('scanStatusMessage surfaces an honest provider rate-limit message', () => {
  const msg = scanStatusMessage({
    scan_status: 'provider_rate_limited',
    provider_rate_limited_count: 18,
    analysis_failed_count: 20,
    error_summary: { universe_size: 200 },
    provider_status: { cooldown_remaining_seconds: 42 },
  });
  assert.ok(msg);
  assert.equal(msg.tone, 'bear');
  assert.equal(msg.rateLimited, true);
  assert.match(msg.title, /rate-limited/i);
  assert.match(msg.title, /18 failures out of 200/);
  assert.match(msg.body, /yfinance/);
  assert.match(msg.body, /42s/);
  assert.match(msg.body, /safe mode/i);
});

test('scanStatusMessage marks rate-limited partial scans but still keeps picks', () => {
  const msg = scanStatusMessage({
    scan_status: 'partial_with_picks',
    provider_rate_limited_count: 12,
    analysis_failed_count: 15,
    analyzed_success_count: 50,
    error_summary: { universe_size: 65 },
  });
  assert.ok(msg);
  assert.equal(msg.tone, 'warn');
  assert.equal(msg.rateLimited, true);
  assert.match(msg.title, /12 rate-limited/);
});

test('empty scan explanation shows backend diagnostics without HTML injection', () => {
  const html = emptyScanExplanationHtml({
    empty_scan_explanation: 'Analyzed 4 of 5 names; 1 failed.\nFilter: <unsafe>',
  });
  assert.match(html, /Analyzed 4 of 5/);
  assert.match(html, /white-space:pre-line/);
  assert.match(html, /&lt;unsafe&gt;/);
  assert.doesNotMatch(html, /<unsafe>/);
});

test('errorTypeLabel has a friendly label for provider rate limiting', () => {
  assert.equal(errorTypeLabel('provider_rate_limited'), 'Provider Rate Limited');
});

// --------------------------------------------------------------------------
// [PROVIDER-RESILIENCE] Stock Deep-Dive / Tear Sheet rate-limit messaging
// --------------------------------------------------------------------------
test('tearSheetRateLimitMessage maps the structured /api/analyze error detail', () => {
  const m = tearSheetRateLimitMessage({
    status: 'provider_rate_limited',
    ticker: 'TSM',
    provider: 'yfinance',
    error_type: 'provider_rate_limited',
    error_message: 'Yahoo Finance is rate-limiting requests for TSM. Retry available in ~57s.',
    attempts: 3,
    retryable: true,
    cooldown_remaining_seconds: 57,
    last_successful_fetch: '2026-06-02T16:00:00',
    suggested_action: 'Wait, retry in safe mode, or use cached data.',
  });
  assert.equal(m.rateLimited, true);
  assert.equal(m.ticker, 'TSM');
  assert.equal(m.provider, 'yfinance');
  assert.equal(m.attempts, 3);
  assert.equal(m.retryable, true);
  assert.equal(m.cooldown, 57);
  assert.match(m.body, /rate-limiting requests for TSM/);
  assert.match(m.suggested, /safe mode/i);
});

test('tearSheetRateLimitMessage degrades gracefully with no detail', () => {
  const m = tearSheetRateLimitMessage(null);
  assert.equal(m.rateLimited, true);
  assert.equal(m.cooldown, 0);
  assert.equal(m.retryable, true);
  assert.match(m.body, /rate-limiting/i);
});

test('tearSheetCacheBannerHtml renders a cached-data banner only when cached', () => {
  const banner = tearSheetCacheBannerHtml({
    used_cached_data: true,
    provider_rate_limited: true,
    cache_timestamp: 1764691200, // fixed epoch seconds
  });
  assert.match(banner, /Using cached data/);
  assert.match(banner, /rate-limiting live requests/);
  // Non-cached tear sheets must not show the banner.
  assert.equal(tearSheetCacheBannerHtml({ used_cached_data: false }), '');
  assert.equal(tearSheetCacheBannerHtml({}), '');
});

test('tearSheetFastModeBannerHtml renders a preliminary banner only in fast mode', () => {
  const banner = tearSheetFastModeBannerHtml({ fast_mode: true, ticker: 'hal.ns' });
  assert.match(banner, /Fast preview/);
  assert.match(banner, /preliminary/i);
  // Offers a one-click reload that targets the same ticker (uppercased).
  assert.match(banner, /data-load-full-analysis="HAL.NS"/);
  // Non-fast (full) tear sheets must not show the banner.
  assert.equal(tearSheetFastModeBannerHtml({ fast_mode: false }), '');
  assert.equal(tearSheetFastModeBannerHtml({}), '');
});

test('assetTypeMeta classifies a common stock with no extra chips', () => {
  const m = assetTypeMeta({ ticker: 'AAPL', asset_type: 'common_stock' });
  assert.equal(m.assetType, 'common_stock');
  assert.equal(m.isEtf, false);
  assert.equal(m.primaryLabel, 'Common Stock');
  assert.equal(m.chips.length, 1);
  assert.equal(m.caveat, '');
});

test('assetTypeMeta flags a high-volatility common stock with an overlay chip', () => {
  const m = assetTypeMeta({ ticker: 'NVDA', asset_type: 'common_stock', is_high_volatility: true });
  assert.equal(m.assetType, 'common_stock');
  assert.equal(m.isHighVol, true);
  assert.ok(m.chips.some((c) => c.kind === 'highvol'));
});

test('assetTypeMeta classifies an ETF and marks single-company sections N/A', () => {
  const m = assetTypeMeta({ ticker: 'XLK', asset_type: 'etf', is_etf: true, quote_type: 'ETF' });
  assert.equal(m.assetType, 'etf');
  assert.equal(m.isEtf, true);
  assert.match(m.caveat, /not applicable/i);
  const ap = assetApplicability({ ticker: 'XLK', asset_type: 'etf', is_etf: true });
  assert.equal(ap.insider, 'na_etf');
  assert.equal(ap.dcf, 'na_etf');
  assert.equal(ap.piotroski, 'na_etf');
  assert.equal(ap.altman, 'na_etf');
  assert.equal(ap.companyThesis, 'na_etf');
});

test('assetTypeMeta classifies an ADR with a foreign-issuer caveat', () => {
  const m = assetTypeMeta({ ticker: 'TSM', asset_type: 'adr', is_adr: true, country: 'Taiwan' });
  assert.equal(m.assetType, 'adr');
  assert.equal(m.isAdr, true);
  assert.match(m.caveat, /ADR/);
  assert.match(m.caveat, /Taiwan/);
  const ap = assetApplicability({ ticker: 'TSM', asset_type: 'adr', is_adr: true });
  assert.equal(ap.insider, 'ok');
  assert.equal(ap.adrCaveat, true);
});

test('assetTypeMeta classifies a financial issuer with a multiples caveat', () => {
  const m = assetTypeMeta({ ticker: 'C', asset_type: 'financial', is_financial: true });
  assert.equal(m.assetType, 'financial');
  assert.equal(m.isFinancial, true);
  assert.match(m.caveat, /book value|net interest|capital ratios/i);
  const ap = assetApplicability({ ticker: 'C', asset_type: 'financial', is_financial: true });
  assert.equal(ap.valuationCaveat, true);
  assert.equal(ap.insider, 'ok');
});

test('assetTypeMeta handles an invalid ticker', () => {
  const m = assetTypeMeta({ ticker: 'FAKE123', asset_type: 'invalid', is_invalid: true });
  assert.equal(m.assetType, 'invalid');
  assert.equal(m.primaryLabel, 'Invalid');
});

test('assetCaveatBannerHtml renders only when a caveat applies', () => {
  assert.equal(assetCaveatBannerHtml({ asset_type: 'common_stock' }), '');
  const etf = assetCaveatBannerHtml({ asset_type: 'etf', is_etf: true });
  assert.match(etf, /ETF/);
  assert.match(etf, /plan-note/);
});

// [DQ-GATE] Data-quality / low-confidence display gate.
test('dataQualityGate stays inert when data quality passes', () => {
  const gate = dataQualityGate({ dataQualityFailed: false, confidenceScore: 80, riskGrade: 'B' });
  assert.equal(gate.failed, false);
  assert.equal(gate.provisionalHold, false);
  assert.equal(gate.overrideRecoLabel, null);
  assert.equal(gate.newBuyerAction, null);
  assert.equal(gate.existingHolderAction, null);
  assert.equal(gate.rejectionThesis, null);
  assert.equal(gate.metricsTone, null);
});

test('dataQualityGate produces a provisional HOLD when fail + low confidence', () => {
  const gate = dataQualityGate({ dataQualityFailed: true, confidenceScore: 42, riskGrade: 'F' });
  assert.equal(gate.failed, true);
  assert.equal(gate.lowConfidence, true);
  assert.equal(gate.provisionalHold, true);
  assert.equal(gate.overrideRecoLabel, 'NOT CONFIRMED: DATA QUALITY ISSUE');
  assert.equal(gate.buyNowAnswer, 'NO');
  assert.equal(gate.holdAnswer, 'Only with a strict stop-loss');
  assert.equal(gate.newBuyerAction, 'Avoid new entry until data quality passes.');
  assert.equal(gate.existingHolderAction, 'Hold only with strict stop-loss.');
  assert.equal(gate.metricsTone, 'muted');
});

test('dataQualityGate rejection thesis names the risk grade', () => {
  // [HOLD-INFLATION-FIX] Thesis now frames the issue as a data problem,
  // not a bearish signal, but still names the risk guardrail in play.
  const gate = dataQualityGate({ dataQualityFailed: true, confidenceScore: 30, riskGrade: 'F' });
  assert.match(gate.rejectionThesis, /Risk Grade F/);
  assert.match(gate.rejectionThesis, /Critical data missing/);
  assert.deepEqual(gate.whyReasons, ['data quality failed', 'low confidence', 'high risk']);
});

test('dataQualityGate falls back to generic guardrails when risk grade missing', () => {
  const gate = dataQualityGate({ dataQualityFailed: true, confidenceScore: 30 });
  assert.match(gate.rejectionThesis, /risk guardrails/);
});

test('dataQualityGate surfaces NOT_CONFIRMED even when confidence is high if critical DQ fails', () => {
  // [HOLD-INFLATION-FIX] Critical data failure should NEVER be silently
  // ignored just because the model is confident in its (broken) inputs.
  const gate = dataQualityGate({ dataQualityFailed: true, confidenceScore: 75, riskGrade: 'C' });
  assert.equal(gate.failed, true);
  assert.equal(gate.lowConfidence, false);
  assert.equal(gate.provisionalHold, false);
  assert.equal(gate.overrideRecoLabel, 'NOT CONFIRMED: DATA QUALITY ISSUE');
  assert.equal(gate.newBuyerAction, 'Avoid new entry until data quality passes.');
  assert.ok(gate.rejectionThesis);
});

test('dataQualityGate infers low confidence from a text level when score is absent', () => {
  const gate = dataQualityGate({ dataQualityFailed: true, confidenceLevel: 'Low' });
  assert.equal(gate.lowConfidence, true);
  assert.equal(gate.provisionalHold, true);
});

test('dataQualityFailBannerHtml renders only on failure', () => {
  assert.equal(dataQualityFailBannerHtml({ failed: false }), '');
  const html = dataQualityFailBannerHtml({ failed: true });
  // One line since the styling pass (intended change).
  assert.match(html, /Data quality failed: do not use this analysis for new trades/);
  assert.match(html, /role="alert"/);
});



// ===========================================================================
// [HOLD-INFLATION-FIX] New tests: critical-vs-optional split + classifier.
// ===========================================================================

test('buildValidationIssues marks missing analyst coverage as OPTIONAL WARN, not critical FAIL', () => {
  const data = {
    signal: 'BUY',
    trade_setup: { entry_price: 50, target_base: 60, stop_loss: 45, risk_reward_ratio: 2 },
    market_intel: { analyst_rating: 'STRONG_BUY', buy_ratings: 0, hold_ratings: 0, sell_ratings: 0 },
  };
  const issues = buildValidationIssues(data, { requiredMissing: [] });
  const analyst = issues.find((i) => i.code === 'analyst_data_unavailable');
  assert.ok(analyst, 'analyst-missing issue should be emitted');
  assert.equal(analyst.severity, 'WARN');
  assert.equal(analyst.kind, 'optional');
});

test('buildValidationIssues marks Monte-Carlo inconsistency as OPTIONAL WARN, not critical FAIL', () => {
  const data = {
    current_price: 100,
    signal: 'BUY',
    trade_setup: { entry_price: 100, target_base: 120, stop_loss: 90, risk_reward_ratio: 2 },
    // expected_return is the MEAN return in PERCENT (engine contract): -45% against
    // a mean price of 102 (+2%) is inconsistent. It is never compared with the median.
    monte_carlo: { median_price: 102, mean_price: 102, expected_return: -45, probability_profit: 0.5 },
  };
  const issues = buildValidationIssues(data, {});
  const mc = issues.find((i) => i.code === 'monte_carlo_inconsistent');
  assert.ok(mc, 'MC inconsistency issue should be emitted');
  assert.equal(mc.severity, 'WARN');
  assert.equal(mc.kind, 'optional');
});

test('a mean return that differs in sign from the median is not flagged (skewed distribution)', () => {
  const data = {
    current_price: 100, signal: 'BUY',
    trade_setup: { entry_price: 100, target_base: 120, stop_loss: 90, risk_reward_ratio: 2 },
    monte_carlo: { median_price: 98, mean_price: 100.6, expected_return: 0.6, probability_profit: 0.48 },
  };
  assert.equal(buildValidationIssues(data, {}).find((i) => i.code === 'monte_carlo_inconsistent'), undefined);
});

test('buildValidationIssues still marks no-stop-available as CRITICAL FAIL', () => {
  const data = { signal: 'BUY', trade_setup: { entry_price: 100, target_base: 110 } };
  const issues = buildValidationIssues(data, {});
  const stop = issues.find((i) => i.code === 'no_stop_loss');
  assert.ok(stop);
  assert.equal(stop.severity, 'FAIL');
  assert.equal(stop.kind, 'critical');
});

test('summarizeValidationIssues: optional-only fails do NOT trigger critical FAIL', () => {
  const issues = [
    { severity: 'WARN', kind: 'optional', code: 'analyst_data_unavailable', text: 'x' },
    { severity: 'WARN', kind: 'optional', code: 'monte_carlo_inconsistent', text: 'y' },
  ];
  const s = summarizeValidationIssues(issues);
  assert.equal(s.criticalStatus, 'PASS');
  assert.equal(s.dqOverride, 'WARN');
  assert.equal(s.optionalStatus, 'PARTIAL');
});

test('summarizeValidationIssues: critical FAIL drives dqOverride=FAIL', () => {
  const issues = [
    { severity: 'FAIL', kind: 'critical', code: 'no_stop_loss', text: 'x' },
  ];
  const s = summarizeValidationIssues(issues);
  assert.equal(s.criticalStatus, 'FAIL');
  assert.equal(s.dqOverride, 'FAIL');
});

test('classifyFinalAction: critical DQ fail returns NOT_CONFIRMED, not HOLD', () => {
  const data = { final_action: 'HOLD_EXISTING', scores: { composite_score: 60 }, risk: { risk_grade: 'C' }, trade_setup: { risk_reward_ratio: 2 } };
  const info = classifyFinalAction(data, { criticalStatus: 'FAIL', optionalStatus: 'PARTIAL', criticalFail: [{ text: 'no stop' }] });
  assert.equal(info.label, 'NOT_CONFIRMED');
  assert.match(info.headline, /data issue, not a bearish signal/i);
});

test('classifyFinalAction: Risk D + decent score + good R/R + ACCUMULATE → LOW_CONVICTION_ACCUMULATE', () => {
  const data = { final_action: 'ACCUMULATE', scores: { composite_score: 62 }, risk: { risk_grade: 'D' }, trade_setup: { risk_reward_ratio: 2 } };
  const info = classifyFinalAction(data, { criticalStatus: 'PASS', optionalStatus: 'PASS' });
  assert.equal(info.label, 'LOW_CONVICTION_ACCUMULATE');
});

test('classifyFinalAction: poor R/R fires a gate but does NOT relabel the engine verdict', () => {
  const data = { final_action: 'HOLD_EXISTING', scores: { composite_score: 70 }, risk: { risk_grade: 'C' }, trade_setup: { risk_reward_ratio: 1.1 } };
  const info = classifyFinalAction(data, { criticalStatus: 'PASS', optionalStatus: 'PASS' });
  assert.equal(info.label, 'HOLD_EXISTING');
  assert.ok(info.gates.some((g) => g.name === 'risk_reward_below_min'));
});

test('classifyFinalAction: engine WATCHLIST is NOT relabeled to HOLD even at Risk Grade F', () => {
  const data = { final_action: 'WATCHLIST', scores: { composite_score: 64 }, risk: { risk_grade: 'F' }, trade_setup: { risk_reward_ratio: 0 } };
  const info = classifyFinalAction(data, { criticalStatus: 'PASS', optionalStatus: 'PASS' });
  assert.equal(info.label, 'WATCHLIST');
  assert.ok(info.gates.some((g) => g.name === 'risk_grade_F'));
});

test('classifyFinalAction: engine ACCUMULATE at Risk Grade C stays ACCUMULATE', () => {
  const data = { final_action: 'ACCUMULATE', scores: { composite_score: 60 }, risk: { risk_grade: 'C' }, trade_setup: { risk_reward_ratio: 2.4 } };
  const info = classifyFinalAction(data, { criticalStatus: 'PASS', optionalStatus: 'PASS' });
  assert.equal(info.label, 'ACCUMULATE');
});

test('classifyFinalAction: Risk F + holding → HOLD_EXISTING_STRICT_STOP', () => {
  const data = { final_action: 'HOLD_EXISTING', scores: { composite_score: 50 }, risk: { risk_grade: 'F' }, trade_setup: { risk_reward_ratio: 2 } };
  const info = classifyFinalAction(data, { criticalStatus: 'PASS', optionalStatus: 'PASS' });
  assert.equal(info.label, 'HOLD_EXISTING_STRICT_STOP');
});

test('classifyFinalAction: clean BUY with no fired gates → returns BUY label, empty headline gates', () => {
  const data = { final_action: 'BUY', scores: { composite_score: 70 }, risk: { risk_grade: 'B' }, trade_setup: { risk_reward_ratio: 2.5 } };
  const info = classifyFinalAction(data, { criticalStatus: 'PASS', optionalStatus: 'PASS' });
  assert.equal(info.label, 'BUY');
  assert.equal(info.gates.length, 0);
});

test('whyNotBuyHtml: returns empty string for clean BUY with no gates', () => {
  const info = { label: 'BUY', gates: [], reasons: [] };
  assert.equal(whyNotBuyHtml(info, { criticalStatus: 'PASS', optionalStatus: 'PASS' }), '');
});

test('whyNotBuyHtml: renders Why-not-BUY card for NOT_CONFIRMED', () => {
  const info = {
    label: 'NOT_CONFIRMED',
    headline: 'Critical data missing — recommendation not confirmed. This is a data issue.',
    gates: [{ name: 'critical_data_failed', detail: 'no stop' }],
    reasons: ['Composite score 60 → candidate ACCUMULATE'],
  };
  const html = whyNotBuyHtml(info, { criticalStatus: 'FAIL', optionalStatus: 'PARTIAL' });
  assert.match(html, /Why not Buy\?/);
  assert.match(html, /Critical data: FAIL/);
  assert.match(html, /data-gate="critical_data_failed">Key data/);
  assert.match(html, /Overall score 60\/100 is in the Accumulate range/);
  assert.match(html, /data issue/);
});

test('friendlyFetchError: maps network-level failures to a backend-unreachable message', () => {
  for (const raw of ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.', 'Network request failed']) {
    const msg = friendlyFetchError(new Error(raw));
    assert.match(msg, /Cannot reach the QUANTA backend/);
    assert.match(msg, /server is running/);
  }
});

test('friendlyFetchError: passes through genuine application errors unchanged', () => {
  assert.equal(friendlyFetchError(new Error('Scan failed to start')), 'Scan failed to start');
  assert.equal(friendlyFetchError(new Error('HTTP 500')), 'HTTP 500');
});

test('friendlyFetchError: handles empty / non-Error inputs without throwing', () => {
  assert.equal(friendlyFetchError(null), 'Request failed');
  assert.equal(friendlyFetchError(new Error('')), 'Request failed');
});

test('strategistBrief: bullish broad tape reads as an investor\u2019s market', () => {
  const b = strategistBrief({
    postureTone: 'bull', posture: 'Offensive', count: 4,
    alignment: { support: ['Trend', 'Participation', 'Volatility'], oppose: [], neutral: [], bull: 3, bear: 0 },
    vix: 14, breadth: 80, highImpact: 0, earningsCount: 0,
  });
  assert.match(b.marketType.label, /Investor/);
  assert.match(b.lead, /earned offense/);
  for (const k of ['lead', 'marketType', 'riskHidden', 'favored', 'watch']) assert.ok(b[k], `${k} present`);
  assert.ok(b.marketType.text && b.riskHidden.text && b.favored.text && b.watch.text);
});

test('strategistBrief: bullish narrow tape flags concentration as the hidden risk', () => {
  const b = strategistBrief({
    postureTone: 'bull', posture: 'Selective Offensive', count: 4,
    alignment: { support: ['Trend'], oppose: ['Volatility'], neutral: ['Credit/Rates'], bull: 1, bear: 0 },
    vix: 18, breadth: 38, highImpact: 0, earningsCount: 0,
  });
  assert.match(b.marketType.label, /Stock-picker/);
  assert.match(b.riskHidden.text, /Concentration/);
  assert.match(b.watch.text, /Volatility/); // opposing factor surfaces as today's watch
});

test('strategistBrief: neutral split tape asks for patience; bear tape is defensive', () => {
  const neutral = strategistBrief({
    postureTone: 'neutral', posture: 'Neutral', count: 4,
    alignment: { support: [], oppose: [], neutral: ['Trend', 'Volatility'], bull: 1, bear: 1 },
    vix: 17, breadth: 50, highImpact: 0, earningsCount: 0,
  });
  assert.match(neutral.marketType.label, /Two-way/);
  assert.match(neutral.lead, /patience/);

  const bear = strategistBrief({
    postureTone: 'bear', posture: 'Defensive', count: 4,
    alignment: { support: ['Trend', 'Participation'], oppose: [], neutral: [], bull: 0, bear: 3 },
    vix: 28, breadth: 20, highImpact: 4, earningsCount: 2,
  });
  assert.match(bear.marketType.label, /Defensive/);
  assert.match(bear.favored.text, /A-quality|reversal/i);
  assert.match(bear.watch.text, /macro print/i); // event risk dominates the watch
});

test('strategistBrief: tolerates an empty model without throwing', () => {
  const b = strategistBrief({});
  assert.ok(b.lead && b.marketType.label && b.riskHidden.text && b.favored.text && b.watch.text);
});

// ---- Signal Check counts (decision-brief redesign) ----
// The tally follows the engine's per-factor status, independent of posture,
// so it partitions the factors and matches the API (see smoke DASH-003).
test('signalCheckCounts: buckets by factor score and always sums to the total', () => {
  const c = signalCheckCounts({
    postureTone: 'bull',
    factors: [{ score: 1 }, { score: 2 }, { score: 0 }, { score: -1 }, { score: -2 }],
  });
  assert.deepEqual(c, { confirmed: 2, neutral: 1, warning: 1, opposed: 1, total: 5 });
});

test('signalCheckCounts: a defensive posture does not relabel bearish factors as confirming', () => {
  const c = signalCheckCounts({
    postureTone: 'bear',
    factors: [{ score: -2 }, { score: -2 }, { score: 1 }],
  });
  assert.equal(c.confirmed, 1, 'only the positive factor confirms');
  assert.equal(c.opposed, 2);
  assert.equal(c.warning, 0, 'a -2 is counted once, as opposed');
  assert.equal(c.confirmed + c.neutral + c.warning + c.opposed, c.total);
});

test('signalCheckCounts: empty model is safe', () => {
  const c = signalCheckCounts({});
  assert.deepEqual(c, { confirmed: 0, neutral: 0, warning: 0, opposed: 0, total: 0 });
});

test('confidenceRead: maps score to label + tone, and is null-safe', () => {
  assert.deepEqual(confidenceRead({ confidenceScore: 80 }), { score: 80, label: 'High', tone: 'bull', withheld: false });
  assert.deepEqual(confidenceRead({ confidenceScore: 40 }), { score: 40, label: 'Low', tone: 'warn', withheld: false });
  assert.deepEqual(confidenceRead({ confidenceScore: 60 }), { score: 60, label: 'Moderate', tone: 'neutral', withheld: false });
  const none = confidenceRead({});
  assert.equal(none.score, undefined);
  assert.equal(none.label, null);
});

test('computeRegimeModel consumes canonical backend market_regime_engine', () => {
  const model = computeRegimeModel({
    tone: 'Neutral',
    tone_detail: 'legacy fallback',
    market_regime_engine: {
      marketRegime: 'Constructive - Selective',
      posture: 'Selective Offensive',
      modelSignal: 'Constructive but Selective',
      confidenceScore: 71,
      regimeScore: 5,
      factors: [
        { key: 'trend', label: 'Trend', score: 1, note: 'uptrend' },
        { key: 'volatility', label: 'Volatility', score: 0, note: 'normal' },
        { key: 'participation', label: 'Participation', score: 1, note: 'broad' },
        { key: 'rates_credit', label: 'Rates / Credit', score: -1, note: 'tightening' },
        { key: 'index_performance', label: 'Index Performance', score: 1, note: 'strong day' },
        { key: 'sector_leadership', label: 'Sector Leadership', score: 0, note: 'mixed' },
        { key: 'momentum', label: 'Momentum', score: 1, note: 'positive' },
        { key: 'macro_risk', label: 'Macro / Catalyst Risk', score: 0, note: 'contained' },
      ],
      primaryAction: {
        primary: 'Add in confirmed leaders',
        sizing: 'Normal size',
        avoid: 'Avoid chasing',
        risk: 'Use tight invalidation',
      },
      portfolioGuidance: {
        band: '65-95% gross',
        headline: 'Build exposure through confirmed setups',
        headlineTone: 'bull',
        actions: [{ tone: 'bull', text: 'Prioritize RS leaders' }],
      },
      outlookTriggers: {
        strengthens: 'Breadth expands above 75%',
        weakens: 'VIX spikes above 25',
      },
      dataFreshness: {
        stale: false,
      },
      marketHeadline: 'Constructive - Selective. Trend and breadth lean supportive.',
      newsImpactSummary: '1 high-impact bullish headline leads the tape.',
      newsImpact: { netDirection: 'bullish', highImpactCount: 1, bullishHighImpact: 1, bearishHighImpact: 0 },
      marketDrivers: [{ title: 'CPI cools', direction: 'bullish', impactLevel: 'High' }],
      catalystRisks: [{ title: 'FOMC ahead', category: 'macro', impactLevel: 'High' }],
      sectorLeadership: { label: 'Confirmed', tone: 'bull', note: 'broad leadership' },
      sourceHealth: { healthPct: 92, state: 'healthy' },
      overridesTriggered: [],
      dataConfidence: 90,
      debug: { scoreSum: 5, factorScores: { trend: 1 }, overridesTriggered: [] },
      shortSummary: 'constructive summary',
      detailedReasoning: 'full reasoning',
      timeframe: 'Swing horizon',
      colorTheme: { toneClass: 'bull' },
    },
  }, {}, { earnings_week: [] });

  assert.equal(model.posture, 'Selective Offensive');
  assert.equal(model.modelSignal, 'Constructive but Selective');
  assert.equal(model.label, 'Constructive - Selective');
  assert.equal(model.count, 8);
  assert.equal(model.net, 5);
  assert.equal(model.action.primary, 'Add in confirmed leaders');
  assert.equal(model.exposure.band, '65-95% gross');
  assert.equal(model.aggressiveTrigger, 'Breadth expands above 75%');
  assert.equal(model.confidenceScore, 71);
  // Canonical engine fields must flow through to the model the UI consumes.
  assert.match(model.headline, /Constructive - Selective/);
  assert.equal(model.newsImpactSummary, '1 high-impact bullish headline leads the tape.');
  assert.equal(model.marketDrivers.length, 1);
  assert.equal(model.sourceHealth.state, 'healthy');
  assert.equal(model.sectorLeadership.tone, 'bull');
  assert.ok(model.debug && typeof model.debug === 'object');
  assert.ok(Array.isArray(model.overrides));
});

test('computeRegimeModel surfaces backend overrides and uses a data-driven headline', () => {
  const model = computeRegimeModel({
    market_regime_engine: {
      marketRegime: 'Mixed Market',
      posture: 'Neutral',
      modelSignal: 'Balanced / Mixed',
      regimeScore: 1,
      factors: [{ key: 'volatility', label: 'Volatility', score: -2, note: 'VIX 31 stressed' }],
      primaryAction: { primary: 'Stay balanced' },
      portfolioGuidance: { band: '45-65% gross' },
      marketHeadline: 'Mixed Market. Risk signals are building — keep size light. Key driver: VIX spike.',
      overridesTriggered: ['VIX 31.0 > 25 — risk-on/offensive blocked (capped at Lean Defensive)'],
      colorTheme: { toneClass: 'warn' },
      dataFreshness: { stale: false },
    },
  }, {}, { earnings_week: [] });

  assert.equal(model.overrides.length, 1);
  assert.match(model.overrides[0], /VIX/);
  // The hero would use model.headline (backend), never a hardcoded bias string.
  assert.match(model.headline, /Mixed Market/);
  assert.equal(model.postureTone, 'neutral');
});

test('computeRegimeModel exposes low-confidence fields for the hero banner', () => {
  const model = computeRegimeModel({
    market_regime_engine: {
      marketRegime: 'Mixed / Cautious — Low Confidence',
      posture: 'Neutral',
      modelSignal: 'Balanced / Mixed',
      confidenceScore: 38,
      confidenceLabel: 'Low',
      lowConfidence: true,
      confidenceReasons: ['5 data input(s) missing or stale', 'News impact is mixed'],
      actionBrief: 'Trade smaller. Confirm before adding.',
      regimeScore: 0,
      factors: [{ key: 'trend', label: 'Trend', score: 0, note: 'flat' }],
      primaryAction: { primary: 'Keep risk balanced and wait for clearer confirmation.' },
      portfolioGuidance: { band: '30-55% gross', headlineTone: 'warn' },
      colorTheme: { toneClass: 'warn' },
      dataFreshness: { stale: false },
      sourceHealth: { state: 'degraded', healthPct: 60 },
    },
  }, {}, { earnings_week: [] });

  assert.equal(model.lowConfidence, true);
  assert.equal(model.confidenceLabel, 'Low');
  assert.equal(model.confidenceScore, 38);
  assert.equal(model.confidenceReasons.length, 2);
  assert.equal(model.actionBrief, 'Trade smaller. Confirm before adding.');
  assert.match(model.label, /Low Confidence/);
  // Low confidence must not present a green tone.
  assert.notEqual(model.postureTone, 'bull');
});

test('computeRegimeModel honors degraded freshness by tightening next review copy', () => {
  const model = computeRegimeModel({
    market_regime_engine: {
      marketRegime: 'Cautious Market',
      posture: 'Lean Defensive',
      modelSignal: 'Risk Caution',
      regimeScore: -3,
      factors: [
        { key: 'volatility', label: 'Volatility', score: -2, note: 'stressed' },
      ],
      primaryAction: { primary: 'Preserve capital' },
      portfolioGuidance: { band: '20-50% gross' },
      dataFreshness: { stale: true },
      colorTheme: { toneClass: 'warn' },
    },
  }, {}, { earnings_week: [] });

  assert.match(model.nextReview, /degraded/i);
  assert.equal(model.postureTone, 'neutral');
});

test('factorState marks -2 scores as Critical', () => {
  const s = factorState(-2);
  assert.equal(s.label, 'Critical');
  assert.equal(s.pips, 0);
  assert.equal(s.cls, 'warn');
});

// ---------------------------------------------------------------------------
// Conservative fallback: with no backend regime engine, the frontend must
// never print an offensive/risk-on read (no engine = no veto rules ran).
// ---------------------------------------------------------------------------
test('computeRegimeModel without engine caps a strongly bullish tape below Offensive', () => {
  const model = computeRegimeModel({
    tone: 'Risk-on',
    tone_factors: [
      { key: 'trend', label: 'Trend', score: 1, note: 'up' },
      { key: 'volatility', label: 'Volatility', score: 1, note: 'calm' },
      { key: 'breadth', label: 'Breadth', score: 1, note: 'broad' },
      { key: 'rates', label: 'Rates', score: 1, note: 'easing' },
    ],
  });
  assert.ok(!['Offensive', 'Selective Offensive'].includes(model.posture),
    `fallback posture must be capped, got ${model.posture}`);
  assert.equal(model.engineMissing, true);
  assert.equal(model.lowConfidence, true);
  assert.ok(model.confidenceReasons.length >= 1);
  // Guidance can never look confidently green on an unverified read.
  assert.notEqual(model.exposure.headlineTone, 'bull');
});

test('computeRegimeModel without engine leaves defensive reads defensive', () => {
  const model = computeRegimeModel({
    tone: 'Risk-off',
    tone_factors: [
      { key: 'trend', label: 'Trend', score: -2, note: 'down' },
      { key: 'volatility', label: 'Volatility', score: -1, note: 'stressed' },
      { key: 'breadth', label: 'Breadth', score: -1, note: 'weak' },
      { key: 'rates', label: 'Rates', score: 0, note: 'flat' },
    ],
  });
  // Conservative cap must not soften a bearish read.
  assert.match(model.posture, /Defensive/);
  assert.equal(model.exposure.headlineTone, 'bear');
});

test('computeRegimeModel with engine passes through backend posture and overrides', () => {
  const model = computeRegimeModel({
    market_regime_engine: {
      posture: 'Selective Offensive',
      marketRegime: 'Constructive — Selective',
      regimeScore: 5,
      colorTheme: { toneClass: 'bull' },
      factors: [{ key: 'trend', label: 'Trend', score: 1, note: 'up' }],
      overridesTriggered: ['VIX 26.0 > 25 — risk-on/offensive blocked (capped at Neutral)'],
      primaryAction: {}, portfolioGuidance: { band: '60-80% gross' },
    },
  });
  assert.equal(model.posture, 'Selective Offensive');
  assert.equal(model.engineMissing, undefined);
  assert.equal(model.overrides.length, 1);
  assert.match(model.overrides[0], /VIX/);
});

test('decision evidence line states the horizon and an unvalidated model plainly', () => {
  const { decisionEvidenceHtml } = require('../static/app.js');
  const html = decisionEvidenceHtml({
    label: 'BUY', action: 'BUY', qualifier: 'Unproven model',
    horizon: { label: 'Position (~3 months)' },
    evidence: { status: 'UNTESTED', status_text: 'Never validated', permits_recommendation: false },
    caveat: 'Unproven model: the scoring model has not been tested on past data yet, so treat this as a research idea, not a recommendation.',
  });
  assert.match(html, /Position \(~3 months\)/);
  assert.match(html, /Track record: <b class="warn" title="Never validated">Not yet tested/);
  assert.match(html, /data-evidence-status="UNTESTED"/);
  assert.match(html, /research idea, not a recommendation/);
  assert.equal(decisionEvidenceHtml(null), '');
});

test('the empty-scan filter counts read in words, with the minimum from config (audit U2)', () => {
  const html = emptyScanExplanationHtml({
    empty_scan_explanation: "No new-money candidates passed this scan's rules.\nFilter counts: score_below_min=85; zero_allocation=37; brand_new_filter=2",
  });
  assert.match(html, /Why names were screened out: score below the minimum 85 · sized to \$0 \(under the \$100 minimum position, or capped\) 37 · brand new filter 2/);
  assert.doesNotMatch(html, /score_below_min|zero_allocation/);
});
