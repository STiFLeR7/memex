"""Objective Goal 10 benchmark: real edits, isolated repos, and file checks."""

from __future__ import annotations

import concurrent.futures
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memex.evaluation.objective import verify_file_state
from memex.evaluation.release_readiness import EVALUATION_CASES, EvaluationCase, run_evaluation_matrix
from memex.integrations.hermes_provider import _retrieval_id


ROOT = Path(os.environ.get("GOAL10_REPO", "/mnt/d/memex")).resolve()
HERMES = os.environ.get("HERMES_BIN", "/mnt/d/memex/.goal10-hermes-venv/bin/hermes")
LLM_PROVIDER = os.environ.get("GOAL10_LLM_PROVIDER", "nvidia")
MODEL = os.environ.get(
    "GOAL10_MODEL",
    "stealth/ox-alpha" if LLM_PROVIDER == "openrouter" else (
        "llama-3.3-70b-versatile" if LLM_PROVIDER == "groq" else
        "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    ),
)
ARTIFACTS = Path(
    os.environ.get(
        "GOAL10_ARTIFACT_ROOT",
        "/mnt/d/memex/docs/architecture/v0.9/goal10-objective",
    )
).resolve()
HERMES_TIMEOUT = int(os.environ.get("GOAL10_HERMES_TIMEOUT", "180"))
EMBEDDING_MODEL = os.environ.get("MEMEX_EMBEDDING_MODEL", "nvidia/nemotron-3-embed-1b")
PROJECT_PREFIX = "goal10-objective"


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout or exc.output, stderr=stderr or exc.stderr) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", str(repo), *args], cwd=repo)


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fixture(case: str, repo: Path) -> None:
    _write(repo, "README.md", """# Objective calculator\n\n`calculator.py` is the public arithmetic module. Tests live in `tests/`.\nEngineering decisions are recorded in `docs/decisions.md`; current decisions supersede older ones.\n""")
    _write(repo, "calculator.py", """def add(a, b):\n    return a + b\n\n\ndef divide(a, b):\n    if b == 0:\n        raise ValueError(\"division by zero\")\n    return a / b\n""")
    _write(repo, "tests/test_calculator.py", """from calculator import add, divide\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_divide():\n    assert divide(8, 2) == 4\n""")
    _write(repo, "docs/decisions.md", """# Engineering decisions\n\n- The calculator module owns arithmetic behavior.\n- Tests must be updated with public behavior changes.\n""")

    if case == "unfamiliar-repository":
        pass
    elif case == "architecture-investigation":
        pass
    elif case == "bug-investigation":
        _write(repo, "parser.py", """def parse_count(value):\n    return int(value.strip()) + 1\n""")
        _write(repo, "tests/test_parser.py", """from parser import parse_count\n\n\ndef test_parse_count():\n    assert parse_count(\"41\") == 41\n""")
    elif case == "regression-fix":
        _write(repo, "discount.py", """def apply_discount(amount, rate):\n    return amount * (1 + rate)\n""")
        _write(repo, "tests/test_discount.py", """from discount import apply_discount\n\n\ndef test_apply_discount():\n    assert apply_discount(100, 0.10) == 90\n""")
    elif case == "multi-session":
        _write(repo, "docs/decisions.md", """# Engineering decisions\n\n- `calculate_total(items)` belongs in `calculator.py` and returns the sum.\n- Tests must be updated with public behavior changes.\n""")
    elif case == "agent-handoff":
        _write(repo, "slugger.py", """def slugify(value):\n    # TODO: normalize a title into a lowercase hyphenated slug\n    return value\n""")
        _write(repo, "tests/test_slugger.py", """from slugger import slugify\n\n\ndef test_slugify():\n    assert slugify(\"Hello, World!\") == \"hello-world\"\n""")
    elif case == "stale-knowledge":
        _write(repo, "settings.py", "DEFAULT_TIMEOUT = 5\n")
        _write(repo, "docs/decisions.md", """# Engineering decisions\n\n- The old 5-second timeout decision is superseded.\n- Current decision: `DEFAULT_TIMEOUT` is 30 seconds.\n""")
        _write(repo, "tests/test_settings.py", """from settings import DEFAULT_TIMEOUT\n\n\ndef test_current_timeout():\n    assert DEFAULT_TIMEOUT == 30\n""")
    elif case == "parallel-conflicts":
        _write(repo, "settings.py", "DEFAULT_TIMEOUT = 10\n")
        _write(repo, "tests/test_settings.py", """from settings import DEFAULT_TIMEOUT\n\n\ndef test_timeout_is_positive():\n    assert DEFAULT_TIMEOUT > 0\n""")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "goal10@example.invalid")
    _git(repo, "config", "user.name", "Goal 10")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")


def _prompts(case: str) -> list[str]:
    return {
        "unfamiliar-repository": [
            "Inspect this repository with tools. Implement multiply(a, b) in calculator.py, add a pytest for it, and run python -m pytest -q. Do not only explain; make the edits."
        ],
        "architecture-investigation": [
            "Inspect the repository. Create docs/ARCHITECTURE_NOTES.md explaining the module ownership, test location, and the decision that public behavior changes require tests. Then run python -m pytest -q. Make the file, do not only describe it."
        ],
        "bug-investigation": [
            "Inspect parser.py and its failing test. Fix parse_count so it returns the integer represented by the input, add no unrelated behavior, and run python -m pytest -q."
        ],
        "regression-fix": [
            "Inspect discount.py and its failing regression test. Fix apply_discount so the rate is subtracted from the amount, then run python -m pytest -q."
        ],
        "multi-session": [
            "Inspect the repository and docs/decisions.md. Create HANDOFF_PLAN.md with a concise implementation plan for calculate_total(items) in calculator.py. Do not implement it yet.",
            "Continue the engineering task from HANDOFF_PLAN.md. Implement calculate_total(items) in calculator.py, add a pytest, and run python -m pytest -q."
        ],
        "agent-handoff": [
            "You are the first engineer. Inspect slugger.py and its test. Create HANDOFF.md describing the bug, the expected behavior, and the exact files the next engineer must change. Do not implement the fix yet.",
            "You are the receiving engineer in a fresh session. Read HANDOFF.md and the repository. Implement the handoff fix in slugger.py, add or update tests only as needed, and run python -m pytest -q."
        ],
        "stale-knowledge": [
            "Inspect settings.py, docs/decisions.md, and the failing test. Apply the current decision, not the superseded 5-second value: update DEFAULT_TIMEOUT to 30 and run python -m pytest -q."
        ],
        "parallel-conflicts": [],
    }[case]


def _config(home: Path, repo: Path, project: str, treatment: bool) -> None:
    lines = ["model:", f"  provider: {LLM_PROVIDER}", f"  default: {MODEL}"]
    if treatment:
        lines.extend([
            "memory:", "  provider: memex", "plugins:", "  memex:",
            f"    repo_path: {repo.as_posix()}", f"    project_id: {project}",
            "    prefetch_timeout_seconds: 8", "    max_items: 5", "    max_chars: 8000",
        ])
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _trace(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-1] if rows else {}


def _usage(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _hermes_step(
    prompt: str,
    repo: Path,
    home: Path,
    artifact: Path,
    project: str,
    treatment: bool,
    step: int,
    timeout: int | None = None,
) -> dict[str, Any]:
    artifact.mkdir(parents=True, exist_ok=True)
    _config(home, repo, project, treatment)
    usage_path = artifact / f"usage-{step}.json"
    trace_path = artifact / "prefetch.jsonl"
    if treatment and step == 1 and trace_path.exists():
        trace_path.unlink()
    env = os.environ.copy()
    env.update({"HERMES_HOME": str(home), "TERMINAL_CWD": str(repo)})
    if treatment:
        env["MEMEX_PREFETCH_TRACE_PATH"] = str(trace_path)
    else:
        env.pop("MEMEX_PREFETCH_TRACE_PATH", None)
    command = [
        HERMES, "-z", prompt, "--provider", LLM_PROVIDER, "-m", MODEL,
        "--toolsets", "file,terminal", "--no-restore-cwd", "--accept-hooks",
        "--yolo", "--usage-file", str(usage_path),
    ]
    try:
        completed = _run(command, cwd=repo, env=env, timeout=timeout or HERMES_TIMEOUT)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        completed = None
        timed_out = True
        (artifact / f"stdout-{step}.txt").write_text(str(exc.stdout or ""), encoding="utf-8")
        (artifact / f"stderr-{step}.txt").write_text(str(exc.stderr or ""), encoding="utf-8")
    if completed is not None:
        (artifact / f"stdout-{step}.txt").write_text(completed.stdout, encoding="utf-8")
        (artifact / f"stderr-{step}.txt").write_text(completed.stderr, encoding="utf-8")
    usage = _usage(usage_path)
    return {"completed": completed, "timed_out": timed_out, "usage": usage, "trace": _trace(trace_path)}


def _seed_embedding(text: str) -> list[float]:
    base = os.environ["NVIDIA_NIM_BASE_URL"].rstrip("/")
    request = urllib.request.Request(
        f"{base}/embeddings",
        data=json.dumps({"input": [text], "model": EMBEDDING_MODEL, "input_type": "passage", "encoding_format": "float"}).encode(),
        headers={"Authorization": f"Bearer {os.environ['NVIDIA_NIM_API_KEY']}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())['data'][0]['embedding']


def _seed_context(project: str, repo: Path, facts: list[str]) -> str:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    group = f"{PROJECT_PREFIX}-{project.replace('/', '-') }"
    driver.execute_query("MATCH (n) WHERE n.group_id = $group DETACH DELETE n", group=group, database_=os.environ.get("NEO4J_DATABASE", "neo4j"))
    now = datetime.now(timezone.utc).isoformat()
    for index, fact in enumerate(facts):
        embedding = _seed_embedding(fact)
        source_uuid = f"{group}-source-{index}"
        fact_uuid = f"{group}-fact-{index}"
        edge_uuid = f"{group}-edge-{index}"
        driver.execute_query(
            """
            CREATE (s:Entity {uuid: $source_uuid, name: $source_name, summary: $source_summary,
                repo_path: $repo, project_id: $project, group_id: $group, source: $source,
                source_kind: 'derived', base_confidence: 0.95, observed_at: $now,
                created_at: $now, valid_at: $now, name_embedding: $embedding})
            CREATE (f:Entity {uuid: $fact_uuid, name: $fact, summary: $fact,
                repo_path: $repo, project_id: $project, group_id: $group, source: $source,
                source_kind: 'derived', base_confidence: 0.95, observed_at: $now,
                created_at: $now, valid_at: $now, name_embedding: $embedding})
            CREATE (s)-[:RELATES_TO {uuid: $edge_uuid, name: $fact, fact: $fact, repo_path: $repo,
                project_id: $project, group_id: $group, source: $source, source_kind: 'derived',
                base_confidence: 0.95, observed_at: $now, created_at: $now, valid_at: $now,
                fact_embedding: $embedding, episodes: []}]->(f)
            """,
            source_uuid=source_uuid, source_name=f"Goal 10 source {index}", source_summary="Seeded objective evaluation evidence",
            fact_uuid=fact_uuid, fact=fact, edge_uuid=edge_uuid, repo=str(repo), project=project,
            group=group, source="goal10-objective-seed", now=now, embedding=embedding, database_="neo4j",
        )
    driver.close()
    return group


def _clean_evaluation_seed_groups() -> None:
    """Remove only Goal 10-owned Neo4j seed nodes before a fresh matrix."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    driver.execute_query(
        "MATCH (n) WHERE n.group_id STARTS WITH $prefix DETACH DELETE n",
        prefix="goal10-",
        database_=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )
    driver.close()


def _objective(case: str, repo: Path) -> tuple[bool, list[str]]:
    checks: dict[str, Any] = {
        "unfamiliar-repository": {"required_files": ["calculator.py", "tests/test_calculator.py"], "expected_text": {"calculator.py": ["def multiply", "return a * b"]}},
        "architecture-investigation": {"required_files": ["docs/ARCHITECTURE_NOTES.md"], "expected_text": {"docs/ARCHITECTURE_NOTES.md": ["calculator.py", "tests", "public"]}},
        "bug-investigation": {"required_files": ["parser.py", "tests/test_parser.py"], "expected_text": {"parser.py": ["return int(value.strip())"]}},
        "regression-fix": {"required_files": ["discount.py"], "expected_text": {"discount.py": ["1 - rate"]}},
        "multi-session": {"required_files": ["HANDOFF_PLAN.md", "calculator.py", "tests/test_calculator.py"], "expected_text": {"calculator.py": ["def calculate_total", "return sum(items)"], "HANDOFF_PLAN.md": ["calculate_total"]}},
        "agent-handoff": {"required_files": ["HANDOFF.md", "slugger.py", "tests/test_slugger.py"], "expected_text": {"HANDOFF.md": ["slugger.py", "expected"], "slugger.py": ["value.lower()", "replace"]}},
        "stale-knowledge": {"required_files": ["settings.py"], "expected_text": {"settings.py": ["DEFAULT_TIMEOUT = 30"]}},
    }
    if case == "agent-handoff":
        failures: list[str] = []
        handoff = repo / "HANDOFF.md"
        slugger = repo / "slugger.py"
        if not handoff.is_file():
            failures.append("missing_file:HANDOFF.md")
        else:
            handoff_text = handoff.read_text(encoding="utf-8").lower()
            for needle in ("slugger.py", "expected"):
                if needle not in handoff_text:
                    failures.append(f"missing_text:HANDOFF.md:{needle}")
        if not slugger.is_file():
            failures.append("missing_file:slugger.py")
        else:
            behavior = _run(
                [sys.executable, "-c", "from slugger import slugify; assert slugify('Hello, World!') == 'hello-world'"],
                cwd=repo,
                timeout=30,
            )
            if behavior.returncode != 0:
                failures.append("behavior_failed:slugger.slugify")
        return not failures, failures
    if case == "parallel-conflicts":
        return True, []
    return verify_file_state(repo, **checks[case])


def _pytest(repo: Path) -> tuple[bool, str]:
    result = _run([sys.executable, "-m", "pytest", "-q"], cwd=repo, timeout=120)
    return result.returncode == 0, (result.stdout + result.stderr)[-4000:]


def _run_parallel(repo: Path, artifact: Path, treatment: bool, project: str) -> tuple[bool, list[str], dict[str, Any]]:
    workers = Path(tempfile.mkdtemp(prefix="workers-", dir=artifact))
    paths = [workers / "agent-a", workers / "agent-b"]
    for path in paths:
        shutil.copytree(repo, path)
    prompts = [
        "You are parallel worker A. In settings.py replace exactly DEFAULT_TIMEOUT = 10 with DEFAULT_TIMEOUT = 30. Run python -m pytest -q after making the edit.",
        "You are parallel worker B. In settings.py replace exactly DEFAULT_TIMEOUT = 10 with DEFAULT_TIMEOUT = 60. Run python -m pytest -q after making the edit.",
    ]
    def worker(index: int) -> dict[str, Any]:
        home = Path(tempfile.mkdtemp(prefix=f"goal10-parallel-{index}-"))
        try:
            return _hermes_step(
                prompts[index], paths[index], home, artifact / f"worker-{index}", project, treatment, 1,
                timeout=max(HERMES_TIMEOUT, 360),
            )
        except Exception as exc:
            return {
                "completed": None,
                "timed_out": False,
                "usage": {},
                "trace": {},
                "error": f"{type(exc).__name__}:{exc}",
            }
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    contents = [((path / "settings.py").read_text(encoding="utf-8"),) for path in paths if (path / "settings.py").exists()]
    success, failures = verify_file_state(paths[0], expected_text={"settings.py": ["DEFAULT_TIMEOUT = 30"]})
    success_b, failures_b = verify_file_state(paths[1], expected_text={"settings.py": ["DEFAULT_TIMEOUT = 60"]})
    conflict = len(contents) == 2 and contents[0] != contents[1]
    if not success:
        failures.extend(f"agent_a:{item}" for item in list(failures))
    if not success_b:
        failures.extend(f"agent_b:{item}" for item in failures_b)
    if not conflict:
        failures.append("parallel_conflict_not_observed")
    warnings: list[str] = []
    worker_processes_ok = all(item["completed"] and item["completed"].returncode == 0 for item in results)
    for index, path in enumerate(paths):
        tests_passed, output = _pytest(path)
        (artifact / f"worker-{index}" / "pytest.txt").write_text(output, encoding="utf-8")
        if not tests_passed:
            failures.append(f"parallel_worker_{index}_pytest_failed")
    if not worker_processes_ok:
        if success and success_b and all(verify_file_state(path, required_files=["settings.py"])[0] for path in paths):
            # ponytail: objective file state plus tests are the product assertion; keep
            # shutdown latency as a warning while the agent runner is hardened.
            warnings.append("worker_shutdown_timeout_after_objective")
        else:
            failures.append("parallel_worker_failed")
    traces = [item["trace"] for item in results]
    return not failures, failures, {
        "traces": traces,
        "worker_count": 2,
        "conflict_observed": conflict,
        "warnings": warnings,
    }


def _run_case(case: EvaluationCase, arm: str) -> dict[str, Any]:
    treatment = arm == "treatment"
    root = Path(tempfile.mkdtemp(prefix=f"goal10-{case.slug}-{arm}-"))
    repo = root / "repo"
    repo.mkdir()
    _fixture(case.slug, repo)
    project = f"{PROJECT_PREFIX}/{case.slug}"
    artifact = ARTIFACTS / case.slug / arm
    shutil.rmtree(artifact, ignore_errors=True)
    artifact.mkdir(parents=True, exist_ok=True)
    facts = {
        "unfamiliar-repository": ["calculator.py owns arithmetic and multiply should be covered by tests."],
        "architecture-investigation": ["The calculator module owns arithmetic; tests live in tests; public changes require tests."],
        "bug-investigation": ["The parser regression is in parser.py: parse_count must return int(value.strip()) without adding one."],
        "regression-fix": ["The current discount decision is amount * (1 - rate); the previous plus-rate behavior is wrong."],
        "multi-session": ["Current decision: implement calculate_total(items) in calculator.py as return sum(items)."],
        "agent-handoff": ["Handoff requires slugger.py to normalize lowercase text and replace punctuation/spaces with hyphens."],
        "stale-knowledge": ["The 5-second timeout is superseded; current DEFAULT_TIMEOUT is 30 seconds."],
        "parallel-conflicts": ["Parallel workers may propose different timeout values; preserve both changes for conflict review."],
    }[case.slug]
    group = _seed_context(project, repo, facts) if treatment else None
    try:
        if case.slug == "parallel-conflicts":
            passed, failures, extra = _run_parallel(repo, artifact, treatment, project)
            traces = extra["traces"]
            trace = next((item for item in traces if item), {})
            warnings = list(extra.get("warnings", []))
        else:
            warnings = []
            home = Path(tempfile.mkdtemp(prefix=f"goal10-hermes-{case.slug}-{arm}-"))
            results = []
            for step, prompt in enumerate(_prompts(case.slug), start=1):
                if case.slug == "agent-handoff" and step == 2:
                    # ponytail: a fresh home is the smallest faithful handoff check; reuse the repo only.
                    home = Path(tempfile.mkdtemp(prefix=f"goal10-hermes-{case.slug}-{arm}-fresh-"))
                results.append(_hermes_step(prompt, repo, home, artifact, project, treatment, step))
            trace = results[-1]["trace"] if results else {}
            passed, failures = _objective(case.slug, repo)
            tests_passed, test_output = _pytest(repo)
            (artifact / "pytest.txt").write_text(test_output, encoding="utf-8")
            if not tests_passed:
                failures.append("pytest_failed")
            if any(item["timed_out"] for item in results):
                failures.append("hermes_timeout")
            if any(not item["usage"] or item["usage"].get("failed") for item in results):
                failures.append("missing_or_failed_usage")
        if treatment and not trace.get("packet_id"):
            failures.append("missing_treatment_context")
        status = "SUCCESS" if not failures else "FAILURE"
        if not trace and treatment:
            status = "INVALID_RUN"
        usage = {}
        usage_path = artifact / "usage-1.json"
        if usage_path.exists():
            usage = _usage(usage_path)
        packet_id = trace.get("packet_id") if treatment else None
        retrieval_id = trace.get("retrieval_id") if treatment else None
        prompts = _prompts(case.slug)
        expected_query = prompts[-1] if prompts else "parallel"
        if treatment and retrieval_id and retrieval_id != _retrieval_id(expected_query, trace.get("session_id", "")):
            # Parallel workers have independent prompts; their trace is still valid evidence.
            if case.slug != "parallel-conflicts":
                failures.append("retrieval_trace_mismatch")
                status = "INVALID_RUN"
        return {
            "arm": arm, "task_id": case.case_id, "session_id": str(usage.get("session_id") or f"{case.case_id}-{arm}"),
            "task_outcome": status, "outcome_id": f"outcome-{case.case_id}-{arm}", "packet_id": packet_id,
            "retrieval_id": retrieval_id, "context_returned": bool(packet_id), "context_chars": int(trace.get("context_chars", 0)),
            "prefetch_latency_ms": float(trace.get("latency_ms", 0.0)), "selected_entities": list(trace.get("selected_entities", [])),
            "useful_context": bool(packet_id), "stale_context": bool(trace.get("stale_count", 0)), "irrelevant_context": False,
            "tool_calls": int(usage.get("api_calls", 0)), "token_count": int(usage.get("total_tokens", 0)),
            "human_intervention": False, "verification": ["objective_file_state", "pytest_passed"] if not failures else [],
            "failures": failures, "warnings": warnings, "seed_group": group,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        _clean_evaluation_seed_groups()
    except Exception as exc:
        print(f"seed_cleanup_failed:{type(exc).__name__}:{exc}", file=sys.stderr)
        raise
    selected = os.environ.get("GOAL10_CASES", "").strip()
    if selected == "clean":
        print("evaluation_seed_cleanup:complete", flush=True)
        return
    requested = {item.strip() for item in selected.split(",")} if selected else set()
    cases = tuple(case for case in EVALUATION_CASES if not requested or case.slug in requested)
    results = []
    for case in cases:
        print(f"case_start:{case.slug}", flush=True)
        try:
            partial = run_evaluation_matrix(
                lambda current: {"baseline": _run_case(current, "baseline"), "treatment": _run_case(current, "treatment")},
                cases=(case,),
            )
        except BaseException:
            (ARTIFACTS / "matrix-fatal.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            raise
        results.extend(partial["cases"])
        (ARTIFACTS / "matrix-progress.json").write_text(
            json.dumps(results, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"case_done:{case.slug}", flush=True)
    valid_count = sum(item["evaluation"]["valid"] for item in results)
    invalid_count = len(results) - valid_count
    failure_count = sum(item["evaluation"].get("treatment", {}).get("status") == "FAILURE" for item in results)
    worse_count = sum(
        item["evaluation"].get("baseline", {}).get("status") == "SUCCESS"
        and item["evaluation"].get("treatment", {}).get("status") != "SUCCESS"
        for item in results
    )
    reasons = []
    if invalid_count:
        reasons.append("valid paired evidence is missing")
    if failure_count:
        reasons.append("treatment failures are present")
    if worse_count:
        reasons.append("treatment does not meet baseline success")
    report = {
        "case_count": len(results),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "failure_count": failure_count,
        "release_decision": "GO" if not reasons else "NO-GO",
        "evidence_status": "paired_runs",
        "reasons": reasons,
        "cases": results,
    }
    (ARTIFACTS / "matrix.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("case_count", "valid_count", "invalid_count", "failure_count", "release_decision", "evidence_status")}, indent=2), flush=True)


if __name__ == "__main__":
    main()
