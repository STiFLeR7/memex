"""Read-only Hermes MemoryProvider integration for memex.

The Hermes dependency is optional at import time so the core memex package
continues to work without Hermes installed. Hermes loads this module through
the ``hermes_agent.memory_providers`` entry point when both packages coexist.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Coroutine

from memex.config import canonical_repo_path, resolve_project_id
from memex.context.packet import ContextPacket, PacketBudget, validate_packet_metadata
from memex.context.selection import select_context

logger = logging.getLogger(__name__)

try:  # Hermes is an optional host dependency.
    from agent.memory_provider import MemoryProvider
except ImportError:  # pragma: no cover - exercised by the standalone package
    class MemoryProvider:  # type: ignore[no-redef]
        """Small fallback base for importing/testing memex without Hermes."""


Selector = Callable[..., Coroutine[Any, Any, ContextPacket]]
DEFAULT_TIMEOUT_SECONDS = 7.0
DEFAULT_MAX_ITEMS = 8
DEFAULT_MAX_CHARS = 12_000


def _env_or(config: dict[str, Any], key: str, env_key: str, default: Any) -> Any:
    if key in config:
        return config[key]
    return os.getenv(env_key, default)


def _load_plugin_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import cfg_get, load_config_readonly

        config = load_config_readonly()
        return cfg_get(config, "plugins", "memex", default={}) or {}
    except Exception:
        return {}


def _positive_float(value: Any, default: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_int(value: Any, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _retrieval_id(query: str, session_id: str) -> str:
    digest = hashlib.sha256(f"{session_id}\n{query}".encode()).hexdigest()[:24]
    return f"hermes-prefetch-{digest}"


def _run_with_timeout(
    operation: Callable[[], Coroutine[Any, Any, ContextPacket]],
    timeout: float,
) -> ContextPacket | None:
    """Run the async selector from Hermes' synchronous provider contract.

    The worker is daemonized because a wedged graph/network call must never
    hold Hermes shutdown hostage after the bounded prefetch window expires.
    """
    result: dict[str, ContextPacket] = {}
    error: dict[str, BaseException] = {}

    def run() -> None:
        try:
            result["packet"] = asyncio.run(asyncio.wait_for(operation(), timeout=timeout))
        except BaseException as exc:  # converted to fail-open by the caller
            error["error"] = exc

    worker = threading.Thread(target=run, daemon=True, name="memex-prefetch")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return None
    if error:
        raise error["error"]
    return result.get("packet")


class HermesMemexProvider(MemoryProvider):
    """Automatic, read-only engineering context for Hermes prefetch."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        selector: Selector = select_context,
    ) -> None:
        config = dict(config) if config is not None else _load_plugin_config()
        self._config = config
        self._selector = selector
        self._timeout = _positive_float(
            _env_or(config, "prefetch_timeout_seconds", "MEMEX_PREFETCH_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            DEFAULT_TIMEOUT_SECONDS,
        )
        self._max_items = _positive_int(
            _env_or(config, "max_items", "MEMEX_CONTEXT_MAX_ITEMS", DEFAULT_MAX_ITEMS),
            DEFAULT_MAX_ITEMS,
        )
        self._max_chars = _positive_int(
            _env_or(config, "max_chars", "MEMEX_CONTEXT_MAX_CHARS", DEFAULT_MAX_CHARS),
            DEFAULT_MAX_CHARS,
        )
        self._repo_path: str | None = None
        self._project_id: str | None = None
        self._session_id = ""
        self._agent_identity = ""
        self._initialized = False

    @property
    def name(self) -> str:
        return "memex"

    def is_available(self) -> bool:
        """Fast, side-effect-free availability check for Hermes discovery."""
        return callable(self._selector)

    def unavailable_reason(self) -> str:
        return "memex retrieval dependencies are unavailable"

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        configured_repo = self._config.get("repo_path") or os.getenv("MEMEX_REPO_ROOT")
        repo_path = kwargs.get("repo_path") or configured_repo or str(Path.cwd())
        self._repo_path = canonical_repo_path(str(repo_path))
        self._project_id = (
            kwargs.get("project_id")
            or self._config.get("project_id")
            or os.getenv("MEMEX_PROJECT_ID")
            or resolve_project_id(self._repo_path or str(Path.cwd()))
        )
        self._session_id = session_id or ""
        self._agent_identity = str(kwargs.get("agent_identity") or "hermes")
        self._initialized = True

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {"key": "repo_path", "description": "Repository path; defaults to Hermes CWD", "required": False},
            {"key": "project_id", "description": "Optional shared memex project identity", "required": False},
            {"key": "prefetch_timeout_seconds", "description": "Maximum read timeout", "default": DEFAULT_TIMEOUT_SECONDS, "type": "number"},
            {"key": "max_items", "description": "Maximum context items", "default": DEFAULT_MAX_ITEMS, "type": "integer"},
            {"key": "max_chars", "description": "Maximum rendered context characters", "default": DEFAULT_MAX_CHARS, "type": "integer"},
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        """Persist non-secret provider settings in Hermes' profile config."""
        try:
            import yaml

            config_path = Path(hermes_home) / "config.yaml"
            existing = {}
            if config_path.exists():
                existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            existing.setdefault("plugins", {})["memex"] = dict(values)
            config_path.write_text(
                yaml.safe_dump(existing, default_flow_style=False),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("Unable to save Hermes memex configuration", exc_info=True)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._initialized or not self._repo_path or not query or not query.strip():
            return ""

        active_session = session_id or self._session_id
        retrieval_id = _retrieval_id(query, active_session)
        started = time.perf_counter()

        async def operation() -> ContextPacket:
            return await self._selector(
                query,
                repo=self._repo_path,
                project=self._project_id,
                session_id=active_session,
                agent_id=self._agent_identity,
                harness="hermes",
                retrieval_id=retrieval_id,
                budget=PacketBudget(max_items=self._max_items, max_chars=self._max_chars),
            )

        try:
            packet = _run_with_timeout(operation, self._timeout)
            if packet is None:
                logger.debug("memex prefetch timed out after %.2fs", self._timeout)
                self._trace(
                    retrieval_id=retrieval_id,
                    session_id=active_session,
                    status="timeout",
                    started=started,
                )
                return ""
            if not isinstance(packet, ContextPacket):
                packet = ContextPacket.model_validate(packet)
            metadata_issues = validate_packet_metadata(packet)
            if metadata_issues:
                logger.warning("memex prefetch dropped context with invalid metadata: %s", metadata_issues)
                self._trace(
                    retrieval_id=retrieval_id,
                    session_id=active_session,
                    status="invalid_metadata",
                    packet=packet,
                    started=started,
                )
                return ""
            self._trace(
                retrieval_id=retrieval_id,
                session_id=active_session,
                status="success",
                packet=packet,
                started=started,
            )
            return packet.render_text()
        except Exception:
            logger.debug("memex prefetch failed; continuing without context", exc_info=True)
            self._trace(
                retrieval_id=retrieval_id,
                session_id=active_session,
                status="failure",
                started=started,
            )
            return ""

    def _trace(
        self,
        *,
        retrieval_id: str,
        session_id: str,
        status: str,
        started: float,
        packet: ContextPacket | None = None,
    ) -> None:
        path = os.getenv("MEMEX_PREFETCH_TRACE_PATH")
        if not path:
            return
        record = {
            "retrieval_id": retrieval_id,
            "session_id": session_id,
            "status": status,
            "packet_id": packet.packet_id if packet else None,
            "item_count": len(packet.items) if packet else 0,
            "selected_entities": [item.ref for item in packet.items] if packet else [],
            "context_chars": len(packet.render_text()) if packet else 0,
            "stale_count": sum(item.freshness == "stale" for item in packet.items) if packet else 0,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        try:
            with Path(path).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            logger.debug("Unable to write optional memex prefetch trace", exc_info=True)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Goal 3 is read-only: do not retain transcripts or turn content."""

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

    def shutdown(self) -> None:
        self._initialized = False


def register(ctx: Any) -> None:
    """Hermes plugin/entry-point registration hook."""
    ctx.register_memory_provider(HermesMemexProvider())
