/**
 * Site content. Every claim here traces to a file in the memex repository —
 * the `source` fields are not decoration, they are the citation.
 */

export type Stage = {
  readonly n: string;
  readonly title: string;
  readonly path: string;
  readonly caption: string;
  readonly artifact: string;
};

export const STAGES: readonly Stage[] = [
  {
    n: '01',
    title: 'Watcher',
    path: 'memex/watcher/',
    caption:
      'Filesystem events, Git hooks, commit polling and lockfile changes enter one router. The watcher owns detection and debouncing, and nothing durable.',
    artifact: 'EMITS → routed change events',
  },
  {
    n: '02',
    title: 'Extractor',
    path: 'memex/extractor/',
    caption:
      'tree-sitter produces symbols and conservative call edges; lockfile parsers produce dependencies and imports. A callee is stored as a bare name until write time.',
    artifact: 'EMITS → SymbolDelta, CallEdge',
  },
  {
    n: '03',
    title: 'Synthesizer',
    path: 'memex/synthesizer/',
    caption:
      'On a commit, Gemini Flash reads the message and diff and proposes Decision records at base confidence 0.6, unvalidated. The only LLM call on the write path.',
    artifact: 'EMITS → candidate Decision',
  },
  {
    n: '04',
    title: 'Graph',
    path: 'memex/graph/',
    caption:
      'Graphiti episode ingestion, then a post-hoc Cypher SET for the memex-specific structured fields. Neo4j is the only authoritative state in the system.',
    artifact: 'HOLDS → entities + typed relations',
  },
  {
    n: '05',
    title: 'Temporal',
    path: 'graph/confidence.py',
    caption:
      'Confidence is recomputed on every read from base_confidence, validation state and time since reinforcement. Nothing is stored, so nothing can drift.',
    artifact: 'DERIVES → confidence, freshness',
  },
  {
    n: '06',
    title: 'Retrieval',
    path: 'memex/context/',
    caption:
      'Scope, validity filter, candidates, composite rank, conflict check, bounded projection, trace. A packet over budget is rejected rather than truncated.',
    artifact: 'EMITS → ContextPacket',
  },
] as const;

export type ToolClass = 'read' | 'write' | 'analytic';

export type Tool = {
  readonly cls: ToolClass;
  readonly name: string;
  readonly moment: string;
  readonly input: string;
  readonly returns: string;
};

/** Verified against memex/mcp_server/server.py — 14 registered tools. */
export const TOOLS: readonly Tool[] = [
  { cls: 'read', name: 'get_project_context', moment: 'Session orientation', input: '{ scope?, repo?, project? }', returns: 'A cluster-level briefing held under 1500 tokens by the formatter, regardless of repository size.' },
  { cls: 'read', name: 'get_symbol_context', moment: 'Before changing a symbol', input: '{ symbol_name, file?, repo?, project? }', returns: 'A symbol plus its callers, callees, and linked decisions and problems.' },
  { cls: 'read', name: 'get_recent_decisions', moment: 'Recover recent choices', input: '{ days?, module?, repo?, project? }', returns: 'Recent Decision records, with conflict detection run across the result set.' },
  { cls: 'read', name: 'get_open_problems', moment: 'Find unresolved work', input: '{ module?, repo?, project? }', returns: 'Open Problem records ordered by severity.' },
  { cls: 'read', name: 'search_context', moment: 'Broad discovery', input: '{ query, top_k: 1..20, repo?, project? }', returns: 'Graphiti search after expiry filtering, composite reranking, and a per-factor score breakdown.' },
  { cls: 'read', name: 'get_engineering_context', moment: 'Bounded context packet', input: '{ query, top_k?, repo | project, allow_historical? }', returns: 'A ContextPacket with provenance, freshness, selection reasons and source references.' },
  { cls: 'read', name: 'get_stale_context', moment: 'Question aging knowledge', input: '{ threshold?, repo?, project? }', returns: 'Live relations whose computed confidence has fallen below the requested threshold.' },
  { cls: 'read', name: 'get_context_briefing', moment: 'Prime a session', input: '{ max_tokens?, scope?, repo?, project? }', returns: 'A token-budgeted sequence of summaries, decisions, problems and stale items. Default budget 2000.' },
  { cls: 'write', name: 'record_decision', moment: 'Persist a choice', input: '{ text, module?, rationale?, corroborates?, supersedes? }', returns: 'A governed write. Corroboration reinforces; supersession expires the predecessor’s outgoing edges.' },
  { cls: 'write', name: 'record_problem', moment: 'Record a known issue', input: '{ text, module?, severity?, repo? }', returns: 'A governed Problem record at base confidence 0.7.' },
  { cls: 'write', name: 'resolve_problem', moment: 'Close recorded work', input: '{ problem_id, resolution_text, repo? }', returns: 'A RESOLVED_BY relation and a closed state.' },
  { cls: 'write', name: 'invalidate_edge', moment: 'Retire a relation', input: '{ edge_id, reason, repo? }', returns: 'Sets expired_at. The edge leaves live traversal; it is not deleted.' },
  { cls: 'analytic', name: 'explain_change', moment: 'Understand a commit', input: '{ commit_sha, repo? }', returns: 'Cross-references the diff with linked Decision and Problem records, grounded by Gemini Pro.' },
  { cls: 'analytic', name: 'predict_impact', moment: 'Estimate blast radius', input: '{ file_path, repo? }', returns: 'Ranked modules via calls, imports and decision links. Pure graph traversal with no LLM call.' },
] as const;

export const TOOL_GROUPS: Record<ToolClass, string> = {
  read: 'Read · 8',
  write: 'Write · 4',
  analytic: 'Analytic · 2',
};

export type Capability = {
  readonly num: string;
  readonly key: string;
  readonly headline: string;
  readonly emphasis: string;
  readonly body: string;
  readonly source: string;
};

export const CAPABILITIES: readonly Capability[] = [
  {
    num: '01', key: 'Observe',
    headline: 'Repository motion becomes', emphasis: 'events.',
    body: 'Filesystem watchers, Git hooks, commit polling and lockfile changes enter one router. The watcher owns "something changed" and nothing durable.',
    source: 'memex/watcher/ · daemon · event_router',
  },
  {
    num: '02', key: 'Extract',
    headline: 'Structure becomes', emphasis: 'typed records.',
    body: 'tree-sitter produces symbols and conservative call edges; lockfile parsers produce dependencies. On a commit, Gemini Flash can propose Decision records at base confidence 0.6, unvalidated.',
    source: 'memex/extractor/ · memex/synthesizer/',
  },
  {
    num: '03', key: 'Expire',
    headline: 'Every claim carries an', emphasis: 'end condition.',
    body: 'Live traversal filters expired_at IS NULL. Decisions supersede, corroborate, or get invalidated. Confidence is a pure function of base value, validation state and time since reinforcement.',
    source: 'memex/graph/confidence.py · archive.py',
  },
  {
    num: '04', key: 'Serve',
    headline: 'Agents receive bounded', emphasis: 'evidence.',
    body: 'A ContextPacket carries at most 8 items and 12,000 characters, each with provenance, freshness and a selection reason. Over budget, it is rejected, never silently truncated.',
    source: 'memex/context/packet.py · selection.py',
  },
] as const;

export type LifecycleState = {
  readonly n: string;
  readonly title: string;
  readonly body: string;
  /** Confidence used for the halftone field density and the bar fill. */
  readonly conf: number;
  /** Null when the record has left live traversal entirely. */
  readonly readout: string | null;
  readonly bar: number;
};

export const NAV_LINKS = [
  { href: '#thesis', label: 'Thesis' },
  { href: '#plane', label: 'System' },
  { href: '#caps', label: 'Capabilities' },
  { href: '#motion', label: 'Temporal' },
  { href: '#artifacts', label: 'Surface' },
] as const;
