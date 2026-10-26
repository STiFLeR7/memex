import { DocShell, JsonLd, pageLd, pageMeta } from '../shell';

/* Source: CHANGELOG.md in the memex repository (v1 releases only). */
const PATH = '/changelog';
const TITLE = 'memex changelog: v1 releases';
const DESC = 'What changed in each memex v1 release: v1.0.2 (2026-10-08), v1.0.1 and v1.0.0 (2026-10-06): Claude Code and Codex hooks, held edits, the hook service.';

export const metadata = pageMeta(PATH, `${TITLE} · memex`, DESC);

export default function Changelog() {
  return (
    <DocShell path={PATH} title="Changelog" lede={<>Every memex v1 release, newest first. The current release is v1.0.2, published 2026-10-08 on PyPI
      (<code>memex-mcp</code>), npm (<code>stifler-memex-mcp</code>) and the MCP Registry.</>}>
      <JsonLd data={pageLd(PATH, TITLE, DESC)} />

      <h2 id="v1-0-2">v1.0.2 · 2026-10-08</h2>
      <h3>Fixed</h3>
      <p>The MCP server answers the client’s handshake at once, even when Neo4j is not running. It used to wait about 50 seconds on Neo4j retries and then
        exit, so clients showed only a connection timeout. Tool calls now say that Neo4j is unreachable and how to fix it, and work as soon as Neo4j
        answers, without a restart. <code>MEMEX_NEO4J_CHECK_SECONDS</code> sets the check’s time limit (default 5).</p>
      <h3>Documentation</h3>
      <p>README: v1 live context, quick start and architecture.</p>

      <h2 id="v1-0-1">v1.0.1 · 2026-10-06</h2>
      <h3>Fixed</h3>
      <p><code>npx stifler-memex-mcp</code> now runs the matching <code>memex-mcp</code> version from PyPI. Previously uv could reuse a cached older{' '}
        <code>memex-mcp</code>, so the v1 commands were missing for anyone who had run an earlier release.</p>

      <h2 id="v1-0-0">v1.0.0 · 2026-10-06</h2>
      <p>memex v1: live engineering context for coding agents, kept current as the code changes.</p>
      <h3>Added</h3>
      <ul className="plain">
        <li><b>Claude Code and Codex adapters.</b> <code>memex v1 install claude</code> and <code>memex v1 install codex</code> add hooks to the checkout’s own project configuration and leave global client settings alone. Each session starts with a bounded working set, and delivery is confirmed from the client’s own session record.</li>
        <li><b>Action checks and corrections.</b> Before an edit, memex checks whether the context it rests on still holds. If not, the edit is held, the agent gets a scoped correction, and it revises (Claude Code <code>Edit</code>/<code>Write</code>/<code>MultiEdit</code>/<code>NotebookEdit</code>; Codex <code>apply_patch</code> in app-server sessions).</li>
        <li><b>Change notice on resume.</b> When a session resumes after the code changed, memex names the files whose claims changed so the agent re-reads them.</li>
        <li><b>Hook service.</b> A per-user local service keeps hooks warm. It listens on 127.0.0.1 only, with a token check, stops after 15 idle minutes, and falls back to a one-shot run if it is unavailable. <code>MEMEX_HOOKD=0</code> turns it off.</li>
        <li><b>Worktree-scoped views.</b> Durable structural views per linked worktree, so parallel agents in separate worktrees each see their own code.</li>
        <li><b>Modes and rollback.</b> <code>memex v1 mode off|shadow|live</code>. Shadow records what would have been corrected without changing anything. <code>memex v1 rollback</code> stops memex at once.</li>
        <li><b>Diagnostics and migration.</b> <code>memex v1 doctor</code> reports what is set up and what to do next. <code>memex v1 migrate</code> imports v0.9 knowledge additively, marked <code>legacy_unverified</code>.</li>
        <li><b>Opt-in guarded writes</b> for cooperating writers, with fencing enforced at commit.</li>
        <li><b>Field pilot kit.</b> <code>memex v1 pilot</code> runs a self-contained, privacy-preserving pilot that produces count-only reports.</li>
      </ul>
      <h3>Changed</h3>
      <ul className="plain">
        <li>Source capture reads HEAD from ref files, lists sources once per check, and caches parses by content hash.</li>
        <li>Fixed: delivery is confirmed exactly once under concurrent hooks, interrupted guarded writes are resolved before the next guarded operation, and structural context refreshes after body-only edits.</li>
      </ul>

      <div className="next">
        <a className="btn lg" href="https://github.com/STiFLeR7/memex/blob/master/CHANGELOG.md">Full changelog on GitHub <span className="a" aria-hidden="true">↗</span></a>
        <a className="btn lg ghost" href="/setup">Set up memex <span className="a" aria-hidden="true">→</span></a>
      </div>
    </DocShell>
  );
}
