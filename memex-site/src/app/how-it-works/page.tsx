import { DocShell, JsonLd, STAGES, pageLd, pageMeta } from '../shell';

/* Sources: docs/v1/01_VISION_AND_SCOPE.md, 25_ONBOARDING.md, CHANGELOG.md
   [1.0.0], and the stage list shared with the home page. */
const PATH = '/how-it-works';
const TITLE = 'How memex keeps a coding agent’s context current';
const DESC = 'How memex works: it binds each claim to its source bytes, records what each Claude Code or Codex session was told, and holds an edit whose evidence changed.';

export const metadata = pageMeta(PATH, `${TITLE} · memex`, DESC);

export default function HowItWorks() {
  return (
    <DocShell path={PATH} title="How memex works" lede={<>memex keeps an agent’s engineering context current as the code changes. It binds each claim
      about your repository to the exact source bytes behind it, remembers which session was told what, and when that evidence moves, holds the affected
      edit and tells the agent what changed and what to re-read.</>}>
      <JsonLd data={pageLd(PATH, TITLE, DESC)} />

      <figure>
        {/* eslint-disable-next-line @next/next/no-img-element -- static export, pre-sized WebP */}
        <img src="/memex-film-live-context.webp" width={1280} height={720} loading="lazy" decoding="async"
          alt="Still from the memex v1 film: Claude Code in worktree A plans an edit, a teammate changes directory.py, and the memex live-context panel marks claim c-contract@r1 unsupported, holds session A's affected edit and queues a notice on resume for session B." />
        <figcaption>Still from the 60-second memex v1 film: the affected edit is held and corrected.</figcaption>
      </figure>

      <h2 id="problem">What problem does memex solve?</h2>
      <p>A coding agent reads the code, forms a plan, and acts on it. If a teammate or another agent changes the code in between, the agent keeps acting
        on what it was told. Retrieval tells an agent what is true now. memex also remembers what each session was already told, so it can say exactly
        what stopped being true, and when, before the next affected edit lands.</p>
      <p>The statement memex supports is “this evidence was checked in view V at generation G”. It is not “the entire repository is understood” or
        “the model cannot make a mistake”: matching source hashes establish source identity, not semantic truth.</p>

      <h2 id="model">Delivered, watched, corrected</h2>
      <ol className="plain">
        <li><b>Delivered.</b> Each session starts with a bounded working set: claims about your repository, each bound to the exact source bytes behind it.
          Delivery counts only when the client’s own transcript shows the packet arrived whole.</li>
        <li><b>Watched.</b> Every change produces a new repository view. A claim whose evidence moved is marked <code>needs_revalidation</code>,{' '}
          <code>unsupported</code> or replaced, deterministically, with no model in the loop.</li>
        <li><b>Corrected.</b> An edit that depends on stale context is held, the correction is delivered, and the agent reconsiders. A resumed session is
          told what changed and which files to re-read.</li>
      </ol>

      <h2 id="stages">What are the six stages of a memex check?</h2>
      <p>Every check runs the same pipeline, from reading the source to holding an edit. Each stage names the module that implements it.</p>
      <ol className="stages">
        {STAGES.map(s => <li key={s.n}><h3><span className="n">{s.n}</span> {s.t}</h3><p>{s.c}</p><div className="path">{s.p}</div><div className="art">{s.a}</div></li>)}
      </ol>

      <h2 id="concurrency">What happens when two agents share a repository?</h2>
      <p>Claude Code receives an API constraint and begins a change. Codex changes the supporting API in the same checkout. Claude’s next affected edit is
        held for reconsideration, with the old claim identified and current evidence supplied, while an unrelated action proceeds without interruption.</p>
      <p>In separate worktrees, Codex’s unmerged change does not become Claude’s local truth: memex keeps a structural view per linked worktree, and
        integrating the change into Claude’s view is what triggers revalidation. One session’s acknowledgement never suppresses another session’s correction.</p>

      <h2 id="hosts">What does each host support?</h2>
      <table>
        <thead><tr><th>Capability</th><th>Claude Code</th><th>Codex</th></tr></thead>
        <tbody>
          <tr><td>Working set at session start, confirmed from the client’s record</td><td>Yes</td><td>Yes, in app-server (IDE and desktop) sessions</td></tr>
          <tr><td>Held edit and correction</td><td><code>Edit</code>, <code>Write</code>, <code>MultiEdit</code>, <code>NotebookEdit</code></td><td><code>apply_patch</code>; not under <code>codex exec</code>, which runs no hooks</td></tr>
          <tr><td>Change notice when a session resumes</td><td>Yes</td><td>Yes</td></tr>
          <tr><td>Concurrent sessions, worktrees and shared checkouts</td><td>Yes</td><td>Yes</td></tr>
          <tr><td>Guarded writes (opt-in)</td><td>Through <code>guard_read</code> / <code>guard_write</code></td><td>Same</td></tr>
          <tr><td>Shell edits</td><td>Reported, never certified</td><td>Same</td></tr>
          <tr><td>Hook failure</td><td>Fails open: the edit proceeds without a certificate</td><td>Same</td></tr>
        </tbody>
      </table>

      <h2 id="not">What memex is not</h2>
      <p>memex is not a datastore replacement, a semantic oracle, an autonomous coding planner or a personal-memory service. It does not ingest
        transcripts, merge patches, or call a model on every keystroke. It never grants permission: it can only hold an edit and ask the agent to
        reconsider, and if it cannot check, the edit proceeds and the agent is told freshness is unknown. Shell writes, editors and other tools bypass the
        hooks; their changes are seen at the next check, not prevented.</p>

      <div className="next">
        <a className="btn lg" href="/setup">Set up memex <span className="a" aria-hidden="true">→</span></a>
        <a className="btn lg ghost" href="/evaluation">See the evaluation <span className="a" aria-hidden="true">→</span></a>
      </div>
    </DocShell>
  );
}
