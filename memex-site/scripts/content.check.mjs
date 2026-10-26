/**
 * Every version the site states must be the released one, and every number on
 * the page must still be in the repository file it comes from.
 * node --test scripts/content.check.mjs (node:test, no framework)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const site = p => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
const repo = p => readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');
const VERSION = repo('pyproject.toml').match(/^version = "(.+)"/m)[1];

test('every stated version is the released one', () => {
  for (const f of ['src/app/page.tsx', 'src/app/layout.tsx', 'public/llms.txt', 'scripts/og.html']) {
    const found = [...site(f).matchAll(/v?(\d+\.\d+\.\d+)/g)].map(m => m[1]);
    assert.ok(found.length, `${f} states no version`);
    for (const v of found) assert.equal(v, VERSION, `${f} says ${v}, pyproject.toml says ${VERSION}`);
  }
});

test('evidence numbers trace to the reports', () => {
  const page = site('src/app/page.tsx');
  assert.match(page, /data-count="16">16<\/span> of 16 fixtures/);
  assert.match(repo('docs/v1/24_PHASE5_RELEASE_READINESS.md'), /16\/16 passed/);
  assert.match(page, /data-count="480"/);
  assert.match(page, /48 held-out histories/);
  assert.match(repo('docs/v1/27_PHASE6_PROTOCOL.md'), /48 × 5 × 2 = \*\*480 trials\*\*/);
  assert.match(page, /Five gates passed; efficacy, precision and latency did not/);
  assert.match(repo('docs/v1/28_PHASE6_RESULTS.md'), /Five gates pass and three fail: efficacy, precision and latency/);
});
