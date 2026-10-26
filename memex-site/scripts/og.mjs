// Renders scripts/og.html to public/og.jpg (1200×630, baseline JPEG from Chrome).
import { chromium } from 'playwright';
const b = await chromium.launch({ channel: 'chrome' });
const p = await b.newPage({ viewport: { width: 1200, height: 630 } });
await p.goto(new URL('og.html', import.meta.url).href, { waitUntil: 'networkidle' });
await p.evaluate(() => document.fonts.ready);
await p.screenshot({ path: new URL('../public/og.jpg', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'), type: 'jpeg', quality: 90 });
await b.close();
