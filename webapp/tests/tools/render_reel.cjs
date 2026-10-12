#!/usr/bin/env node
/**
 * Render the "How QUANTA works" reel to a WebM, frame by frame.
 *
 * The reel is one Web Animations timeline (bindGuideReel in static/app.js),
 * so every frame is exact: seek, screenshot, pipe. No screen recording, no
 * dropped frames. Uses Playwright's Chromium and the ffmpeg build that
 * Playwright installs for its own video recorder (VP8 + MJPEG input).
 *
 *   BASE_URL=http://127.0.0.1:5617 node tests/tools/render_reel.cjs
 *   env: OUT (default static/img/guide/quanta-reel.webm), FPS (30), WIDTH (1280)
 */
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5500';
const OUT = process.env.OUT || path.join(__dirname, '..', '..', 'static', 'img', 'guide', 'quanta-reel.webm');
const FPS = Number(process.env.FPS || 30);
const W = Number(process.env.WIDTH || 1280);
const H = Math.round((W * 9) / 16);

function findFfmpeg() {
  if (process.env.FFMPEG) return process.env.FFMPEG;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH
    || (process.platform === 'win32' ? path.join(os.homedir(), 'AppData', 'Local', 'ms-playwright')
      : process.platform === 'darwin' ? path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright')
        : path.join(os.homedir(), '.cache', 'ms-playwright'));
  const dir = fs.readdirSync(root).filter((d) => d.startsWith('ffmpeg')).sort().pop();
  if (!dir) throw new Error(`no ffmpeg under ${root}; run "npx playwright install ffmpeg"`);
  const bin = fs.readdirSync(path.join(root, dir)).find((f) => f.startsWith('ffmpeg'));
  return path.join(root, dir, bin);
}

(async () => {
  const browser = await chromium.launch();
  // Reduced motion keeps the page still and turns autoplay off; the reel's
  // own timeline still seeks, which is all the renderer needs.
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, reducedMotion: 'reduce', colorScheme: 'dark' });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/?render=${Date.now()}#guide`, { waitUntil: 'load' });
  await page.waitForFunction(() => window.QuantaReel);
  await page.evaluate(() => document.fonts.ready);
  // Pin the stage to the viewport so nothing on the page can clip it.
  await page.addStyleTag({ content: `.hw-reel-stage{position:fixed!important;left:0!important;top:0!important;z-index:2147483647!important;width:${W}px!important;height:${H}px!important;max-width:none!important;border-radius:0!important;box-shadow:none!important}.hw-reel-cover{display:none!important}` });
  const clip = { x: 0, y: 0, width: W, height: H };

  const ffmpeg = spawn(findFfmpeg(), [
    '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', 'pipe:0',
    '-loglevel', 'error', '-c:v', 'libvpx', '-b:v', '3M', '-crf', '8', '-qmin', '2', '-qmax', '30', '-deadline', 'good', '-cpu-used', '1',
    '-pix_fmt', 'yuv420p', '-an', OUT,
  ], { stdio: ['pipe', 'ignore', 'inherit'] });
  const done = new Promise((res, rej) => ffmpeg.on('close', (c) => (c === 0 ? res() : rej(new Error(`ffmpeg exited ${c}`)))));

  const duration = await page.evaluate(() => window.QuantaReel.duration);
  const frames = Math.round((duration / 1000) * FPS) + 1;
  for (let i = 0; i < frames; i += 1) {
    const t = Math.min(duration, (i * 1000) / FPS);
    await page.evaluate((v) => { window.QuantaReel.pause(); window.QuantaReel.seek(v); }, t);
    const jpg = await page.screenshot({ type: 'jpeg', quality: 95, clip });
    if (!ffmpeg.stdin.write(jpg)) await new Promise((r) => ffmpeg.stdin.once('drain', r));
    if (i % 60 === 0) process.stdout.write(`frame ${i}/${frames}\n`);
  }
  ffmpeg.stdin.end();
  await done;
  await browser.close();
  const kb = Math.round(fs.statSync(OUT).size / 1024);
  console.log(`wrote ${OUT} (${frames} frames, ${W}x${H}, ${FPS} fps, ${kb} KB)`);
})().catch((e) => { console.error(e); process.exit(1); });
