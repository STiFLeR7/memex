"""The hook service: one long-lived process that answers memex hook events.

`memex/hook_client.py` starts it and forwards events to it. Keeping the
interpreter, the graph connection, schema state, parser workers and parsed
structures alive is what brings a warm action check under the latency gate.
Each event still builds its own adapter and engine over the shared connection,
so concurrent events behave as separate one-shot hooks always have.

It listens on 127.0.0.1 only and answers only requests that carry the token in
its address file under the user's home. It exits after an idle period.
"""
import asyncio
import hmac
import json
import os
import secrets
import sys
import time
from pathlib import Path


def _handler(argv: list[str]):
    module = argv[0]
    if module == "memex.evaluation.arms":
        from memex.evaluation import arms
        return lambda payload, write, stores, created: arms.serve(argv[1:], payload, write, stores, created)
    from memex.integrations.host_adapter import serve_hook
    if module == "memex.integrations.claude_code":
        from memex.integrations.claude_code import ClaudeCodeAdapter, load_capability
        return lambda payload, write, stores, created: serve_hook(ClaudeCodeAdapter, load_capability,
                                                                  payload, write, stores)
    if module == "memex.integrations.codex":
        from memex.integrations.codex import CodexAdapter, load_capability
        return lambda payload, write, stores, created: serve_hook(CodexAdapter, load_capability,
                                                                  payload, write, stores)
    raise SystemExit(f"memex.hookd: no hook service for {module}")


class Stores:
    """One driver and its schema-aware stores per backend, for the service's life."""

    def __init__(self):
        self._made: dict = {}
        self._lock = asyncio.Lock()

    async def __call__(self, uri, user, password):
        async with self._lock:
            key = (uri, user, password)
            if key not in self._made:
                from graphiti_core.driver.neo4j_driver import Neo4jDriver

                from memex.runtime.graph import StructuralGraphStore
                from memex.runtime.supports import ClaimStore
                driver = await asyncio.to_thread(Neo4jDriver, uri, user, password)
                self._made[key] = (driver, StructuralGraphStore(driver), ClaimStore(driver))
            _, graph, claims = self._made[key]
            return graph, claims

    async def close(self):
        for driver, _, _ in self._made.values():
            await driver.close()


async def serve(key: str, argv: list[str]) -> None:
    from memex.integrations.host_adapter import unavailable
    from memex.runtime import views

    views.DISCOVERY_CACHE = {}
    handler = _handler(argv)
    stores = Stores()
    token = secrets.token_hex(16)
    idle = float(os.environ.get("MEMEX_HOOKD_IDLE_SECONDS") or 900)
    state = {"last": time.monotonic(), "active": 0}

    async def answer(reader, writer):
        state["active"] += 1
        written = []
        try:
            request = json.loads(await reader.readline() or b"{}")
            if not hmac.compare_digest(str(request.get("token")), token):
                return
            try:
                payload = json.loads(request.get("payload") or "{}")
            except json.JSONDecodeError:
                payload = None

            async def write(text: str) -> float | None:
                """Send the hook's output; return when the client has printed it."""
                written.append(text)
                writer.write(json.dumps({"stdout": text}).encode() + b"\n")
                await writer.drain()
                ack = await asyncio.wait_for(reader.readline(), 30)
                if not ack:
                    raise ConnectionError("hook client closed before printing")
                return json.loads(ack).get("printed")

            if payload is None:
                await write(json.dumps({"systemMessage": "memex: unparsable hook payload"}) + "\n")
                return
            try:
                await handler(payload, write, stores, request.get("created"))
            except Exception as exc:  # noqa: BLE001 - a memex fault must not gate the host
                if not written:
                    event = payload.get("hook_event_name") or "PreToolUse"
                    await write(json.dumps(unavailable(exc).to_payload(event)) + "\n")
        except (OSError, ValueError, asyncio.TimeoutError):
            pass  # The client is gone or spoke nonsense; it fails open on its side.
        finally:
            try:
                writer.close()
            except (OSError, RuntimeError):
                pass
            state["active"] -= 1
            state["last"] = time.monotonic()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    directory = Path.home() / ".memex" / "hookd"
    directory.mkdir(parents=True, exist_ok=True)
    address = directory / f"{key}.json"
    staged = directory / f"{key}.{os.getpid()}.tmp"
    staged.write_text(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token,
                                  "pid": os.getpid(), "argv": argv}), encoding="utf-8")
    os.replace(staged, address)
    (directory / f"{key}.starting").unlink(missing_ok=True)
    from memex.runtime.parsing import parse_sources
    warming = asyncio.create_task(parse_sources({"memex-hookd-warmup.py": b""}))  # Start the parser workers now.
    try:
        while True:
            await asyncio.sleep(min(idle, 30))
            if state["active"] == 0 and time.monotonic() - state["last"] >= idle:
                break
            try:
                if json.loads(address.read_text(encoding="utf-8")).get("pid") != os.getpid():
                    idle = min(idle, 60)  # A newer service took over; finish what is in flight.
            except (OSError, ValueError):
                idle = min(idle, 60)
    finally:
        warming.cancel()
        server.close()
        try:
            if json.loads(address.read_text(encoding="utf-8")).get("pid") == os.getpid():
                address.unlink()
        except (OSError, ValueError):
            pass
        await stores.close()


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    asyncio.run(serve(argv[0], argv[1:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
