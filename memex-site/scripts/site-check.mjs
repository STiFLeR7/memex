/**
 * QA against the static export in out/. Run after `npm run build`:
 *   npm run site-check
 * Serves out/ locally and drives Google Chrome through Playwright.
 * Exits non-zero and lists every failure.
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const OUT = fileURLToPath(new URL('../out/', import.meta.url));
const TYPES = { '.webp': 'image/webp', '.png': 'image/png', '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.jpg': 'image/jpeg',
  '.mp4': 'video/mp4', '.woff2': 'font/woff2', '.txt': 'text/plain', '.xml': 'application/xml', '.ico': 'image/x-icon' };
const server = createServer(async (req, res) => {
  let path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  if (path.endsWith('/')) path += 'index.html';
  if (!extname(path)) path += '.html'; // /setup → setup.html, as Workers assets serve it
  try {
    const body = await readFile(join(OUT, normalize(path)));
    res.writeHead(200, { 'content-type': TYPES[extname(path)] || 'application/octet-stream' }).end(body);
  } catch { res.writeHead(404).end(await readFile(join(OUT, '404.html')).catch(() => '')); }
}).listen(0);
const BASE = `http://localhost:${server.address().port}/`;

const fails = [];
const check = (ok, msg) => { if (!ok) fails.push(msg); };
const browser = await chromium.launch({ channel: 'chrome' });

async function open(name, opts = {}, url = BASE) {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  page.on('console', m => m.type() === 'error' && fails.push(`${name}: console error: ${m.text()}`));
  page.on('pageerror', e => fails.push(`${name}: page error: ${e.message}`));
  page.on('requestfailed', r => !r.url().endsWith('.mp4') && !(opts.javaScriptEnabled === false && r.url().endsWith('.js')) && fails.push(`${name}: request failed: ${r.url()}`));
  page.on('response', r => r.status() >= 400 && fails.push(`${name}: HTTP ${r.status()} ${r.url()}`));
  await page.goto(url, { waitUntil: 'networkidle' });
  return { ctx, page };
}

// Layout: no horizontal overflow, headings, anchors.
for (const [w, h] of [[1440, 900], [390, 844]]) {
  const name = `${w}px`;
  const { ctx, page } = await open(name, { viewport: { width: w, height: h } });
  const r = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll('main *, nav *, footer *')].filter(el => {
      const b = el.getBoundingClientRect(), cs = getComputedStyle(el);
      return b.width && b.right > vw + 1 && cs.position !== 'absolute' && cs.position !== 'fixed' && !el.closest('.glass, .legend');
    }).map(el => el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''));
    const hs = [...document.querySelectorAll('h1,h2,h3,h4')].map(x => ({ l: +x.tagName[1], t: x.textContent.trim() }));
    const anchors = [...document.querySelectorAll('a[href^="#"]')].map(a => a.getAttribute('href')).filter(h => h.length > 1 && !document.querySelector(h));
    return { scroll: document.documentElement.scrollWidth - vw, wide: [...new Set(wide)].slice(0, 5), hs, anchors };
  });
  check(r.scroll <= 0, `${name}: page scrolls horizontally by ${r.scroll}px`);
  check(!r.wide.length, `${name}: elements overflow the viewport: ${r.wide.join(', ')}`);
  check(r.hs.filter(x => x.l === 1).length === 1, `${name}: expected exactly one h1`);
  check(r.hs.every(x => x.t), `${name}: empty heading`);
  r.hs.forEach((x, i) => i && check(x.l - r.hs[i - 1].l <= 1, `${name}: heading level skips to h${x.l} ("${x.t}")`));
  check(!r.anchors.length, `${name}: anchors with no target: ${r.anchors.join(', ')}`);
  await ctx.close();
}

// Reduced motion: everything rests on its final state.
{
  const { ctx, page } = await open('reduced', { viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  const r = await page.evaluate(() => ({
    hidden: [...document.querySelectorAll('.rv, .stmt')].filter(el => !el.classList.contains('in') || getComputedStyle(el).opacity !== '1').length,
    demo: ['s1', 's2', 's3'].every(c => document.getElementById('demo').classList.contains(c)),
    caption: document.querySelectorAll('#stages > li.on').length,
  }));
  check(!r.hidden, `reduced: ${r.hidden} reveal targets not in their final state`);
  check(r.demo, 'reduced: hero demo is not on its finished state');
  check(r.caption === 1, `reduced: ${r.caption} pipeline stages marked active, expected 1`);
  await ctx.close();
}

// JS off: the page is readable.
{
  const { ctx, page } = await open('no-js', { viewport: { width: 1440, height: 900 }, javaScriptEnabled: false });
  const r = await page.evaluate(() => ({
    invisible: [...document.querySelectorAll('h1, h2, .rv, main p')].filter(el => getComputedStyle(el).opacity !== '1').map(el => el.outerHTML.slice(0, 90)),
    stages: [...document.querySelectorAll('#stages > li')].filter(li => li.offsetHeight && li.querySelector('p').textContent.trim()).length,
    text: document.querySelector('main').innerText.length,
  }));
  check(!r.invisible.length, `no-js: invisible content: ${r.invisible.join(' | ')}`);
  check(r.stages === 6, `no-js: pipeline shows ${r.stages} of 6 stages`);
  check(r.text > 3000, `no-js: main has only ${r.text} characters of text`);
  await ctx.close();
}

// Inner pages: load clean (no console errors), one h1, no skipped headings, visible body text.
for (const path of ['setup', 'how-it-works', 'evaluation', 'changelog']) {
  for (const js of [true, false]) {
    const name = `/${path}${js ? '' : ' no-js'}`;
    const { ctx, page } = await open(name, { viewport: { width: 390, height: 844 }, javaScriptEnabled: js }, BASE + path);
    const r = await page.evaluate(() => {
      const hs = [...document.querySelectorAll('h1,h2,h3,h4')].map(x => +x.tagName[1]);
      return { h1: hs.filter(l => l === 1).length, skip: hs.some((l, i) => i && l - hs[i - 1] > 1), text: document.querySelector('main').innerText.length,
        scroll: document.documentElement.scrollWidth - document.documentElement.clientWidth, canon: document.querySelector('link[rel=canonical]')?.href };
    });
    check(r.h1 === 1, `${name}: expected exactly one h1`);
    check(!r.skip, `${name}: heading level skips`);
    check(r.text > 1500, `${name}: main has only ${r.text} characters`);
    check(r.scroll <= 0, `${name}: page scrolls horizontally by ${r.scroll}px`);
    check(r.canon === `https://memex.stifler.in/${path}`, `${name}: canonical is ${r.canon}`);
    await ctx.close();
  }
}

// Theme toggle: flips, persists, and survives a reload.
{
  const { ctx, page } = await open('theme', { viewport: { width: 1440, height: 900 }, colorScheme: 'light', reducedMotion: 'reduce' });
  const before = await page.evaluate(() => document.documentElement.dataset.theme);
  await page.click('#theme');
  const after = await page.evaluate(() => [document.documentElement.dataset.theme, localStorage.getItem('memex-theme'), document.getElementById('theme').getAttribute('aria-pressed')]);
  await page.reload({ waitUntil: 'networkidle' });
  const kept = await page.evaluate(() => document.documentElement.dataset.theme);
  check(before === 'light' && after[0] === 'dark' && after[1] === 'dark' && after[2] === 'true', `theme: toggle gave ${before} → ${after.join('/')}`);
  check(kept === 'dark', `theme: reload lost the choice (${kept})`);
  await ctx.close();
}

// Static files the site promises.
for (const f of ['og.jpg', 'robots.txt', 'sitemap.xml', 'llms.txt', 'icon.svg', 'favicon.ico', 'apple-icon.png', 'memex-v1-video.jpg', 'memex-v1-video.webp', 'memex-film-live-context.webp']) {
  const r = await fetch(BASE + f); check(r.ok, `GET /${f} → ${r.status}`);
}
check((await fetch(BASE + 'no-such-page')).status === 404, 'unknown path is not a 404');
// Worker config, not a served file: must ship in out/ with HSTS and immutable caching.
const headers = await readFile(join(OUT, '_headers'), 'utf8').catch(() => '');
check(/Strict-Transport-Security/.test(headers) && /\/_next\/static\/\*\s+Cache-Control: public, max-age=31536000, immutable/.test(headers), 'out/_headers missing HSTS or immutable caching');

await browser.close(); server.close();
if (fails.length) { console.error(`site-check: ${fails.length} failure(s)\n- ` + fails.join('\n- ')); process.exit(1); }
console.log('site-check: all checks passed (1440/390 layout, headings, anchors, reduced motion, JS off, theme, static files)');
