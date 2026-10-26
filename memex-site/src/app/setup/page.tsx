import { DocShell, JsonLd, pageLd, pageMeta } from '../shell';

/* Source: docs/v1/25_ONBOARDING.md, README.md (Install), CHANGELOG.md. */
const PATH = '/setup';
const TITLE = 'Set up memex for Claude Code and Codex';
const DESC = 'Install memex (memex-mcp), connect the Neo4j 5 you already run, add hooks for Claude Code or Codex to one repository, try shadow mode, and roll back at once.';

export const metadata = pageMeta(PATH, `${TITLE} · memex`, DESC);

const Code = ({ children }: { children: string }) => (
  <div className="card"><pre>{children}</pre></div>
);

export default function Setup() {
  return (
    <DocShell path={PATH} title="Set up memex" lede={<>memex installs per repository in a few commands. You need Python 3.11+, Git and a Neo4j 5
      server you already run; hooks go only into that repository’s own project settings, and <code>memex v1 rollback</code> turns everything off at once.</>}>
      <JsonLd data={pageLd(PATH, TITLE, DESC)} />

      <h2 id="requirements">What do you need before installing memex?</h2>
      <ul className="plain">
        <li>Python 3.11 or newer.</li>
        <li>Git.</li>
        <li>A reachable Neo4j 5 server you already run. Docker is not required, and memex does not change your datastore.</li>
        <li>The host client you use (Claude Code or Codex), logged in as usual. memex never asks for or stores host credentials.</li>
      </ul>

      <h2 id="steps">How do you install memex in a repository?</h2>
      <ol className="plain">
        <li><b>Install the package once.</b> From PyPI with <code>pip install memex-mcp</code>, or run it through npm with <code>npx stifler-memex-mcp</code>.</li>
        <li><b>Point memex at Neo4j.</b> Set <code>NEO4J_URI</code>, <code>NEO4J_USER</code> and <code>NEO4J_PASSWORD</code> in the environment, or in a <code>.env</code> file at the repository root.</li>
        <li><b>Add the hooks for your client</b> in the repository you want memex to serve: <code>memex v1 install claude</code> or <code>memex v1 install codex</code>.</li>
        <li><b>Check the setup</b> with <code>memex v1 doctor</code>. It says what is set up, what is missing and what to do next.</li>
        <li><b>Optionally watch first.</b> <code>memex v1 mode shadow</code> records what memex would have corrected, without changing anything; the setup guide suggests it for a first week.</li>
        <li><b>Go live</b> with <code>memex v1 mode live</code>.</li>
      </ol>
      <Code>{`pip install memex-mcp

# in your repository
memex v1 install claude      # or: codex
memex v1 doctor
memex v1 mode shadow         # optional first week
memex v1 mode live`}</Code>

      <h2 id="claude-code">How do you set up memex for Claude Code?</h2>
      <p><code>memex v1 install claude</code> registers you and adds memex’s hooks to the repository’s <code>.claude/settings.json</code>, keeping any
        other hooks. Your global Claude Code settings are not edited. Start Claude Code in the checkout as usual. Transcripts must stay under the client’s
        config directory (<code>~/.claude</code>, or <code>CLAUDE_CONFIG_DIR</code>), because memex confirms delivery from them.</p>
      <p>Held edits cover <code>Edit</code>, <code>Write</code>, <code>MultiEdit</code> and <code>NotebookEdit</code>. The memex MCP server, for explicit
        lookups, is also available as a Claude Code marketplace plugin: <code>/plugin marketplace add STiFLeR7/claude-plugins</code> then{' '}
        <code>/plugin install memex-mcp@stifler-marketplace</code>.</p>

      <h2 id="codex">How do you set up memex for Codex?</h2>
      <Code>{`memex v1 install codex --neo4j-uri bolt://your-neo4j:7687   # adds hooks to .codex/hooks.json`}</Code>
      <p>Open Codex in the checkout (the IDE extension, desktop app or interactive client) and review memex’s hooks with <code>/hooks</code>.
        Untrusted hooks do not run, and memex never grants trust itself. Codex filters a hook’s environment, so the graph URI is stored in the
        registration; credentials are not stored, so an authenticated Neo4j must be reachable with the environment the launcher sets.</p>
      <p>Held edits cover <code>apply_patch</code>, and session-start delivery works in app-server (IDE and desktop) sessions. <code>codex exec</code> runs
        no hooks, so it gets no context and no checks.</p>

      <h2 id="doctor">What does memex v1 doctor report?</h2>
      <p>The repository and worktree identity; the mode and guarded state; observed versus indexed generations; pending corrections; sessions that must
        resynchronize; the last failed insertion; exhausted reconsideration; interrupted guarded writes; and each host’s registration, hooks and adapter
        version. Graph reachability is reported separately, never as a freshness result, and messages say what to do, for example{' '}
        <i>“index is 2 generations behind; current actions are checked as unknown until it catches up.”</i></p>

      <h2 id="migrate">Can memex import knowledge from v0.9?</h2>
      <p>Yes, additively. <code>memex v1 migrate --dry-run</code> shows what would be imported and <code>memex v1 migrate</code> imports v0.9 decisions and
        problems as <code>legacy_unverified</code>, keeping approved authority, timestamps and confidence. That knowledge is available for historical lookup
        but never presented as verified current context, because its source bytes cannot be reconstructed. Legacy data is never modified.</p>

      <h2 id="guarded">What are guarded writes?</h2>
      <p>An opt-in mode, <code>memex v1 guarded on</code>, in which both adapters deny native edits to supported files and direct the agent to the{' '}
        <code>memex_guard</code> MCP server, which you add to the client’s MCP configuration yourself. The guard protects only writes that go through it:
        editors, shell writes, other tools and other machines bypass it, and their changes are detected at the next guarded write, not prevented.</p>

      <h2 id="hook-service">How fast is a check, and what is the hook service?</h2>
      <p>Installed hooks hand each event to a local memex process that stays up between events, so a warm check skips interpreter start and graph connection.
        It listens on 127.0.0.1 only, answers only requests carrying the token in <code>~/.memex/hookd/</code>, and exits after 15 idle minutes. Set{' '}
        <code>MEMEX_HOOKD=0</code> to run every event one-shot. In the S05 evaluation the warm end-to-end check p95 was 275 ms, above the 200 ms target
        (<a href="/evaluation">results</a>).</p>

      <h2 id="rollback">How do you turn memex off?</h2>
      <Code>{`memex v1 rollback      # mode off, guarded writes off, cooperating leases expired
memex v1 uninstall claude; memex v1 uninstall codex   # optional: remove project hooks too`}</Code>
      <p>Rollback keeps the journal, the repository mapping, evidence, migration records and the trace. Running <code>memex v1 mode live</code> again forces
        every bound session to resynchronize before its next affected action.</p>

      <h2 id="platforms">Which platforms are validated?</h2>
      <table>
        <thead><tr><th>Platform</th><th>Status</th></tr></thead>
        <tbody>
          <tr><td>Windows 11</td><td>Validated: all test suites and native Claude Code and Codex runs.</td></tr>
          <tr><td>Linux</td><td>Test suites validated in Docker (Debian). Native host-client integration (hooks fired by a real client on Linux) is not yet validated.</td></tr>
          <tr><td>macOS</td><td>Not validated.</td></tr>
        </tbody>
      </table>

      <h2 id="limits">What are the known limitations?</h2>
      <ul className="plain">
        <li><b>Fail-open by default.</b> A hook timeout or error lets the edit proceed on both hosts.</li>
        <li><b>Guarded writes cover cooperating writers only.</b></li>
        <li><b>Interruption recovery is not power-loss protection.</b> It is proven against a process stopping, not against power loss.</li>
        <li><b>Host insertion formats are undocumented.</b> Delivery confirmation depends on each client’s transcript format; if it changes, memex redelivers rather than wrongly confirming.</li>
      </ul>

      <div className="next">
        <a className="btn lg" href="/how-it-works">How memex works <span className="a" aria-hidden="true">→</span></a>
        <a className="btn lg ghost" href="https://github.com/STiFLeR7/memex/blob/master/docs/v1/25_ONBOARDING.md">Full onboarding doc on GitHub <span className="a" aria-hidden="true">↗</span></a>
      </div>
    </DocShell>
  );
}
