"""Phase 5 analysis: metrics, paired cluster-bootstrap intervals and gate decisions.

Every definition here is the one written in docs/v1/22_PHASE5_PROTOCOL.md.
The functions only read trial records; they never re-run, filter or relabel a
trial. A trial that did not complete stays in the counts it belongs to and is
reported by status.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

PRIMARY = "E"
BASELINES = ("C", "D")
COMPLETED = "completed"


def load(directory: str | Path) -> list[dict]:
    """Every final trial record in `directory`/trials. Retry attempts are kept separately."""
    trials = Path(directory) / "trials"
    records = []
    for path in sorted(trials.glob("*.json")):
        if ".attempt" in path.name:
            continue
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def attempts(directory: str | Path) -> list[dict]:
    paths = sorted((Path(directory) / "trials").glob("*.attempt*.json"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def valid(record: dict) -> bool:
    return record.get("status") == COMPLETED


# --------------------------------------------------------------------------- #
# Per-trial facts
# --------------------------------------------------------------------------- #

def boundaries(record: dict) -> list[dict]:
    return record.get("boundaries") or []


def unaffected_boundaries(record: dict) -> list[dict]:
    """Every boundary in a stable history, and pre-change boundaries in an affected one."""
    return [b for b in boundaries(record) if not b["necessary"]]


def corrections(record: dict) -> list[dict]:
    """Correction events: a denial, or for E-norecon a deferred correction."""
    return [b for b in boundaries(record) if b.get("denied") or b.get("deferred_correction")]


def material(boundary: dict) -> bool:
    """A correction is material when it lands at a necessary boundary."""
    return bool(boundary["necessary"])


def necessary_opportunity(record: dict) -> bool:
    return any(b["necessary"] for b in boundaries(record))


def timely(record: dict) -> bool:
    """The first post-change mutation attempt was held for reconsideration."""
    first = next((b for b in boundaries(record) if b["necessary"]), None)
    return bool(first and first.get("denied"))


def retries(record: dict) -> int:
    """Mutation attempts after the first denial in the trial."""
    seen = False
    count = 0
    for b in boundaries(record):
        if seen:
            count += 1
        if b.get("denied"):
            seen = True
    return count


def warm_unchanged_latencies(record: dict) -> list[float]:
    """End-to-end hook latency for declared action checks that proceeded."""
    return [h["total_ms"] for h in record.get("hooks") or []
            if h.get("event") == "PreToolUse" and h.get("tool") not in (None, "Bash")
            and h.get("decision") is None and h.get("total_ms") is not None and not h.get("error")]


def check_latencies(record: dict) -> list[float]:
    """In-process check time for the same checks (the core's own work)."""
    return [h["check_ms"] for h in record.get("hooks") or []
            if h.get("event") == "PreToolUse" and h.get("tool") not in (None, "Bash")
            and h.get("decision") is None and h.get("check_ms") is not None and not h.get("error")]


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #

def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def rate(numerator: int, denominator: int) -> dict:
    return {"n": numerator, "d": denominator, "rate": None if denominator == 0 else numerator / denominator}


def summarize(records: list[dict], arm: str, host: str | None = None) -> dict:
    rows = [r for r in records if r["arm"] == arm and (host is None or r["host"] == host)]
    done = [r for r in rows if valid(r)]
    affected = [r for r in done if r["label"] == "affected"]
    stable = [r for r in done if r["label"] == "stable"]
    events = [b for r in done for b in corrections(r)]
    unaffected = [b for r in done for b in unaffected_boundaries(r)]
    opportunities = [r for r in affected if necessary_opportunity(r)]
    warm = [x for r in done for x in warm_unchanged_latencies(r)]
    core = [x for r in done for x in check_latencies(r)]
    usage = [r.get("client", {}).get("usage") for r in done]
    tokens_known = [u for u in usage if u and u.get("output_tokens") is not None]
    cost_known = [u["cost_usd"] for u in usage if u and u.get("cost_usd") is not None]
    return {
        "arm": arm, "host": host or "all",
        "trials": len(rows), "status": dict(Counter(r.get("status") for r in rows)),
        "stale_failure": rate(sum(r["stale_failure"] for r in affected), len(affected)),
        "affected_success": rate(sum(r["success"] for r in affected), len(affected)),
        "stable_completion": rate(sum(r["success"] for r in stable), len(stable)),
        "overall_success": rate(sum(r["success"] for r in done), len(done)),
        "functional_failure": rate(sum(bool(r.get("functional_failure")) for r in done), len(done)),
        "precision": rate(sum(material(b) for b in events), len(events)),
        "recall": rate(sum(timely(r) for r in opportunities), len(opportunities)),
        "false_interruption": rate(sum(bool(b.get("denied")) for b in unaffected), len(unaffected)),
        "retries": sum(retries(r) for r in done),
        "latency_warm_unchanged_ms": {"n": len(warm), "p50": percentile(warm, 50), "p95": percentile(warm, 95),
                                      "max": max(warm) if warm else None},
        "latency_core_check_ms": {"n": len(core), "p50": percentile(core, 50), "p95": percentile(core, 95)},
        "delivered_chars_mean": _mean([r.get("delivered_chars", 0) for r in done]),
        "refreshes_mean": _mean([r.get("refreshes", 0) for r in done]),
        "wall_s_mean": _mean([r.get("client", {}).get("wall_s") for r in done]),
        "output_tokens_mean": _mean([u["output_tokens"] for u in tokens_known]),
        "usage_known": f"{len(tokens_known)}/{len(done)}",
        "cost_usd_total": sum(cost_known) if cost_known else None,
        "cost_known": f"{len(cost_known)}/{len(done)}",
    }


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


# --------------------------------------------------------------------------- #
# Paired comparison with clustered bootstrap
# --------------------------------------------------------------------------- #

def paired_units(records: list[dict], arms: tuple[str, ...]) -> dict:
    """{(history, host): {arm: stale_failure}} over affected histories where every arm completed."""
    units: dict = defaultdict(dict)
    for r in records:
        if r["label"] == "affected" and valid(r) and r["arm"] in arms:
            units[(r["history"], r["host"])][r["arm"]] = int(bool(r["stale_failure"]))
    return {k: v for k, v in units.items() if all(a in v for a in arms)}


def stale_rates(units: dict, arms) -> dict:
    return {a: (sum(v[a] for v in units.values()) / len(units)) if units else None for a in arms}


def strongest_baseline(units: dict, baselines=BASELINES) -> str | None:
    rates = stale_rates(units, baselines)
    known = {a: r for a, r in rates.items() if r is not None}
    return min(known, key=lambda a: (known[a], a)) if known else None


def bootstrap(units: dict, primary: str, baseline: str, *, cluster: str = "history", resamples: int = 10000,
              seed: int = 20261005, template_of: dict | None = None) -> dict:
    """Percentile bootstrap for d = p_baseline - p_primary and the relative reduction.

    Resampling is by cluster: whole histories (all their host units) by
    default, or whole templates for the sensitivity analysis.
    """
    if not units:
        return {"units": 0}
    by_cluster: dict = defaultdict(list)
    for (history, _host), value in units.items():
        key = history if cluster == "history" else (template_of or {}).get(history, history)
        by_cluster[key].append(value)
    keys = sorted(by_cluster)
    rng = random.Random(seed)
    diffs, reductions = [], []
    for _ in range(resamples):
        chosen = [v for _ in keys for v in by_cluster[rng.choice(keys)]]
        pb = sum(v[baseline] for v in chosen) / len(chosen)
        pp = sum(v[primary] for v in chosen) / len(chosen)
        diffs.append(pb - pp)
        reductions.append(None if pb == 0 else 1 - pp / pb)
    diffs.sort()
    finite = sorted(x for x in reductions if x is not None)
    point_b = sum(v[baseline] for v in units.values()) / len(units)
    point_p = sum(v[primary] for v in units.values()) / len(units)
    return {
        "units": len(units), "clusters": len(keys), "cluster": cluster, "resamples": resamples, "seed": seed,
        "p_baseline": point_b, "p_primary": point_p, "difference": point_b - point_p,
        "difference_ci95": (diffs[int(0.025 * resamples)], diffs[int(0.975 * resamples) - 1]),
        "relative_reduction": None if point_b == 0 else 1 - point_p / point_b,
        "relative_reduction_ci95": ((finite[int(0.025 * len(finite))], finite[int(0.975 * len(finite)) - 1])
                                    if len(finite) >= 40 else None),
        "undefined_resamples": resamples - len(finite),
    }


def stable_noninferiority(records: list[dict], primary: str, comparator: str, *, resamples: int = 10000,
                          seed: int = 20261006) -> dict:
    """Completion on stable histories: d = completion_primary - completion_comparator, clustered by history."""
    units: dict = defaultdict(dict)
    for r in records:
        if r["label"] == "stable" and valid(r) and r["arm"] in (primary, comparator):
            units[(r["history"], r["host"])][r["arm"]] = int(bool(r["success"]))
    units = {k: v for k, v in units.items() if primary in v and comparator in v}
    if not units:
        return {"units": 0}
    by_history: dict = defaultdict(list)
    for (history, _), value in units.items():
        by_history[history].append(value)
    keys = sorted(by_history)
    rng = random.Random(seed)
    diffs = []
    for _ in range(resamples):
        chosen = [v for _ in keys for v in by_history[rng.choice(keys)]]
        diffs.append(sum(v[primary] - v[comparator] for v in chosen) / len(chosen))
    diffs.sort()
    point = sum(v[primary] - v[comparator] for v in units.values()) / len(units)
    return {"units": len(units), "difference": point,
            "difference_ci95": (diffs[int(0.025 * resamples)], diffs[int(0.975 * resamples) - 1])}


# --------------------------------------------------------------------------- #
# Sample size
# --------------------------------------------------------------------------- #

def required_units(p_baseline: float, p_primary: float, *, alpha: float = 0.05, power: float = 0.8,
                   discordance: float | None = None) -> int | None:
    """Paired-binary (McNemar) sample size for detecting p_baseline - p_primary.

    `discordance` is the expected share of discordant pairs; without a pilot
    estimate it defaults to the independence bound p_b(1-p_p) + p_p(1-p_b).
    Returns None when no difference is expected, which no sample can detect.
    """
    delta = p_baseline - p_primary
    if delta <= 0:
        return None
    z_a, z_b = 1.959963984540054, {0.8: 0.8416212335729143, 0.9: 1.2815515655446004}[power]
    psi = discordance if discordance is not None else p_baseline * (1 - p_primary) + p_primary * (1 - p_baseline)
    psi = max(psi, delta)
    n = (z_a * math.sqrt(psi) + z_b * math.sqrt(psi - delta * delta)) ** 2 / (delta * delta)
    return math.ceil(n)


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #

def gate(name: str, status: str, detail: str, **evidence) -> dict:
    assert status in ("passed", "failed", "pending", "not_run", "not_established")
    return {"gate": name, "status": status, "detail": detail, **evidence}


def efficacy_gate(records: list[dict], frozen: dict) -> dict:
    units = paired_units(records, (PRIMARY,) + BASELINES)
    if not units:
        return gate("efficacy", "not_established", "no complete paired affected units")
    baseline = strongest_baseline(units)
    result = bootstrap(units, PRIMARY, baseline, resamples=frozen["resamples"], seed=frozen["seed"])
    templates = {r["history"]: r["template"] for r in records}
    sensitivity = bootstrap(units, PRIMARY, baseline, cluster="template", template_of=templates,
                            resamples=frozen["resamples"], seed=frozen["seed"])
    rr = result["relative_reduction"]
    passed = (rr is not None and rr >= frozen["min_relative_reduction"] and result["difference_ci95"][0] > 0)
    if result["p_baseline"] == 0:
        detail = (f"strongest baseline {baseline} had no stale-context failures, so no relative reduction "
                  "is possible")
    else:
        detail = (f"E {result['p_primary']:.3f} vs {baseline} {result['p_baseline']:.3f}; relative reduction "
                  f"{rr if rr is None else round(rr, 3)}; difference CI95 "
                  f"{tuple(round(x, 3) for x in result['difference_ci95'])}")
    return gate("efficacy", "passed" if passed else "failed", detail, strongest_baseline=baseline,
                bootstrap=result, template_sensitivity=sensitivity)


def threshold_gate(name: str, value: dict, *, minimum=None, maximum=None) -> dict:
    if value["rate"] is None:
        return gate(name, "not_established", f"no denominator ({value['n']}/{value['d']})", value=value)
    ok = (minimum is None or value["rate"] >= minimum) and (maximum is None or value["rate"] <= maximum)
    bound = f">= {minimum}" if minimum is not None else f"<= {maximum}"
    return gate(name, "passed" if ok else "failed", f"{value['n']}/{value['d']} = {value['rate']:.3f} (needs {bound})",
                value=value)


def latency_gate(summary: dict, maximum_ms: float) -> dict:
    p95 = summary["latency_warm_unchanged_ms"]["p95"]
    if p95 is None:
        return gate("latency", "not_established", "no warm unchanged action checks")
    return gate("latency", "passed" if p95 < maximum_ms else "failed",
                f"warm unchanged action-check p95 {p95:.0f} ms over {summary['latency_warm_unchanged_ms']['n']} "
                f"checks (needs < {maximum_ms} ms); core check p95 {summary['latency_core_check_ms']['p95']}",
                value=summary["latency_warm_unchanged_ms"])
