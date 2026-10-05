"""Bounded authorized MCP projection of a task working set.

Resources, not tools. The public tool surface stays where it is; mirroring
internal runtime operations as new tools would grow the contract without giving
a client anything it cannot already read here.

Capability negotiation is measured, not assumed. The installed MCP SDK derives
`ResourcesCapability` with `subscribe=False` hardcoded whenever a list-resources
handler is registered, so this server cannot advertise resource subscription
through the supported path, regardless of the protocol version a client asks
for. `negotiate()` reports that and selects the polling fallback.

A resource update, were one available, would be a signal to fetch and check. It
is not evidence that context was inserted into a session, and it is never
evidence that an agent reconsidered an action. The projection says so in its own
payload rather than relying on the reader to know.
"""
from dataclasses import dataclass
import json

#: Bounds for one projection payload. A client that needs more resynchronizes.
MAX_ITEMS = 128
MAX_CHARACTERS = 65_536

URI_PREFIX = "memex+task://"
NOTICE = ("A resource read reports the working set as checked at this view. It is not "
          "evidence of context insertion into an agent session, and not evidence of "
          "action reconsideration.")


@dataclass(frozen=True)
class Negotiation:
    """What this server and client can actually agree on."""
    protocol_version: str | None
    subscribe: bool
    mode: str
    reason: str

    @property
    def fallback(self) -> str:
        return "poll the resource before a dependent action" if not self.subscribe else ""


def negotiate(server, client_capabilities=None, protocol_version=None) -> Negotiation:
    """Report the agreed resource mode from real server and client capabilities."""
    from mcp.server.lowlevel import NotificationOptions

    capabilities = server.get_capabilities(NotificationOptions(), {})
    resources = capabilities.resources
    if resources is None:
        return Negotiation(protocol_version, False, "unavailable",
                           "server exposes no resource handlers")

    client_wants = True
    if client_capabilities is not None:
        # A client that declares no resource capability is not offered one.
        client_wants = getattr(client_capabilities, "resources", None) is not None

    if not bool(getattr(resources, "subscribe", False)):
        return Negotiation(protocol_version, False, "poll",
                           "installed MCP SDK advertises resources.subscribe=false")
    if not client_wants:
        return Negotiation(protocol_version, False, "poll",
                           "client declared no resource capability")
    return Negotiation(protocol_version, True, "subscription", "negotiated")


class LiveWorkingSetProjection:
    """Read-only projection of a task working set for an authorized session.

    Authorization is the control plane's own: the task store authenticates the
    principal before any lookup, and a task that does not belong to the session
    is not found. A URI is not a capability.
    """

    def __init__(self, tasks, *, resolve_session, clock, max_items: int = MAX_ITEMS,
                 max_characters: int = MAX_CHARACTERS):
        self.tasks = tasks
        self.resolve_session = resolve_session
        self.clock = clock
        self.max_items = max_items
        self.max_characters = max_characters

    @staticmethod
    def uri(task_id: str) -> str:
        return f"{URI_PREFIX}{task_id}/working-set"

    @staticmethod
    def task_id(uri: str) -> str:
        if not str(uri).startswith(URI_PREFIX):
            raise ValueError("not a memex task resource URI")
        remainder = str(uri)[len(URI_PREFIX):]
        task_id, separator, suffix = remainder.partition("/")
        if not task_id or separator != "/" or suffix != "working-set":
            raise ValueError("unknown memex task resource")
        return task_id

    def list_resources(self) -> list[dict]:
        """Only the requesting session's own tasks are listed."""
        session = self.resolve_session()
        if session is None:
            return []
        self.tasks.authorize(session)
        with self.tasks.connection() as db:
            rows = db.execute(
                "SELECT task_id FROM live_tasks WHERE session=? AND expires>? ORDER BY task_id",
                (session.model_dump_json(), self.clock())).fetchall()
        return [{"uri": self.uri(row["task_id"]),
                 "name": f"memex working set {row['task_id']}",
                 "description": NOTICE,
                 "mimeType": "application/json"} for row in rows]

    def read_resource(self, uri: str) -> str:
        session = self.resolve_session()
        if session is None:
            raise PermissionError("no authenticated principal for this request")
        task_id = self.task_id(uri)
        # Raises for another session's task, an expired task, or a bad principal.
        state = self.tasks.get(task_id, session, now=self.clock())

        items = [{"claim_id": item.claim_id, "revision_id": item.revision_id,
                  "assertion": item.assertion, "authority": item.authority,
                  "status": item.status, "reason": item.reason}
                 for item in state.baseline[:self.max_items]]
        payload = {
            "schema_version": "memex.live.v1",
            "notice": NOTICE,
            "task_id": state.task_id,
            "view_id": state.view.view_id,
            "repo_id": state.view.repo_id,
            "worktree_id": state.view.worktree_id,
            "acknowledged_sequence": state.ack_sequence,
            "stream_sequence": state.sequence,
            "pending_correction": state.pending is not None,
            "expires_at": state.expires_at,
            "items": items,
            "complete": len(state.baseline) <= self.max_items,
        }
        rendered = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        if len(rendered) > self.max_characters:
            # Report an incomplete projection rather than certify a truncated one.
            payload["items"] = []
            payload["complete"] = False
            payload["resync_required"] = True
            payload["reason"] = "projection_budget_exceeded"
            rendered = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return rendered


def attach_live_resources(server, projection: LiveWorkingSetProjection) -> Negotiation:
    """Register the read-only projection on an existing MCP server.

    This adds resource handlers only. No tool is added, renamed or removed.
    """
    @server.list_resources()
    async def _list():
        from mcp.types import Resource

        return [Resource(**entry) for entry in projection.list_resources()]

    @server.read_resource()
    async def _read(uri):
        return projection.read_resource(str(uri))

    return negotiate(server)
