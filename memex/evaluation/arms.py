"""Phase 5 comparison arms, as host-adapter variants. Evaluation only.

Every arm with hooks uses the same host adapter, the same session-start packet
selection and the same declared-mutation boundaries. What differs is what
happens at those boundaries:

* **B** delivers the initial packet and never checks an action.
* **C** retrieves fresh context before each declared mutation. If it differs
  from what this session was last given, the edit is denied with the fresh
  context; otherwise the edit proceeds silently.
* **D** delivers hash-bound notes. If a file a note cites has changed since
  the session last saw it, the edit is denied with a re-read warning, and the
  hashes are rebound.
* **E** is the shipped Live Context adapter, unmodified.
* **E-noinval** never invalidates: action checks always proceed.
* **E-norecon** detects the same corrections but never denies; the
  correction is delivered after the action, as context on its tool result.
* **E-full** denies like E but delivers the full current working set instead
  of a delta.

Arm A has no hooks at all. The arm for a checkout is read from
`<common>/memex/eval-arm.json`, written by the trial harness.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from memex.integrations.host_adapter import HookResponse

ARMS = ("A", "B", "C", "D", "E", "E-noinval", "E-norecon", "E-full")
HOOKED_ARMS = ARMS[1:]
ARM_VERSION = "phase5-arms.v1"


def arm_path(registration) -> Path:
    return Path(registration.common_dir) / "memex" / "eval-arm.json"


def set_arm(registration, arm: str) -> None:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm}")
    path = arm_path(registration)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"arm": arm, "version": ARM_VERSION}), encoding="utf-8")


def read_arm(registration) -> str:
    try:
        return json.loads(arm_path(registration).read_text(encoding="utf-8"))["arm"]
    except (OSError, ValueError, KeyError):
        return "E"


def timings_path(registration) -> Path:
    return Path(registration.common_dir) / "memex" / "eval-timings.jsonl"


# --------------------------------------------------------------------------- #
# Small per-session state for C and D
# --------------------------------------------------------------------------- #

def _state_db(adapter) -> sqlite3.Connection:
    db = sqlite3.connect(adapter.state_path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS eval_arm_state (harness TEXT NOT NULL, native_session_id TEXT NOT NULL, "
               "name TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(harness, native_session_id, name))")
    return db


def _get(adapter, native: str, name: str):
    db = _state_db(adapter)
    try:
        row = db.execute("SELECT value FROM eval_arm_state WHERE harness=? AND native_session_id=? AND name=?",
                         (adapter.HARNESS, native, name)).fetchone()
        return None if row is None else json.loads(row[0])
    finally:
        db.close()


def _put(adapter, native: str, name: str, value) -> None:
    db = _state_db(adapter)
    try:
        with db:
            db.execute("INSERT OR REPLACE INTO eval_arm_state VALUES(?,?,?,?)",
                       (adapter.HARNESS, native, name, json.dumps(value)))
    finally:
        db.close()


def _item_lines(items) -> list[str]:
    return [f"- [{item.status}, {item.authority}] {item.assertion}" for item in items]


def _fingerprint(items) -> list:
    return sorted([item.claim_id, item.revision_id, item.assertion, item.status, item.authority] for item in items)


async def fresh_items(adapter, session):
    """A fresh retrieval: every applicable claim, verified against current evidence now."""
    deadline = time.monotonic() + adapter.deadline_seconds
    _view, items, _coverage, _files, _complete = await adapter.engine._bounded_project(session, (), deadline)
    return items


def _declared(adapter, payload):
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    return adapter.declared_targets(tool_name, tool_input)


def _record(adapter, session, payload, gate, reason, **extra):
    adapter.trace.record(at=adapter.clock(), session=session, event="action_check",
                         adapter_version=adapter.adapter_version,
                         attempt_id=payload.get("tool_use_id") or None, tool_name=payload.get("tool_name"),
                         tool_input=payload.get("tool_input"), gate=gate, reason=reason, insertion="none", **extra)


# --------------------------------------------------------------------------- #
# Arm adapters
# --------------------------------------------------------------------------- #

def adapter_class(base, arm: str):
    """The adapter class for `arm`, derived from a host adapter class."""

    class ArmB(base):
        async def on_pre_tool_use(self, payload):
            _record(self, self.session_identity(payload.get("session_id", "")), payload, "advisory",
                    "arm_b_no_action_check")
            return HookResponse(reason="memex arm B: no action check")

    class ArmC(base):
        async def on_session_start(self, payload):
            response = await super().on_session_start(payload)
            native = payload.get("session_id", "")
            items = await fresh_items(self, self.session_identity(native))
            _put(self, native, "c_last", _fingerprint(items))
            return response

        async def on_pre_tool_use(self, payload):
            native = payload.get("session_id", "")
            session = self.session_identity(native)
            if _declared(self, payload) is None:
                _record(self, session, payload, "advisory", "opaque_action")
                return HookResponse(reason="memex arm C: opaque action")
            started = time.perf_counter()
            items = await fresh_items(self, session)
            self.check_ms = (time.perf_counter() - started) * 1000
            current = _fingerprint(items)
            if current == _get(self, native, "c_last"):
                _record(self, session, payload, "allowed", "fresh_retrieval_unchanged")
                return HookResponse(reason="memex arm C: fresh retrieval unchanged")
            _put(self, native, "c_last", current)
            text = self._cap("memex (fresh retrieval): the engineering context for this action, retrieved "
                             "now from current evidence. Check your proposed change against it before "
                             "proceeding:\n" + "\n".join(_item_lines(items)))[0]
            _record(self, session, payload, "prevented", "fresh_retrieval_changed")
            return HookResponse(decision="deny", reason=text)

    class ArmD(base):
        async def on_session_start(self, payload):
            await super().on_session_start(payload)  # binds the task; its packet is replaced by notes
            native = payload.get("session_id", "")
            items = await fresh_items(self, self.session_identity(native))
            # A hash-bound note cites every source it was written from, not only
            # the support set that currently happens to verify it.
            revisions, evidence, _ = await self.engine.claims.load(self.registration.repo_id)
            notes, bound = [], {}
            for number, item in enumerate(items, 1):
                claim = revisions.get(item.revision_id)
                paths = sorted({evidence[e].path for group in (claim.support_sets if claim else ())
                                for e in group if e in evidence and evidence[e].path})
                cited = []
                for path in paths:
                    digest = self._observed_digest(path)
                    if digest:
                        bound[path] = digest
                        cited.append(f"{path}@{digest[7:19]}")
                notes.append(f"Note {number}: {item.assertion} (sources: {', '.join(cited) or 'none'})")
            _put(self, native, "d_bound", bound)
            _put(self, native, "d_notes", notes)
            text = ("memex notes for this repository. Each note is bound to the exact source versions it "
                    "cites; you will be warned if a cited source changes.\n" + "\n".join(notes))
            # Notes replace the packet; they carry no delivery receipt.
            return HookResponse(additional_context=self._cap(text)[0])

        async def on_pre_tool_use(self, payload):
            native = payload.get("session_id", "")
            session = self.session_identity(native)
            if _declared(self, payload) is None:
                _record(self, session, payload, "advisory", "opaque_action")
                return HookResponse(reason="memex arm D: opaque action")
            started = time.perf_counter()
            bound = _get(self, native, "d_bound") or {}
            changed = sorted(p for p, digest in bound.items() if self._observed_digest(p) != digest)
            self.check_ms = (time.perf_counter() - started) * 1000
            if not changed:
                _record(self, session, payload, "allowed", "bound_sources_unchanged")
                return HookResponse(reason="memex arm D: bound sources unchanged")
            notes = [n for n in (_get(self, native, "d_notes") or []) if any(p in n for p in changed)]
            for path in changed:
                bound[path] = self._observed_digest(path)
            _put(self, native, "d_bound", bound)
            text = ("memex warning: source files cited by your notes changed since you received them: "
                    + ", ".join(changed) + ". Re-read them and reconsider this change before proceeding. "
                    "Affected notes:\n" + "\n".join(notes))
            _record(self, session, payload, "prevented", "bound_source_changed")
            return HookResponse(decision="deny", reason=self._cap(text)[0])

    class ArmE(base):
        pass

    class ArmEFull(base):
        def render_correction(self, check, delivered, observed, targets, dependencies=None):
            text = super().render_correction(check, delivered, observed, targets, dependencies)
            items = getattr(self, "_full_items", None) or ()
            return (text.split("\n", 1)[0] + "\nThe full current working set replaces everything you were "
                    "given before:\n" + "\n".join(_item_lines(items)))

        async def on_pre_tool_use(self, payload):
            if _declared(self, payload) is not None:
                self._full_items = await fresh_items(self, self.session_identity(payload.get("session_id", "")))
            return await super().on_pre_tool_use(payload)

    class ArmENoRecon(base):
        async def on_pre_tool_use(self, payload):
            response = await super().on_pre_tool_use(payload)
            if response.decision != "deny":
                return response
            native = payload.get("session_id", "")
            queued = _get(self, native, "deferred") or []
            if response.reason not in queued:
                queued.append(response.reason)
                _put(self, native, "deferred", queued)
            # The pending action is not reconsidered: it proceeds, and the
            # correction follows its result.
            return HookResponse(reason="memex arm E-norecon: correction deferred to after the action")

        async def on_post_tool_use(self, payload):
            response = await super().on_post_tool_use(payload)
            native = payload.get("session_id", "")
            queued = _get(self, native, "deferred") or []
            if not queued:
                return response
            _put(self, native, "deferred", [])
            delivered = _get(self, native, "deferred_delivered") or []
            _put(self, native, "deferred_delivered", delivered + queued)
            return HookResponse(additional_context=self._cap("\n\n".join(queued))[0])

    classes = {"B": ArmB, "C": ArmC, "D": ArmD, "E": ArmE, "E-noinval": ArmE, "E-norecon": ArmENoRecon,
               "E-full": ArmEFull}
    chosen = classes[arm]
    chosen.__name__ = f"{base.__name__}Arm{arm.replace('-', '_')}"
    return chosen


class NoInvalidationEngine:
    """E-noinval: the core never reports a changed support; every check proceeds."""

    def __init__(self, engine):
        self._engine = engine

    def __getattr__(self, name):
        return getattr(self._engine, name)

    async def check_action(self, request):
        check = await self._engine.check_action(request)
        if check.outcome in ("replan", "resync_required"):
            return check.model_copy(update={"outcome": "proceed", "delta": None, "reason": "invalidation_disabled"})
        return check


class TimedEngine:
    """Records how long the core's action check takes, in process."""

    def __init__(self, engine, sink: dict):
        self._engine, self._sink = engine, sink

    def __getattr__(self, name):
        return getattr(self._engine, name)

    async def check_action(self, request):
        started = time.perf_counter()
        try:
            return await self._engine.check_action(request)
        finally:
            self._sink["check_ms"] = (time.perf_counter() - started) * 1000


# --------------------------------------------------------------------------- #
# Hook entry point
# --------------------------------------------------------------------------- #

def _process_started() -> float | None:
    """This process's creation time (epoch seconds), so latency includes interpreter start."""
    if os.name != "nt":
        try:
            with open(f"/proc/{os.getpid()}/stat") as stream:
                start_ticks = int(stream.read().rsplit(")", 1)[1].split()[19])
            with open("/proc/uptime") as stream:
                uptime = float(stream.read().split()[0])
            return time.time() - uptime + start_ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None
    import ctypes
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(4)]
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    if not kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), *[ctypes.byref(t) for t in times]):
        return None
    created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return created / 1e7 - 11644473600


async def _run(host: str, payload: dict, stream) -> dict:
    from memex.integrations import claude_code, codex
    from memex.integrations.host_adapter import build_engine
    from memex.runtime.views import discover_repository

    base, load = ((claude_code.ClaudeCodeAdapter, claude_code.load_capability) if host == "claude"
                  else (codex.CodexAdapter, codex.load_capability))
    registration = discover_repository(payload.get("cwd") or ".")
    arm = read_arm(registration)
    metrics: dict = {"arm": arm, "host": host, "event": payload.get("hook_event_name"),
                     "tool": payload.get("tool_name"), "session": payload.get("session_id")}
    capability = load(registration)
    started = time.perf_counter()
    engine, driver = await build_engine(registration, capability, base.HARNESS)
    metrics["connect_ms"] = (time.perf_counter() - started) * 1000
    try:
        engine = TimedEngine(NoInvalidationEngine(engine) if arm == "E-noinval" else engine, metrics)
        adapter = adapter_class(base, arm)(engine, capability)
        refresh = adapter.engine.indexer.refresh

        async def timed_refresh(*args, **kwargs):
            began = time.perf_counter()
            try:
                return await refresh(*args, **kwargs)
            finally:
                metrics["refresh_ms"] = metrics.get("refresh_ms", 0.0) + (time.perf_counter() - began) * 1000
                metrics["refreshes"] = metrics.get("refreshes", 0) + 1
        adapter.engine.indexer.refresh = timed_refresh
        response = await adapter.dispatch(payload)
        if getattr(adapter, "check_ms", None) is not None:
            metrics["check_ms"] = adapter.check_ms
        emitted = adapter.emit(response, payload.get("hook_event_name") or "PreToolUse", stream)
        metrics["decision"] = response.decision
        metrics["chars"] = len(response.additional_context) + (len(response.reason) if response.decision else 0)
        return {"metrics": metrics, "registration": registration, "emitted": emitted}
    finally:
        await driver.close()


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    host = argv[0] if argv else "claude"
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"systemMessage": "memex: unparsable hook payload"}))
        return 0
    try:
        created = _process_started()
    except Exception:  # noqa: BLE001 - measurement must never break the hook
        created = None
    began = time.time()
    result = None
    try:
        result = asyncio.run(_run(host, payload, sys.stdout))
    except Exception as exc:  # noqa: BLE001 - fail open, and say so, like the product hook
        print(json.dumps(HookResponse(reason=f"memex: unavailable ({exc.__class__.__name__})",
                                      system_message="memex is unavailable; context freshness is unverified.")
                         .to_payload(payload.get("hook_event_name") or "PreToolUse")))
        result = {"metrics": {"host": host, "event": payload.get("hook_event_name"),
                              "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}}
    finally:
        ended = time.time()
        if result is not None:
            metrics = result["metrics"]
            metrics.update(at=ended, handler_ms=(ended - began) * 1000,
                           total_ms=None if created is None else (ended - created) * 1000)
            try:
                from memex.runtime.views import discover_repository
                registration = result.get("registration") or discover_repository(payload.get("cwd") or ".")
                path = timings_path(registration)
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as out:
                    out.write(json.dumps(metrics) + "\n")
            except Exception:  # noqa: BLE001 - measurement must never break the hook
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
