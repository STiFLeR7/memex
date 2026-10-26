import { DocShell, JsonLd, pageLd, pageMeta } from '../shell';

/* Every figure traces to docs/v1/28_PHASE6_RESULTS.md (S05), 21_PHASE5_EXECUTION_PLAN.md
   (arms) or 24_PHASE5_RELEASE_READINESS.md (mechanism suite). npm run check pins them. */
const PATH = '/evaluation';
const TITLE = 'memex evaluation: preregistered S05 results';
const DESC = "memex's preregistered S05 evaluation: 480 trials on Claude Code and Codex. Five gates passed; efficacy, precision and latency did not. Every gate and caveat.";

export const metadata = pageMeta(PATH, `${TITLE} · memex`, DESC);

export default function Evaluation() {
  return (
    <DocShell path={PATH} title="Evaluation" lede={<>memex is evaluated in the open: definitions and thresholds are frozen before the first trial, trials
      run on the real Claude Code and Codex clients, and results are published whether they pass or not. The S05 confirmatory run did not pass: five
      gates passed, and efficacy, precision and latency did not.</>}>
      <JsonLd data={pageLd(PATH, TITLE, DESC)} />

      <h2 id="summary">Did memex pass its evaluation?</h2>
      <p>No. S05 ran 480 of 480 trials on 6 October 2026: 48 held-out histories, five comparison arms and both hosts, analysed once against values frozen
        in commit <code>23f227a</code> before any trial. Recall, false interruption, tail deadline, resources and stable non-inferiority passed. Each of
        the three failures holds pooled and on each host separately. No speed or accuracy claim is made for memex on the strength of this run.</p>

      <h2 id="gates">Which gates passed and which failed?</h2>
      <table>
        <thead><tr><th>Gate</th><th>Pooled result</th><th>Needs</th></tr></thead>
        <tbody>
          <tr><td>Efficacy</td><td><b>Failed</b></td><td>E reduces stale failures vs the strongest baseline</td></tr>
          <tr><td>Precision</td><td><b>Failed</b>: 56/64 = 0.875</td><td>≥ 0.95</td></tr>
          <tr><td>Recall</td><td>Passed: 56/56</td><td>≥ 0.9</td></tr>
          <tr><td>False interruption</td><td>Passed: 0/40</td><td>≤ 0.05</td></tr>
          <tr><td>Latency (warm check p95)</td><td><b>Failed</b>: 275 ms</td><td>&lt; 200 ms</td></tr>
          <tr><td>Tail deadline</td><td>Passed: max 377 ms</td><td>every check ≤ 10 000 ms</td></tr>
          <tr><td>Resources (E vs C)</td><td>Passed: 0.98× wall time, 0.97× output tokens</td><td>≤ 1.25×</td></tr>
          <tr><td>Stable non-inferiority</td><td>Passed vs A and C</td><td>CI95 lower bound &gt; −0.10</td></tr>
        </tbody>
      </table>

      <h2 id="arms">What were the five arms?</h2>
      <p>Every arm got the same task prompt, repository, history, tools, turn limit and model. The prompt never mentions memex.</p>
      <table>
        <thead><tr><th>Arm</th><th>At session start</th><th>Before each declared edit</th></tr></thead>
        <tbody>
          <tr><td>A</td><td>Nothing beyond the repository and its docs</td><td>Nothing</td></tr>
          <tr><td>B</td><td>The v0.9-style packet of claims</td><td>Nothing</td></tr>
          <tr><td>C</td><td>The same packet</td><td>A fresh retrieval; if it differs from what the session was last given, the edit is denied with the fresh packet</td></tr>
          <tr><td>D</td><td>The same claims as hash-bound notes</td><td>If a cited file’s hash changed, the edit is denied with a re-read warning</td></tr>
          <tr><td>E (memex)</td><td>The same packet, with a confirmed delivery receipt</td><td>The full live-context check: scoped revalidation and a session-relative correction</td></tr>
        </tbody>
      </table>

      <h2 id="why">Why did efficacy, precision and latency fail?</h2>
      <h3>Efficacy</h3>
      <p>As predicted before the trials. The strongest baseline, C, had no stale-context failures (0/56), so there was nothing left to reduce; B, D and E
        were also at 0/56. Only A, with no memex context, failed: 44/56 (79%). The arms with correct context all did equally well on these histories;
        the arm with memex corrections did not do worse.</p>
      <h3>Precision</h3>
      <p>Also as predicted. All 8 of E’s incorrect corrections are on the <code>cosmetic</code> histories, where a comment-only change flags a claim bound
        to the file’s hash. B and C show the same 8.</p>
      <h3>Latency</h3>
      <p>The gate the S05 changes targeted, and its result was not predicted. The warm end-to-end check p95 fell from about 6 s in S04 to 275 ms, but missed
        the 200 ms threshold. Inside the hook service the check p95 was 110 ms; the rest is the client’s start and the round trip, measured with three
        trials running at once.</p>

      <h2 id="mechanism">What does the mechanism suite show?</h2>
      <p>Separately from S05, the deterministic mechanism suite (fixtures F01–F16) passed 16 of 16: stale evidence, supersession, worktree isolation and
        authority, with no isolation or authority violation. It runs on Windows and in Linux Docker.</p>

      <h2 id="conduct">How was the run conducted?</h2>
      <ul className="plain">
        <li>480 of 480 trials completed. Two Codex trials ended <code>unavailable</code> and were retried automatically, as the protocol allows; both first attempts are kept.</li>
        <li>At 36/480 the client’s low-memory reaper stopped the batch; with the maintainer’s approval only the missing trials were resumed, and the restart is recorded.</li>
        <li>The analysis ran once, after the batch ended. Under the stopping rules S05 is not extended and no gate is changed after the fact.</li>
        <li>Cost: Claude Code, $31.42 across its 240 trials. Codex reports no cost, so its 240 trials are unknown, not zero.</li>
      </ul>

      <div className="next">
        <a className="btn lg" href="https://github.com/STiFLeR7/memex/blob/master/docs/v1/28_PHASE6_RESULTS.md">Full S05 results on GitHub <span className="a" aria-hidden="true">↗</span></a>
        <a className="btn lg ghost" href="https://github.com/STiFLeR7/memex/blob/master/docs/v1/27_PHASE6_PROTOCOL.md">The frozen protocol <span className="a" aria-hidden="true">↗</span></a>
      </div>
    </DocShell>
  );
}
