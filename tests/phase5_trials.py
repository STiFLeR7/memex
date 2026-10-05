"""Phase 5 native trial runner: one history, one arm, one host, one real agent.

usage:
  python tests/phase5_trials.py run --split development --hosts claude,codex \
      --arms A,B,C,D,E,E-noinval,E-norecon,E-full --out output/phase5/pilot --parallel 3
  python tests/phase5_trials.py one D01 E claude --out output/phase5/smoke

A trial:
1. materializes the history in a fresh temporary repository (new repository and
   worktree identity, new graph namespace) and seeds its evidence and claims;
2. installs the arm's hooks plus the fixture hooks that apply the change after
   the agent's first completed tool call and record every mutation attempt;
3. runs the real client (Claude Code headless, or Codex through its app-server)
   with the same prompt, model, turn limit and tools in every arm;
4. scores the final state with hidden checks run outside the repository, in the
   worlds before and after the change;
5. joins the change time, every mutation attempt, every arm decision and every
   hook's latency into one record, and keeps derived trace metadata only (no
   raw transcript).

The record is written whatever happens: a failed, timed-out or unavailable
trial is a result, not a gap.
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
import traceback
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from memex.evaluation import fixtures  # noqa: E402
from memex.evaluation.arms import ARM_VERSION, ARMS, HOOKED_ARMS, set_arm, timings_path  # noqa: E402
from tests import phase4_support as support  # noqa: E402
from tests.phase5_trial_hooks import encode_plan  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
CHECKOUT = HERE.parent
TRIAL_HOOKS = HERE / "phase5_trial_hooks.py"
HARNESS_VERSION = "phase5-trials.v2-two-turn"
CLAUDE_MODEL = os.getenv("MEMEX_P5_CLAUDE_MODEL", "claude-sonnet-5-5")
CODEX_MODEL = os.getenv("MEMEX_P5_CODEX_MODEL", "gpt-6-sol")
CODEX_EFFORT = os.getenv("MEMEX_P5_CODEX_EFFORT", "medium")
MAX_TURNS = 30
TRIAL_TIMEOUT = float(os.getenv("MEMEX_P5_TRIAL_TIMEOUT", "900"))
PLAN_SUFFIX = (" First read whatever you need and describe your implementation plan. Do not edit any file in "
               "this turn, and do not run code or tests.")
IMPLEMENT = ("Now implement your plan. Edit only {target}. You may read any file in the repository, but do not "
             "run code or tests.")


def prompts_for(h: fixtures.History) -> tuple[str, str]:
    return h.spec.prompt + PLAN_SUFFIX, IMPLEMENT.format(target=h.spec.target)


def prompt_hash(h: fixtures.History) -> str:
    return "sha256:" + hashlib.sha256("\n".join(prompts_for(h)).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Hook wiring
# --------------------------------------------------------------------------- #

def _launcher(directory: pathlib.Path, name: str, body: str, uri: str) -> pathlib.Path:
    """Hosts filter hook environments, so everything a hook needs is set here."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        path = directory / f"{name}.cmd"
        path.write_text(f'@echo off\r\nset "PYTHONPATH={CHECKOUT}"\r\nset "MEMEX_LIVE_NEO4J_URI={uri}"\r\n'
                        f'{body} %*\r\n', encoding="utf-8")
    else:
        path = directory / f"{name}.sh"
        path.write_text(f"#!/bin/sh\nexport PYTHONPATH='{CHECKOUT}'\nexport MEMEX_LIVE_NEO4J_URI='{uri}'\n"
                        f"exec {body} \"$@\"\n", encoding="utf-8")
        path.chmod(0o700)
    return path


def launchers(bin_dir: pathlib.Path, host: str, uri: str) -> tuple[pathlib.Path, pathlib.Path]:
    python = sys.executable
    arm = _launcher(bin_dir, f"arm-{host}", f'"{python}" -m memex.evaluation.arms {host}', uri)
    hooks = _launcher(bin_dir, "trial-hooks", f'"{python}" "{TRIAL_HOOKS}"', uri)
    return arm, hooks


def _entry(command: str, timeout: int = 60) -> dict:
    return {"type": "command", "command": command, "timeout": timeout}


def claude_settings(repo: pathlib.Path, arm: str, arm_launcher, hooks, state, uri) -> None:
    hooked = arm in HOOKED_ARMS
    memex = _entry(arm_launcher.as_posix())
    edits = "Edit|Write|MultiEdit|NotebookEdit"
    record = lambda phase: _entry(f"{hooks.as_posix()} record {state.as_posix()} {phase}", 30)  # noqa: E731
    settings = {"hooks": {
        "SessionStart": [{"hooks": [memex]}] if hooked else [],
        "PreToolUse": ([{"matcher": edits + "|Bash", "hooks": [memex]}] if hooked else [])
        + [{"matcher": edits, "hooks": [record("proposed")]}],
        "PostToolUse": ([{"matcher": edits, "hooks": [memex]}] if hooked else [])
        + [{"matcher": edits, "hooks": [record("executed")]}],
        "SessionEnd": [{"hooks": [memex]}] if hooked else [],
    }}
    settings["hooks"] = {k: v for k, v in settings["hooks"].items() if v}
    path = repo / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def codex_flags(arm: str, arm_launcher, hooks, state, uri) -> list[str]:
    from memex.integrations import codex
    groups = codex.hook_groups(arm_launcher, timeout=60) if arm in HOOKED_ARMS else {}
    record = f"{hooks.as_posix()} record {state.as_posix()}"
    extra = {
        "PreToolUse": [{"matcher": "apply_patch", "hooks": [_entry(f"{record} proposed", 30)]}],
        "PostToolUse": [{"matcher": "apply_patch", "hooks": [_entry(f"{record} executed", 30)]}],
    }
    for event, more in extra.items():
        groups.setdefault(event, []).extend(more)
    flags = []
    for event, event_groups in groups.items():
        rendered = []
        for group in event_groups:
            bodies = ",".join(f"{{type=\"command\",command='{e['command']}',timeout={e['timeout']}}}"
                              for e in group["hooks"])
            matcher = f"matcher=\"{group['matcher']}\"," if "matcher" in group else ""
            rendered.append(f"{{{matcher}hooks=[{bodies}]}}")
        flags += ["-c", f"hooks.{event}=[{','.join(rendered)}]"]
    return flags


# --------------------------------------------------------------------------- #
# Running the clients
# --------------------------------------------------------------------------- #

def _claude_turn(repo: pathlib.Path, prompt: str, resume: str | None) -> dict:
    command = ["claude", "-p", prompt, "--model", CLAUDE_MODEL, "--permission-mode", "acceptEdits",
               "--output-format", "stream-json", "--verbose", "--max-turns", str(MAX_TURNS),
               "--setting-sources", "project,local", "--strict-mcp-config"]
    if resume:
        command += ["--resume", resume]
    try:
        done = subprocess.run(command, cwd=repo, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TRIAL_TIMEOUT)
        stdout, code, stderr, timed_out = done.stdout, done.returncode, done.stderr, False
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        stdout = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        code, stderr, timed_out = None, "", True
    records = []
    for line in stdout.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    result = next((r for r in reversed(records) if r.get("type") == "result"), {})
    return {"records": records, "result": result, "code": code, "stderr": stderr, "timed_out": timed_out,
            "session_id": next((r.get("session_id") for r in records if r.get("session_id")), None)}


def run_claude(repo: pathlib.Path, prompts: tuple[str, str], between) -> dict:
    """Turn 1 plans; `between()` applies the change; turn 2 resumes the same session and implements."""
    started = time.time()
    turns = [_claude_turn(repo, prompts[0], None)]
    first = turns[0]
    out: dict = {"timed_out": first["timed_out"], "client_error": None}
    if first["timed_out"] or first["code"] != 0 or first["result"].get("is_error") or not first["session_id"]:
        out["client_error"] = "turn 1: " + (first["stderr"][-400:] or str(first["result"].get("subtype")))
    else:
        between()
        second = _claude_turn(repo, prompts[1], first["session_id"])
        turns.append(second)
        out["timed_out"] = second["timed_out"]
        if not second["timed_out"] and (second["code"] != 0 or second["result"].get("is_error")):
            out["client_error"] = "turn 2: " + (second["stderr"][-400:] or str(second["result"].get("subtype")))
        out["resumed_same_session"] = second["session_id"] == first["session_id"]
    records = [r for t in turns for r in t["records"]]
    tools = []
    for turn_number, turn in enumerate(turns, 1):
        for record in turn["records"]:
            if record.get("type") == "assistant":
                for block in record.get("message", {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tools.append({"turn": turn_number, "tool": block["name"],
                                      "target": _target(block.get("input"))})
    results = [t["result"] for t in turns if t["result"]]
    usage = None
    if results:
        def total(key):
            values = [(r.get("usage") or {}).get(key) for r in results]
            return None if any(v is None for v in values) else sum(values)
        costs = [r.get("total_cost_usd") for r in results]
        usage = {"input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
                 "cache_read_input_tokens": total("cache_read_input_tokens"),
                 "cache_creation_input_tokens": total("cache_creation_input_tokens"),
                 "cost_usd": None if any(c is None for c in costs) else sum(costs),
                 "turns_reported": len(results)}
    out.update(
        wall_s=time.time() - started,
        model_reported=next((r.get("model") for r in records if r.get("type") == "system" and r.get("model")), None),
        num_turns=sum(r.get("num_turns") or 0 for r in results), usage=usage, tool_sequence=tools,
        denial_texts_seen=sum(1 for r in records if r.get("type") == "user" and "memex" in json.dumps(r)
                              and '"is_error": true' in json.dumps(r)),
        correction_text_seen=sum(1 for r in records if "outcome=replan" in json.dumps(r)),
        context_texts_seen=sum(1 for r in records if "memex" in json.dumps(r)))
    return out


def _target(tool_input) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    value = next((tool_input[k] for k in ("file_path", "path", "pattern", "command") if tool_input.get(k)), None)
    return None if value is None else str(value)[:200]


def _codex_turn(server, thread_id: str, prompt: str, out: dict, label: str) -> bool:
    turn_id = server.start_turn(thread_id, prompt)
    try:
        turn = server.wait_turn(thread_id, turn_id, timeout=TRIAL_TIMEOUT)
    except TimeoutError:
        out["timed_out"] = True
        return False
    if turn.get("status") != "completed":
        out["client_error"] = f"{label}: " + json.dumps(turn.get("error") or turn.get("status"))[:500]
        return False
    return True


def run_codex(repo: pathlib.Path, prompts: tuple[str, str], flags: list[str], between) -> dict:
    """Turn 1 in one app-server process; the change; turn 2 after `thread/resume` in a new process.

    S02 measured `SessionStart(source=resume)` firing for a resumed thread in a
    new process, which matches Claude's `--resume`, so both hosts see the same
    session boundary between the turns.
    """
    started = time.time()
    out: dict = {"timed_out": False, "client_error": None, "declined_approvals": 0}
    thread_id, rollout = None, None
    config = {"bypass_hook_trust": True, "model_reasoning_effort": CODEX_EFFORT}
    server = support.AppServer(flags)
    try:
        result = server.request("thread/start", {"cwd": str(repo), "model": CODEX_MODEL,
                                                 "sandbox": "workspace-write", "config": config})
        thread_id = result["thread"]["id"]
        ok = _codex_turn(server, thread_id, prompts[0], out, "turn 1")
        out["declined_approvals"] += len(server.declined)
    except Exception as exc:  # noqa: BLE001 - recorded as an unavailable client
        out["client_error"], ok = f"turn 1: {exc.__class__.__name__}: {str(exc)[:400]}", False
    finally:
        server.close()
    if ok:
        between()
        server = support.AppServer(flags)
        try:
            server.request("thread/resume", {"threadId": thread_id, "model": CODEX_MODEL, "config": config})
            _codex_turn(server, thread_id, prompts[1], out, "turn 2")
            out["declined_approvals"] += len(server.declined)
            try:
                rollout = server.rollout(thread_id)
            except Exception:  # noqa: BLE001
                rollout = None
        except Exception as exc:  # noqa: BLE001
            out["client_error"] = f"turn 2: {exc.__class__.__name__}: {str(exc)[:400]}"
        finally:
            server.close()
    out["wall_s"] = time.time() - started
    usage, tools, model, correction_seen, turn_number = None, [], None, 0, 0
    if rollout and pathlib.Path(rollout).exists():
        for record in support.rollout_records(pathlib.Path(rollout)):
            payload = record.get("payload") or {}
            if record.get("type") == "turn_context":
                model = payload.get("model") or model
                turn_number += 1
            if record.get("type") == "event_msg" and payload.get("type") == "token_count" and payload.get("info"):
                total = payload["info"].get("total_token_usage") or {}
                usage = {"input_tokens": total.get("input_tokens"), "output_tokens": total.get("output_tokens"),
                         "cache_read_input_tokens": total.get("cached_input_tokens"),
                         "reasoning_output_tokens": total.get("reasoning_output_tokens"), "cost_usd": None}
            if "outcome=replan" in json.dumps(payload):
                correction_seen += 1
            if record.get("type") == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                arguments = payload.get("arguments") or payload.get("input") or ""
                tools.append({"turn": turn_number, "tool": payload.get("name"), "target": str(arguments)[:200]})
    out.update(model_reported=model, usage=usage, tool_sequence=tools, rollout_found=bool(rollout),
               correction_text_seen=correction_seen, thread_id=thread_id)
    return out


# --------------------------------------------------------------------------- #
# One trial
# --------------------------------------------------------------------------- #

def _jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def trial_id(h: fixtures.History, arm: str, host: str, replicate: int = 0) -> str:
    return f"{h.history_id}-{arm}-{host}" + (f"-r{replicate}" if replicate else "")


def run_trial(h: fixtures.History, arm: str, host: str, *, uri: str, trial_root: pathlib.Path,
              evidence_dir: pathlib.Path, probe: tuple[str, ...], replicate: int = 0, keep: bool = False) -> dict:
    tid = trial_id(h, arm, host, replicate)
    base = trial_root / f"{tid}-{uuid.uuid4().hex[:6]}"
    state = trial_root / f"{tid}-state-{uuid.uuid4().hex[:6]}"
    state.mkdir(parents=True)
    record: dict = {
        "trial_id": tid, "history": h.history_id, "template": h.template, "mechanism": h.mechanism,
        "fixture": h.fixture, "label": h.label, "split": h.split, "arm": arm, "host": host,
        "replicate": replicate, "started_at": time.time(),
        "versions": {"fixtures": fixtures.FIXTURE_VERSION, "arms": ARM_VERSION, "harness": HARNESS_VERSION,
                     "prompt": prompt_hash(h), "python": platform.python_version(),
                     "model_requested": CLAUDE_MODEL if host == "claude" else f"{CODEX_MODEL}/{CODEX_EFFORT}",
                     "hardware": f"{platform.processor()} / {os.cpu_count()} cpu / {platform.platform()}"},
    }
    try:
        made = fixtures.materialize(h, base)
        repo = pathlib.Path(made["repo"])
        (state / "plan.b64").write_text(encode_plan(made["plan"]))
        seeded = asyncio.run(fixtures.seed_graph(h, repo, uri))
        record["repo_id"] = seeded["repo_id"]
        from memex.runtime.views import discover_repository
        registration = discover_repository(repo)
        if arm in HOOKED_ARMS:
            set_arm(registration, arm)
            if host == "claude":
                from memex.integrations.claude_code import register
                register(repo, "owner")
            else:
                from memex.integrations import codex
                codex.register(repo, "owner", backend={"neo4j_uri": uri})
        arm_launcher, hooks = launchers(state / "bin", host, uri)
        prompts = prompts_for(h)

        def between():
            """The change, applied by the harness between the two turns, as another contributor would."""
            at = time.time()
            error, applied = None, []
            try:
                applied = fixtures.apply_change(repo, made["plan"], uri=uri)
            except Exception as exc:  # noqa: BLE001 - recorded; the trial is then invalid
                error = f"{exc.__class__.__name__}: {str(exc)[:300]}"
            (state / "change.json").write_text(json.dumps({"at": at, "applied_at": time.time(),
                                                           "after_tool": "turn 1", "applied": applied,
                                                           "error": error}))
        if host == "claude":
            claude_settings(repo, arm, arm_launcher, hooks, state, uri)
            client = run_claude(repo, prompts, between)
        else:
            client = run_codex(repo, prompts, codex_flags(arm, arm_launcher, hooks, state, uri), between)
        record["client"] = client
        record.update(score(h, repo, registration, state, probe, arm))
        record["status"] = classify(record)
    except Exception as exc:  # noqa: BLE001 - an infrastructure failure is recorded, not hidden
        record["status"] = "invalid_infrastructure"
        record["error"] = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()[-1500:]}"
    record["finished_at"] = time.time()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f"{tid}.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    if not keep:
        for path in (base, state):
            try:
                fixtures.clean_tree(path)
            except OSError:
                pass
    return record


def score(h: fixtures.History, repo: pathlib.Path, registration, state: pathlib.Path, probe, arm: str) -> dict:
    """Objective outcome, action boundaries, arm decisions and resources for one trial."""
    from memex.runtime.trace import TraceStore

    change = json.loads((state / "change.json").read_text()) if (state / "change.json").exists() else None
    target_source = (repo / h.spec.target).read_text(encoding="utf-8", errors="replace")
    world_now = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in repo.glob("*.py")}
    after = fixtures.run_checks(h, {**fixtures.after_files(h), **world_now}, target_source)
    before = fixtures.run_checks(h, fixtures.before_files(h), target_source)
    failed_after = sorted(fixtures.failed_checks(h, after))
    failed_before = sorted(fixtures.failed_checks(h, before))
    success = not failed_after
    stale_failure = bool(h.affected and failed_after and not failed_before)

    actions = _jsonl(state / "actions.jsonl")
    proposed = [a for a in actions if a["phase"] == "proposed"]
    executed = [a for a in actions if a["phase"] == "executed"]
    try:
        trace = TraceStore(registration.runtime_path).events()
    except Exception:  # noqa: BLE001 - arm A may have no control plane
        trace = []
    checks = [e for e in trace if e["event"] == "action_check"]
    prevented = {e["attempt_id"]: e for e in checks if e["gate"] == "prevented" and e.get("attempt_id")}
    change_at = change["applied_at"] if change and not change.get("error") else None

    boundaries = []
    for p in proposed:
        after_change = change_at is not None and p["at"] >= change_at
        boundaries.append({"at": p["at"], "tool_use_id": p["tool_use_id"], "after_change": after_change,
                           "necessary": bool(h.affected and after_change),
                           # E-norecon's base check records "prevented" but the action proceeds.
                           "denied": p["tool_use_id"] in prevented and arm != "E-norecon",
                           "deferred_correction": p["tool_use_id"] in prevented and arm == "E-norecon",
                           "reason": prevented[p["tool_use_id"]]["reason"] if p["tool_use_id"] in prevented else None})
    timings = _jsonl(timings_path(registration))
    usage_hooks = [t for t in timings if t.get("event") == "PreToolUse" and t.get("tool") not in ("Bash", None)]
    return {
        "change": change, "probe": list(probe), "checks_after": after, "checks_before": before,
        "failed_after": failed_after, "failed_before": failed_before, "success": success,
        "stale_failure": stale_failure, "functional_failure": bool(failed_after and failed_before),
        "final_target_sha256": fixtures.digest_text(target_source),
        "boundaries": boundaries, "executions": len(executed),
        "trace_summary": {"events": len(trace), "prevented": len(prevented),
                          "confirmed_deliveries": sum(1 for e in trace if e["event"] == "delivery"
                                                      and e["insertion"] == "confirmed"),
                          "reasons": sorted({e["reason"] or "" for e in checks})},
        "hooks": timings, "action_check_hooks": len(usage_hooks),
        "delivered_chars": sum(t.get("chars") or 0 for t in timings),
        "refreshes": sum(t.get("refreshes") or 0 for t in timings),
        "refresh_ms": sum(t.get("refresh_ms") or 0.0 for t in timings),
    }


def classify(record: dict) -> str:
    client = record.get("client") or {}
    if client.get("timed_out"):
        return "timeout"
    if client.get("client_error"):
        text = str(client["client_error"]).lower()
        if any(word in text for word in ("overloaded", "capacity", "rate limit", "rate_limit", "429", "usage limit")):
            return "unavailable"
        return "client_error"
    if record.get("change") is None:
        return "no_change_applied"
    if record["change"].get("error"):
        return "invalid_infrastructure"
    if any(h.get("error") for h in record.get("hooks") or []):
        return "hook_error"
    return "completed"


# --------------------------------------------------------------------------- #
# Batches
# --------------------------------------------------------------------------- #

def plan_trials(histories, arms, hosts, replicates=1):
    return [(h, a, host, r) for r in range(replicates) for h in histories for host in hosts for a in arms]


def run_batch(histories, arms, hosts, *, out: pathlib.Path, parallel: int, uri: str, replicates: int = 1,
              budget: int | None = None, retries: int = 2) -> list[dict]:
    """Run every planned trial not already recorded, at most `budget` of them.

    Infrastructure failures and unavailable clients are retried up to `retries`
    times, as the protocol allows; every attempt's record is kept.
    """
    out.mkdir(parents=True, exist_ok=True)
    evidence = out / "trials"
    default_root = pathlib.Path(os.environ.get("TEMP", "/tmp")) / "memex-p5"
    trial_root = pathlib.Path(os.getenv("MEMEX_P5_TRIAL_ROOT", default_root))
    admissions = {h.history_id: fixtures.admit(h) for h in histories}
    rejected = [k for k, a in admissions.items() if not a.admitted]
    if rejected:
        raise SystemExit(f"histories not admitted: {rejected}")
    todo = [t for t in plan_trials(histories, arms, hosts, replicates)
            if not (evidence / f"{trial_id(t[0], t[1], t[2], t[3])}.json").exists()]
    if budget is not None:
        todo = todo[:budget]
    print(f"{len(todo)} trials to run", flush=True)
    results = []

    def one(item):
        h, arm, host, rep = item
        for attempt in range(retries + 1):
            record = run_trial(h, arm, host, uri=uri, trial_root=trial_root, evidence_dir=evidence,
                               probe=admissions[h.history_id].probe, replicate=rep)
            if record["status"] not in ("invalid_infrastructure", "unavailable"):
                return record
            (evidence / f"{record['trial_id']}.attempt{attempt}.json").write_text(json.dumps(record, default=str))
            time.sleep(30 * (attempt + 1))
        return record

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        for record in pool.map(one, todo):
            results.append(record)
            print(json.dumps({k: record.get(k) for k in ("trial_id", "status", "success", "stale_failure")}),
                  flush=True)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--split", choices=("development", "confirmatory"), required=True)
    run.add_argument("--mechanisms", default=",".join(fixtures.MECHANISMS))
    run.add_argument("--histories", default="")
    run.add_argument("--hosts", default="claude,codex")
    run.add_argument("--arms", default=",".join(ARMS))
    run.add_argument("--out", required=True)
    run.add_argument("--parallel", type=int, default=3)
    run.add_argument("--replicates", type=int, default=1)
    run.add_argument("--budget", type=int)
    one = sub.add_parser("one")
    one.add_argument("history")
    one.add_argument("arm")
    one.add_argument("host")
    one.add_argument("--out", required=True)
    one.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI") or "bolt://127.0.0.1:17687"
    if args.command == "one":
        pool = fixtures.DEV_HISTORIES + fixtures.confirmatory_histories(tuple(fixtures.MECHANISMS))
        h = fixtures.history_by_id(args.history, pool)
        trial_root = pathlib.Path(os.environ.get("TEMP", "/tmp")) / "memex-p5"
        record = run_trial(h, args.arm, args.host, uri=uri, trial_root=trial_root,
                           evidence_dir=pathlib.Path(args.out) / "trials", probe=fixtures.admit(h).probe,
                           keep=args.keep)
        print(json.dumps({k: v for k, v in record.items() if k not in ("hooks",)}, indent=1, default=str))
        return 0
    if args.split == "development":
        histories = fixtures.DEV_HISTORIES
    else:
        histories = fixtures.confirmatory_histories(tuple(args.mechanisms.split(",")))
    if args.histories:
        wanted = set(args.histories.split(","))
        histories = tuple(h for h in histories if h.history_id in wanted)
    run_batch(histories, args.arms.split(","), args.hosts.split(","), out=pathlib.Path(args.out),
              parallel=args.parallel, uri=uri, replicates=args.replicates, budget=args.budget)
    return 0


if __name__ == "__main__":
    sys.exit(main())
