/**
 * Runnable check for the decay regimes.  node --test src/lib/confidence.check.ts
 * No test framework — node:test is built in.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  currentConfidence,
  isStale,
  VALIDATED_FLOOR,
  UNVALIDATED_OLD_CAP,
} from './confidence.ts';

const near = (a: number, b: number, eps = 1e-9) =>
  assert.ok(Math.abs(a - b) < eps, `${a} !~= ${b}`);

test('regime 1: validated never falls below the floor', () => {
  assert.equal(currentConfidence(0.6, 10_000, true), VALIDATED_FLOOR);
  assert.ok(currentConfidence(0.9, 30, true) > VALIDATED_FLOOR);
});

test('regime 2: unvalidated at base 0.6 crosses staleness at exactly day 30', () => {
  near(currentConfidence(0.6, 30), 0.3);
  assert.ok(!isStale(currentConfidence(0.6, 29)));
  assert.ok(isStale(currentConfidence(0.6, 31)));
});

test('the 30-day cliff is a property of base 0.6, not a universal deadline', () => {
  // a higher base takes longer to go stale under identical constants
  assert.ok(currentConfidence(0.9, 30) > 0.3);
});

test('regime 3: past the cliff decays faster and is capped', () => {
  assert.ok(currentConfidence(0.6, 31) <= UNVALIDATED_OLD_CAP);
  assert.ok(currentConfidence(0.6, 60) < currentConfidence(0.6, 31));
});

test('values rendered on the site', () => {
  near(Number(currentConfidence(0.6, 2).toFixed(2)), 0.57);
  near(Number(currentConfidence(0.6, 0).toFixed(2)), 0.6);
  near(Number(currentConfidence(0.6, 26).toFixed(2)), 0.33);
});
