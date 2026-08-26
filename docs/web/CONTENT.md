# MEMEX WEBSITE CONTENT SYSTEM

Status: v0.9.0 released, editorial review 2026-08-26  
Authority: executable source and tests first; documentation is labelled when it is not independently verified.  
Current package version: `0.9.0` (`pyproject.toml`, `npm/package.json`, and `server.json`).

This is the canonical editorial source for a future multi-page website. It is
content strategy, not HTML or visual design.

## 01 — Editorial Foundation

### Core narrative

Repository work produces knowledge that ordinary file retrieval does not model
well: why a decision was made, which symbols depend on a module, which problem
is still open, and whether a fact is still live. memex observes repository and
Git activity, extracts structured facts, synthesises commit decisions when
configured, stores them in a temporal graph, and exposes scoped context to
coding agents through a protocol-neutral core. Hermes can receive bounded,
read-only context through its `MemoryProvider`; MCP remains the explicit
interoperability and write surface. The result is continuity with evidence and
expiry—not a claim of universal memory.

### Product positioning

**Trusted engineering context for agentic software engineering.** memex sits
beside a repository and its host agent: a protocol-neutral, graph-backed
context layer with temporal retrieval and governed writes. Hermes automatic
prefetch and MCP explicit lookup/write are adapters; memex is not an editor or
coding agent.

### Definitions

- **One-line:** memex maintains a temporal knowledge graph of repository facts,
  engineering decisions, problems, and code relationships, projected as
  bounded context for coding agents.
- **Short:** memex watches files and Git, extracts symbols/dependencies and
  commit decisions, writes them to Neo4j via Graphiti, and lets agents retrieve
  bounded, scoped context or record new engineering knowledge.
- **Technical:** Watcher events flow through tree-sitter and lockfile
  extraction; commit synthesis can use Gemini; Graphiti/Neo4j stores typed
  `Entity` records and relationships; query-time confidence, expiry filtering,
  reranking, and `ContextPacket` projection feed Hermes prefetch and a 14-tool
  MCP surface.

### Product promise

When the repository contains relevant evidence, an agent can receive or request
it through a bounded, scoped interface instead of reconstructing it from
scratch. The system shows where knowledge came from and when it stopped being
current; the host agent remains responsible for action and verification.

### Core problem and insight

The problem is not only missing text. It is missing continuity between changes,
relationships, decisions, and validity. A static README or raw vector result
does not provide the implemented combination of graph relations, repository
scope, source metadata, expiry, and governed writes. memex makes those signals
queryable.

### Why now

Multiple coding agents and repeated sessions make repository context a recurring
systems problem. This is a positioning rationale, not a market-size claim.

### What memex is / is not

**Is:** repository watcher; typed graph writer; temporal/confidence layer;
retrieval and `ContextPacket` projection; protocol-neutral core; MCP server;
CLI; Hermes read-only provider; optional cluster, memory-tool, HTTP, Docker,
VS Code, telemetry, and team surfaces where implemented.

**Is not:** a generic chatbot; an autonomous coding agent; a universal memory
store; a replacement for Hermes `MEMORY.md`, `USER.md`, or `state.db`; a
replacement for Git; a promise that confidence equals truth; or a claim of
native integration with every named MCP client.

## 02 — Product Truth

### Truth classes

- `[IMPLEMENTED]` present in current code and suitable as a current capability.
- `[DOCUMENTED]` stated in repository documentation but not independently
  verified; write cautiously.
- `[EXPERIMENTAL]` present behind optional, incomplete, or environment-dependent
  paths; label it.
- `[PROPOSED]` roadmap/design material only; exclude from current capability copy.
- `[UNKNOWN]` cannot be established; exclude.

### Current primitives [IMPLEMENTED]

`Repository`, `Module`, `Symbol`, `Decision`, `Problem`, `Dependency`,
`Cluster`, `ClusterSummary`, `AgentSession`, and `Principal` are defined in
`memex/graph/schema.py`. Decisions and problems carry source, scope, confidence
or validation fields; structural entities have write policies.

### Current context primitives [IMPLEMENTED]

`ContextPacket` is the bounded, serializable projection shared by the selection
layer, Hermes provider, and MCP context response. Each item carries scope,
confidence, freshness, provenance, relationships, and a selection reason.
`TaskRecord` and `OutcomeRecord` support repository-scoped evaluation and
verification; they are deliberately not a transcript store or workflow engine.

Verified relations include `CALLS`, `IMPORTS`, `CONTAINS`, `CORROBORATES`, and
`RESOLVED_BY`. Query and writer code also handles semantic `RELATES_TO`,
`MOTIVATES`, and `MENTIONS`; describe these as graph relationships, not as a
guarantee that every record has every edge.

### Current temporal behavior [IMPLEMENTED]

Live traversal filters `expired_at IS NULL`. Confidence is computed at read
time by `memex/graph/confidence.py` from `base_confidence`, validation, and
`last_reinforced_at` (falling back to `created_at`). Validated records decay at
`0.005` per day with a `0.7` floor. Unvalidated records use `ln(2)/30` through
day 30, then `ln(2)/20` with a `0.5` cap. The 0.3 staleness crossing at day
30 applies to the watcher default base of 0.6; it is not a universal deadline.
Search reranking separately applies access-count rehearsal weighting.

### Current delivery boundary [IMPLEMENTED]

MCP has 14 registered tools. Stdio and HTTP transports exist. Context-aware
projection uses `ContextPacket` and provenance metadata. Hermes provides
automatic, read-only prefetch with bounded timeout and fail-open behavior; MCP
provides explicit lookup and governed write operations. Anthropic memory-tool
and other integrations are adapters, not replacements for the core.

## 03 — Narrative Strategy

1. **Recognition:** the repository changes faster than session context.
2. **Failure:** agents repeat discovery and lose the reason behind code.
3. **Insight:** useful continuity requires typed relationships and time, not
   only more text.
4. **System:** watcher → extraction/synthesis → graph → temporal state → MCP.
5. **Trust:** source references, scope, validation, corroboration, supersession,
   and explicit invalidation keep uncertainty visible.
6. **Boundary:** the host may receive automatic read-only context or ask via
   MCP; engineering writes remain explicit and governed; memex does not make
   the edit.
7. **Proof:** show real tool names, schemas, commands, formulas, and source
   paths.

## 04 — Brand Voice

Technical, precise, calm, evidence-led, developer-native, and quietly confident.
Use concrete verbs: watches, extracts, writes, expires, ranks, scopes, records.
Explain limitations beside capability claims. A slight provocation is allowed
only when technically defensible: “Confidence is not truth.”

Avoid revolutionary, game-changing, 10x, supercharge, unlock, seamless,
AI-powered, second brain, enterprise-grade, future-proof, never forgets,
intelligent platform, fake urgency, and unsupported superlatives.

## 05 — Information Architecture

The recommended site has six pages. Merge “MCP” and “Agent Integration” so the
protocol boundary is explained once; merge “Product” and “How It Works” only at
the overview level, keeping the detailed pipeline page separate.

1. **Home** — recognise the continuity problem and orient to the system.
2. **How It Works** — trace repository event to agent context.
3. **Knowledge Model** — explain entities, relations, provenance, and time.
4. **Agent Integration** — document MCP tools and write/read boundaries.
5. **Architecture** — serve technical readers with components and deployment.
6. **Repository / Evolution** — commands, source links, version status, and
   changelog; do not imply roadmap features are shipped.

## 06 — Homepage

**Purpose:** answer “What is memex, and why would a coding agent need it?”  
**Audience:** software engineers, AI engineers, coding-agent builders, and
open-source maintainers.

**Hero**

- Eyebrow: `REPOSITORY CONTEXT OVER TIME`
- Headline: `A knowledge graph for the parts of engineering work files do not explain.`
- Subheadline: `memex watches repository changes, connects code relationships and engineering decisions, and serves scoped context through Hermes and MCP.`
- Supporting line: `It records evidence, tracks validity, and leaves the agent in control of the edit.`

**Section: The repeated failure**

Title: `Every new session pays for old discovery.`  
Copy: `The module is still there. The caller is still there. The decision is
still somewhere in a commit. Without a durable, queryable representation, an
agent has to rediscover all three.`  
Evidence: `get_symbol_context`, recent-decision and open-problem queries;
`README.md` lifecycle.

**Section: The system**

Title: `From repository event to usable context.`  
Copy: `Files and Git enter the watcher. Extractors produce symbols, calls,
dependencies, and imports. Commit synthesis can produce Decision records.
Graphiti and Neo4j hold the graph. Hermes or MCP returns the bounded slice an
agent needs.`  
Interaction: step-through pipeline; each step names its source directory.

**Section: Knowledge changes**

Title: `Stored does not mean current.`  
Copy: `Relationships can expire. Decisions can be corroborated or superseded.
Confidence is recalculated as time and validation change.`  
Interaction: toggle created / corroborated / superseded / invalidated; disclose
the exact formula from `graph/confidence.py`.

**Section: Agent boundary**

Title: `The host gets context. The graph keeps the evidence.`  
Copy: `Hermes can receive bounded read-only context before a model action.
MCP clients can explicitly recover project, symbol, decision, problem, search,
stale, briefing, impact, and change explanations. Governed MCP writes record
decisions, problems, resolutions, corroboration, supersession, and
invalidation.`

**Closing:** `Read the repository. Inspect the tools. Run the server.` Link to
GitHub, README, and the technical pages. Do not use a sales CTA.

## 07 — How It Works

**User question:** “What actually happens after I install memex?”

Hero: `A watcher turns repository motion into queryable engineering state.`

Sections:

1. **Input — files and Git.** Watchdog filesystem events, Git hooks/polling,
   and lockfile changes enter `memex/watcher/`. Artifact: routed events.
2. **Extraction — structure.** Tree-sitter produces symbols and conservative
   call edges; lockfile parsers produce dependencies/imports. Artifact:
   structured deltas.
3. **Synthesis — decisions.** Commit message and diff can pass through Gemini
   synthesis when configured. Artifact: candidate `Decision` records.
4. **Storage — graph.** Writers use Graphiti episodes plus structured Neo4j
   updates. Artifact: entities, properties, and typed relationships.
5. **Temporal state — validity.** `created_at`, `expired_at`, reinforcement,
   validation, and computed confidence determine retrieval status.
6. **Retrieval — provider and MCP.** Queries scope by repository/project,
   exclude expired edges, rerank, and format bounded results or ContextPackets.
   Hermes can prefetch read-only context; MCP exposes explicit retrieval.
7. **Write-back.** Agent/human actions call governed MCP write tools; decisions can
   corroborate or supersede, problems can resolve, and edges can invalidate.

Interaction: animated trace with “automated” versus “explicit agent/human
action” labels. Never show fake node counts or fake latency.

## 08 — Knowledge Model

**Hero:** `The graph represents engineering claims, their relationships, and
the evidence that changes their status.`

| Primitive | Definition | Why it matters | Evidence |
|---|---|---|---|
| Module | Repository path and language-level unit | scopes impact and context | `graph/schema.py`, writer |
| Symbol | function/class/constant with file, line, signature | supports pre-edit context and calls | schema, tree-sitter extractor |
| Decision | architectural choice with rationale/source/validation | preserves why, not just what | schema, synthesizer, write tool |
| Problem | severity-bearing open issue or debt | carries unresolved work across sessions | schema, problem tools |
| Dependency | lockfile-derived package/version/ecosystem | grounds import and impact analysis | lockfile extractor/writer |
| Cluster | persisted group of related modules | compresses architecture for briefing | cluster/runner; optional extra |
| Commit | source reference for change and synthesis | anchors explanation and provenance | watcher, explain tool |
| AgentSession | session identity and ownership boundary | separates agent-written context | schema/write path |
| Principal | authenticated human/service identity | governs HTTP/team access | schema, registry, HTTP |

Relations: `CALLS` (symbol coupling), `IMPORTS` (module/dependency structure),
`CONTAINS` (cluster membership), `CORROBORATES` (reinforcement), and
`RESOLVED_BY` (problem resolution). Explain `RELATES_TO`, `MOTIVATES`, and
`MENTIONS` as semantic links where surfaced by the graph.

Interaction: select a Decision and follow source commit → affected module →
superseding decision; show historical items labelled historical.

## 09 — Architecture

**Hero:** `Small boundaries, explicit state.`

| Component | Does | Communicates with |
|---|---|---|
| Watcher | routes file, Git, and lockfile events | extractors, synthesizer, writers |
| Extractor | tree-sitter symbols/calls; lockfile dependencies/imports | watcher and graph writer |
| Synthesizer | Gemini-backed commit decision extraction | watcher and decision writer |
| Graph client/writer | Graphiti/Neo4j reads and structured writes | watcher, queries, clusters |
| Confidence/decay | computes freshness/confidence and cold eligibility | reranker, stale queries, archive |
| Retrieval | structured queries, composite search, reranking | MCP tools and ContextPacket selector |
| MCP server | stdio/HTTP tool boundary | coding agents and explicit writes |
| Telemetry | local statistics/traces and optional OTel | tools/CLI/operations |
| Hermes provider | bounded, read-only automatic prefetch | Hermes model context |
| Integrations | memory-tool and other adapters | external host lifecycles |

Deployment copy: `Local mode uses Docker for Neo4j. The team compose topology
keeps Neo4j internal and exposes memex-server as the sanctioned API entry
point.` This is supported by Docker documentation; do not call it a hosted
SaaS.

## 10 — Agent Integration / MCP

**Hero:** `The host receives context; MCP keeps lookup and writes explicit.`

Explain: Hermes can invoke the memex `MemoryProvider` for automatic read-only
prefetch; an MCP client can explicitly invoke tools. memex does not replace the
host agent. Repository engineering knowledge is durable graph state. Session
state is host/session metadata. Retrieval context is a bounded projection for a
task. These are distinct.

| Tool group | Tools | Agent moment |
|---|---|---|
| Orient | `get_project_context`, `get_context_briefing` | start a session |
| Inspect | `get_symbol_context`, `get_recent_decisions`, `get_open_problems` | before editing/investigating |
| Search/state | `search_context`, `get_engineering_context`, `get_stale_context` | task-specific retrieval |
| Analyze | `explain_change`, `predict_impact` | understand a commit or blast radius |
| Write | `record_decision`, `record_problem`, `resolve_problem`, `invalidate_edge` | write back explicit engineering state |

Each tool returns text/packet projections, not an autonomous edit. Hermes
prefetch is read-only; write operations remain explicit MCP operations. Add a
real input/output table from `memex/mcp_server/server.py`; do not invent
responses. Show client configuration as “MCP-compatible configuration
examples” for Claude Code, Cursor, Codex, and Gemini CLI, not native
integrations.

## 11 — Use Cases

1. **Before changing a symbol.** Situation: an agent is new to a function.
   Interaction: `get_symbol_context(name, file)`. Result: symbol metadata,
   callers, callees, linked decisions/problems. Evidence: `tools_read.py`.
2. **Starting a project session.** Interaction: `get_project_context` or a
   bounded `get_context_briefing`. Result: architecture summaries, recent
   decisions, active problems, stale signal. Evidence: tools and formatter.
3. **Explaining a change.** Interaction: `explain_change(commit_sha)`.
   Result: diff grounded against linked Decision/Problem records via Gemini Pro.
4. **Predicting impact.** Interaction: `predict_impact(file_path)`. Result:
   ranked coupled modules using calls/imports/decision links; no LLM call.
5. **Recording a decision.** Interaction: `record_decision`; use
   `corroborates` to reinforce or `supersedes` to replace. Result: governed
   graph write and expired old outgoing edges when superseding.
6. **Finding aging knowledge.** Interaction: `get_stale_context`. Result:
   live low-confidence relationships for review; confidence is a signal, not
   truth.
7. **Automatic task context.** Hermes invokes the memex provider before a model
   action. Result: a bounded `ContextPacket` with provenance and freshness;
   timeout or provider failure returns no context rather than blocking the host.

## 12 — Engineering Differentiation

| Defensible claim | Evidence | Why it matters | Not this |
|---|---|---|---|
| Temporal validity is first-class | `expired_at`, confidence, tests | stale knowledge can be surfaced or excluded | “perfect truth decay” |
| Graph relationships are queryable | CALLS/IMPORTS/CONTAINS and symbol queries | context follows code structure | vector search alone |
| Decisions/problems are durable primitives | schema and write tools | preserves engineering rationale and unresolved work | chat transcript storage |
| Provenance-aware bounded context exists | ContextPacket, source refs, selector tests | agents receive inspectable evidence | chain-of-thought |
| Writes are explicit and governed | policies, corroboration/supersession, principals | agents cannot silently rewrite locked structure | autonomous code editing |
| Host integration is fail-open | Hermes provider contract and provider tests | context augments an agent without becoming its runtime dependency | replacing host state |

## 13 — Technical Proof

Use only these proof assets: `README.md` lifecycle and MCP tables;
`pyproject.toml` package metadata; `memex/graph/schema.py`; confidence tests;
MCP tool registry; watcher handlers; tree-sitter/lockfile extractors; Docker
compose files; VS Code README; `tests/test_context_mcp.py` and
`tests/test_mcp_queries.py`; and `BENCHMARK.md` for the bounded Goal 10 result.
Good code excerpts include the tool names, the confidence formula,
`WHERE r.expired_at IS NULL`, and documented CLI commands.

## 14 — Interactive Storytelling

- **Lifecycle trace:** click a stage; reveal artifact and source directory.
- **Knowledge graph:** hover `Decision`, `Module`, `Symbol`, or `Problem`; show
  relation meaning, not invented records.
- **Decision lineage:** toggle current/history; reveal corroboration and
  supersession semantics.
- **Confidence timeline:** change validation and reinforcement state; show the
  implemented formula and threshold caveat.
- **MCP console:** choose a real tool; show verified input shape, agent moment,
  and output contract. Label examples illustrative.
- **Context packet:** expand source reference, freshness, selection reason, and
  dropped-item behavior from current packet models.
- **Hermes prefetch:** show the automatic read-only path, bounded timeout, and
  fail-open behavior; do not imply transcript ingestion.

## 15 — Reusable Copy

- **Product descriptor:** `Trusted engineering context for agentic software engineering.`
- **One sentence:** `memex maintains a temporal knowledge graph of repository facts, decisions, problems, and code relationships, projected as bounded context for coding agents.`
- **Short paragraph:** `memex watches repository and Git activity, extracts structure, stores engineering knowledge in Neo4j via Graphiti, and returns scoped context through Hermes automatic prefetch or MCP.`
- **Technical paragraph:** `Its watcher routes file and commit events through tree-sitter, lockfile parsing, and optional Gemini synthesis; retrieval filters expired relationships, computes confidence at read time, and projects bounded context with provenance.`
- **Homepage hero:** `A knowledge graph for the parts of engineering work files do not explain.`
- **GitHub description:** `Trusted engineering context for agentic software engineering.`
- **Product introduction:** `memex turns repository change into queryable engineering state.`
- **Architecture introduction:** `The system separates observation, extraction, synthesis, graph writes, temporal state, and agent retrieval.`
- **MCP introduction:** `MCP is the explicit interoperability boundary through which agents recover context and write back decisions or problems; Hermes provides automatic read-only prefetch.`
- **Footer:** `Repository facts, decisions, problems, and relationships—served with scope and history.`

## 16 — SEO / Metadata

- Title: `memex — trusted engineering context for agentic software engineering`
- Description: `memex watches repository changes, builds a temporal knowledge graph of engineering context, and serves bounded context through Hermes and MCP.`
- Social preview: `See how repository events become provenance-aware context for coding agents.`
- Search terms to use naturally: `MCP coding agent context`, `temporal knowledge graph repository`, `engineering decisions graph`, `repository context memory`, `Neo4j Graphiti MCP`.

## 17 — Content Anti-Patterns

Never publish fake benchmark numbers, customer stories, testimonials, pricing,
download counts, latency, accuracy, token savings, or “never forgets” claims.
Do not call `get_context_briefing` a universal fixed 1500-token guarantee; the
current implementation accepts a configurable budget. Do not say confidence is
truth, that all retrieval is automatic, or that named clients have native
integrations. Do not present roadmap documents, old version walkthroughs, or
optional cluster dependencies as unconditional core behavior.

## 18 — Evidence & Source Map

| Content area | Primary evidence |
|---|---|
| Product definition | `README.md`, `pyproject.toml` |
| Node model | `memex/graph/schema.py` |
| Temporal/confidence | `memex/graph/confidence.py`, `tests/test_confidence.py` |
| Graph writes/relations | `memex/graph/writer.py`, `memex/mcp_server/tools_write.py` |
| Ingestion | `memex/watcher/handlers.py`, `memex/extractor/`, `memex/synthesizer/commit.py` |
| Retrieval | `memex/mcp_server/queries.py`, `reranker.py`, `tools_read.py` |
| Context packet | `memex/context/`, `context_tools.py`, `tests/test_context_mcp.py` |
| Hermes prefetch | `memex/integrations/hermes_provider.py`, `tests/test_hermes_provider.py` |
| Objective benchmark | `BENCHMARK.md`, `goal10_objective_matrix.py`, `tests/test_goal10_objective.py` |
| Task/outcome evaluation | `memex/context/task_outcome.py`, `memex/evaluation/`, `tests/test_task_outcome.py` |
| MCP interface | `memex/mcp_server/server.py` |
| Security/deployment | `SECURITY.md`, `memex/mcp_server/http.py`, `docker/` |
| Integrations | `memex/memory_tool/`, `memex/integrations/`, `memex-vscode/` |

## 19 — Known Gaps / Unverified Claims

- Current version is `0.9.0`; release status is not product proof.
- Current tests include backend-dependent and optional-dependency paths; do not
  turn test counts into product proof.
- Cluster behavior depends on optional clustering dependencies; label it
  optional where shown.
- Gemini synthesis and explanation require configured credentials/backend.
- The exact production prevalence or quality of graph data is unknown without a
  live repository deployment.
- Goal 10 established provider-scoped non-regression evidence: 8/8 valid paired
  runs, zero treatment failures, and zero treatment regressions. It did not
  establish causal improvement because the baseline also completed all cases.
- No repository evidence supports customer outcomes, benchmarks, market size,
  or universal agent productivity claims.
- Roadmap/design documents describe future architecture; they are not current
  website capability evidence.

## 20 — Editorial Acceptance Checklist

Before publishing any page, confirm: every substantive claim has a source;
status is marked when not `[IMPLEMENTED]`; examples use real tool/command names;
illustrative data is labelled; current version is stated; the agent boundary is
clear; confidence is not called truth; and no page needs a designer to invent
what a section means.
