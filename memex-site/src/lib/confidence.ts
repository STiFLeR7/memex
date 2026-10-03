/**
 * Port of memex/graph/confidence.py::current_confidence
 *
 * Confidence is never stored — it is recomputed from three fields and the
 * elapsed time. Three regimes, not two (the README says two; the code says
 * three, and the code wins).
 *
 * Constants are anchored to the staleness crossing (0.3) from the
 * watcher-synthesised default base of 0.6. They equal a "half-life" only by
 * coincidence, because halving 0.6 yields 0.3.
 */

export const LAMBDA_VALIDATED = 0.005;
export const LAMBDA_UNVALIDATED = Math.LN2 / 30;
export const LAMBDA_UNVALIDATED_OLD = Math.LN2 / 20;
export const UNVALIDATED_OLD_THRESHOLD_DAYS = 30;
export const UNVALIDATED_OLD_CAP = 0.5;
export const VALIDATED_FLOOR = 0.7;
export const STALENESS_THRESHOLD = 0.3;

export function currentConfidence(
  base: number,
  days: number,
  validated = false,
): number {
  const d = Math.max(0, days);

  // Regime 1 — validated: slow decay, hard floor.
  if (validated) {
    return Math.max(VALIDATED_FLOOR, base * Math.exp(-LAMBDA_VALIDATED * d));
  }
  // Regime 2 — unvalidated, inside the window.
  if (d <= UNVALIDATED_OLD_THRESHOLD_DAYS) {
    return base * Math.exp(-LAMBDA_UNVALIDATED * d);
  }
  // Regime 3 — unvalidated and past the cliff: faster decay AND a ceiling.
  return Math.min(UNVALIDATED_OLD_CAP, base * Math.exp(-LAMBDA_UNVALIDATED_OLD * d));
}

export const isStale = (conf: number): boolean => conf < STALENESS_THRESHOLD;

export const formatConfidence = (v: number): string =>
  `confidence ≈ ${v.toFixed(2)}`;
