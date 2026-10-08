import type { CSSProperties } from 'react';

/* Static markup: public/site.js drives the theme toggle, canvases, reveals and demos. */
export default function Page() {
  return (
    <>
<a className="skip" href="#main">Skip to content</a>

<svg width="0" height="0" style={{ 'position': 'absolute' } as CSSProperties} aria-hidden="true">
  <defs>
    <linearGradient id="mt" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#7FDDE0"/><stop offset="0.7" stopColor="#00B3B5"/><stop offset="1" stopColor="#00A5A8"/></linearGradient>
    <linearGradient id="ml" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#E6F8C8"/><stop offset="0.75" stopColor="#8EDC5A"/><stop offset="1" stopColor="#7FD24C"/></linearGradient>
    <linearGradient id="mo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#FFFFFF"/><stop offset="1" stopColor="#D8F5EE"/></linearGradient>
    <clipPath id="mc"><rect x="196" y="76" width="260" height="260" rx="78"/></clipPath>
    <symbol id="mark" viewBox="0 0 512 512">
      <rect x="56" y="176" width="260" height="260" rx="78" fill="url(#mt)"/>
      <rect x="196" y="76" width="260" height="260" rx="78" fill="url(#ml)"/>
      <rect x="56" y="176" width="260" height="260" rx="78" fill="url(#mo)" clipPath="url(#mc)"/>
      <rect x="56" y="176" width="260" height="260" rx="78" fill="none" stroke="#fff" strokeWidth="6" strokeOpacity="0.9" clipPath="url(#mc)"/>
    </symbol>
  </defs>
</svg>

<nav className="top" aria-label="Primary">
  <div className="wrap">
    <a className="brand" href="#top"><svg width="34" height="34" aria-hidden="true"><use href="#mark"/></svg>memex</a>
    <span className="ver"><i aria-hidden="true"></i>v1.0.0</span>
    <ul>
      <li><a href="#how">How it works</a></li>
      <li><a href="#plane">Pipeline</a></li>
      <li><a href="#hosts">Hosts</a></li>
      <li><a href="#evidence">Evidence</a></li>
      <li><a href="#install">Install</a></li>
    </ul>
    <button className="theme" id="theme" type="button" aria-label="Switch to dark theme" aria-pressed="false">
      <svg className="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/></svg>
      <svg className="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true"><path d="M20.2 14.6A8.2 8.2 0 0 1 9.4 3.8a8.2 8.2 0 1 0 10.8 10.8Z"/></svg>
    </button>
    <a className="btn" href="https://github.com/STiFLeR7/memex">GitHub <span className="a" aria-hidden="true">↗</span></a>
  </div>
  <div className="progress" aria-hidden="true"></div>
</nav>

<main id="main" tabIndex={-1}>

<header className="hero" id="top">
  <canvas id="lattice" aria-hidden="true"></canvas>
  <div className="glass g-teal" data-par="-0.06" data-rot="-8" aria-hidden="true"></div>
  <div className="glass g-lime" data-par="0.05" data-rot="10" aria-hidden="true"></div>
  <div className="wrap">
    <div>
      <h1 className="stmt" data-words>Your agent read the code. <em>Then the code changed.</em></h1>
      <p className="lede rv d2">memex keeps your agent’s engineering context current as the code changes. It knows what each session
        was told, notices when the evidence behind it moves, and corrects the agent before the affected edit lands.</p>
      <div className="actions rv d3">
        <a className="btn lg" href="#install">Get started <span className="a" aria-hidden="true">→</span></a>
        <a className="btn lg ghost" href="#film">Watch the film <span className="a" aria-hidden="true">▶</span></a>
      </div>
      <div className="quick rv d4">
        <button className="pill" type="button" data-copy="pip install memex-mcp" aria-label="Copy: pip install memex-mcp"><span className="tip">Copied</span><span className="lbl">PyPI</span><span className="cmd">pip install memex-mcp</span><span className="a" aria-hidden="true">⧉</span></button>
        <button className="pill" type="button" data-copy="npx stifler-memex-mcp" aria-label="Copy: npx stifler-memex-mcp"><span className="tip">Copied</span><span className="lbl">npx</span><span className="cmd">npx stifler-memex-mcp</span><span className="a" aria-hidden="true">⧉</span></button>
      </div>
    </div>
    <div className="stack demo rv d2" id="demo" aria-label="Example: a dependency changes and memex corrects the agent">
      <div className="card c1">
        <div className="bar"><span className="dot"></span><span className="dot"></span><span className="dot"></span><span style={{ 'marginLeft': '8px' } as CSSProperties}>directory.py · changed by another contributor</span></div>
<pre dangerouslySetInnerHTML={{ __html: "<span class=\"ln\"><span class=\"k\">def</span> <span class=\"f\">find_name</span>(user_id):</span><span class=\"ln old\">    <span class=\"o\">return USERS.get(user_id)<i class=\"x\"></i></span></span><span class=\"ln new\">    <span class=\"k\">raise</span> LookupError(user_id)</span>" }} />
      </div>
      <div className="card c2">
        <div className="bar"><span className="dot"></span><span className="dot"></span><span className="dot"></span><span style={{ 'marginLeft': '8px' } as CSSProperties}>Claude Code · session resumed</span><span className="chip held" id="heldChip" style={{ 'marginLeft': 'auto' } as CSSProperties}>memex</span></div>
<pre id="typed" dangerouslySetInnerHTML={{ __html: "<span class=\"s\">memex: since this session was last given context, its evidence changed.</span>\n<span class=\"hl\">- changed: c-contract@r1 was supported, now unsupported</span>\nFiles changed since you received them: directory.py.\nRe-read them before continuing." }} />
      </div>
    </div>
  </div>
  <div className="scroll-cue" aria-hidden="true"><i></i>Scroll</div>
</header>

<section className="block film" id="film">
  <div className="wrap">
    <h2 className="stmt" data-words>One change, two agents, <em>sixty seconds.</em></h2>
    <p className="sub rv d1">A teammate pushes. memex notices the evidence moved, holds the edit that depended on it, and tells each session what to re-read.</p>
    <div className="frame rv d2" id="frame">
      <video id="filmV" preload="none" playsInline poster="/memex-v1-video.jpg" aria-label="memex v1 in 60 seconds">
        <source src="/memex-v1.mp4" type="video/mp4" />
      </video>
      <button className="play" id="play" type="button" aria-label="Play the 60-second film"><span className="lbl"><span className="tri" aria-hidden="true"></span>Play the film · 1:00</span></button>
    </div>
  </div>
</section>

<section className="block" id="how">
  <div className="wrap">
    <h2 className="stmt" data-words>Context with a memory of <em>what it promised.</em></h2>
    <p className="sub rv d1">Retrieval tells an agent what is true now. memex also remembers what each session was already told,
      so it can say exactly what stopped being true, and when.</p>
    <div className="model">
      <figure className="viz rv d1" aria-hidden="true">
        <svg viewBox="0 0 640 560">
          <defs><clipPath id="vclip"><rect id="vclipr" width="260" height="260" rx="78"/></clipPath></defs>
          <rect id="vtold" width="260" height="260" rx="78" fill="url(#mt)"/>
          <rect id="vnow" width="260" height="260" rx="78" fill="url(#ml)"/>
          <rect id="vhold" width="260" height="260" rx="78" fill="url(#mo)" clipPath="url(#vclip)"/>
          <text id="vtl">told</text><text id="vnl" textAnchor="end">now</text>
        </svg>
        <figcaption className="vstate"><span className="vtag" id="vtag">c-contract@r1 · supported</span></figcaption>
      </figure>
      <ol className="steps rv d2" id="steps">
        <li><button type="button" aria-pressed="true"><span className="k"><i style={{ '--c': 'var(--teal)' } as CSSProperties}></i>Delivered</span>
          <h3>A working set at session start</h3>
          <p><span>Claims about your repository, each bound to the exact source bytes behind it. Delivery counts only when the client’s own transcript shows it arrived.</span></p></button></li>
        <li><button type="button" aria-pressed="false"><span className="k"><i style={{ '--c': 'var(--lime)' } as CSSProperties}></i>Watched</span>
          <h3>Evidence, not timestamps</h3>
          <p><span>Every change produces a new repository view. A claim whose evidence moved is marked <code>needs_revalidation</code>, <code>unsupported</code> or replaced, deterministically.</span></p></button></li>
        <li><button type="button" aria-pressed="false"><span className="k"><i style={{ '--c': 'linear-gradient(135deg,var(--teal2),var(--lime))' } as CSSProperties}></i>Corrected</span>
          <h3>Before the edit, not after</h3>
          <p><span>An edit that depends on stale context is held, the correction is delivered, and the agent reconsiders. A resumed session is told what changed and which files to re-read.</span></p></button></li>
      </ol>
    </div>
    <ul className="key rv d3" aria-label="How to read the memex mark">
      <li><i style={{ '--c': 'linear-gradient(135deg,#7FDDE0,#00B3B5)' } as CSSProperties}></i>What the session was told</li>
      <li><i style={{ '--c': 'linear-gradient(135deg,#E6F8C8,#8EDC5A)' } as CSSProperties}></i>What the code says now</li>
      <li><i style={{ '--c': 'linear-gradient(135deg,#FFFFFF,#D8F5EE)' } as CSSProperties}></i>What still holds</li>
    </ul>
  </div>
</section>

<section className="block" id="plane">
  <div className="wrap">
    
    <h2 className="stmt" data-words>Six stages from a changed byte <em>to a held edit.</em></h2>
    <div className="plane rv d1">
      <ul className="legend" id="legend" aria-label="Pipeline stages"></ul>
      <div className="stage">
        <canvas id="planeCv" aria-hidden="true"></canvas>
        <div className="caption" id="caption" aria-live="polite"><div className="path"></div><p></p><div className="art"></div></div>
      </div>
    </div>
  </div>
</section>

<section className="block" id="hosts">
  <div className="wrap">
    
    <h2 className="stmt" data-words>Claude Code and Codex, <em>side by side.</em></h2>
    <p className="sub rv d1">Each session gets its own correction. Worktrees stay isolated, and unrelated edits pass untouched.</p>
    <table className="rv d2">
      <thead><tr><th>Capability</th><th>Claude Code</th><th>Codex</th></tr></thead>
      <tbody>
        <tr><td>Working set at session start, confirmed from the client’s record</td><td className="yes">Yes</td><td className="yes">Yes, in app-server (IDE and desktop) sessions</td></tr>
        <tr><td>Held edit and correction</td><td className="yes"><code>Edit</code>, <code>Write</code>, <code>MultiEdit</code>, <code>NotebookEdit</code></td><td className="yes"><code>apply_patch</code></td></tr>
        <tr><td>Change notice when a session resumes</td><td className="yes">Yes</td><td className="yes">Yes</td></tr>
        <tr><td>Concurrent sessions, worktrees and shared checkouts</td><td className="yes">Yes</td><td className="yes">Yes</td></tr>
        <tr><td>Guarded writes (opt-in)</td><td>Through <code>guard_read</code> / <code>guard_write</code></td><td>Same</td></tr>
        <tr><td>Shell edits</td><td>Reported, never certified</td><td>Same</td></tr>
      </tbody>
    </table>
  </div>
</section>

<section className="block" id="boundaries">
  <div className="wrap split">
    <div>
      
      <h2 className="stmt" data-words>What memex <em>will not pretend.</em></h2>
      <p className="sub rv d1">A context layer that overstates itself is worse than none. These limits are part of the design.</p>
    </div>
    <ul className="list">
      <li className="rv d1">It fails open. If memex cannot check, the edit proceeds and the agent is told freshness is unknown.</li>
      <li className="rv d2">It never grants permission. It can only hold an edit and ask the agent to reconsider.</li>
      <li className="rv d3">Shell writes, editors and other tools bypass the hooks; their changes are seen at the next check, not prevented.</li>
      <li className="rv d4"><code>codex exec</code> runs no hooks, so it gets no context and no checks.</li>
      <li className="rv d4">Legacy v0.9 knowledge is imported as <code>legacy_unverified</code> and never shown as current.</li>
    </ul>
  </div>
</section>

<section className="block night" id="evidence">
  <div className="glass g-teal" data-par="-0.05" data-rot="-8" aria-hidden="true"></div>
  <div className="glass g-lime" data-par="0.04" data-rot="10" aria-hidden="true"></div>
  <div className="wrap split ev" style={{ 'position': 'relative' } as CSSProperties}>
    <div>
      <h2 className="stmt" data-words>Preregistered, and published <em>in full.</em></h2>
      <p className="sub rv d1">Every evaluation freezes its definitions and thresholds before the first trial, runs on real clients, and is published with its data.</p>
      <a className="more rv d2" href="https://github.com/STiFLeR7/memex/tree/master/docs/v1">Read the reports ↗</a>
    </div>
    <dl className="ledger">
      <div className="row rv d1"><dt>Mechanism suite</dt><dd><b><span data-count="16">16</span> of 16 fixtures</b>Stale evidence, supersession, worktree isolation and authority, with no isolation or authority violation.</dd></div>
      <div className="row rv d2"><dt>Native clients</dt><dd><b>Claude Code and Codex</b>Live together, with delivery confirmed from each client’s own session record.</dd></div>
      <div className="row rv d3"><dt>Confirmatory run</dt><dd><b><span data-count="480">480</span> trials</b>Five comparison arms across 48 held-out histories on both hosts, analysed once against frozen thresholds.</dd></div>
    </dl>
  </div>
</section>

<section className="block install" id="install">
  <div className="wrap split">
    <div>
      
      <h2 className="stmt" data-words>A few commands <em>per repository.</em></h2>
      <p className="sub rv d1" style={{ 'marginBottom': '34px' } as CSSProperties}>memex uses the Neo4j you already run and installs hooks only into the repository’s own project settings.
        Start in shadow mode to see what it would correct without changing anything.</p>
      <ul className="list lime">
        <li className="rv d1">Python 3.11+, Git and a reachable Neo4j 5</li>
        <li className="rv d2">Your host client, logged in as usual. memex never asks for its credentials</li>
        <li className="rv d3"><code>memex v1 rollback</code> turns it off at once</li>
      </ul>
    </div>
    <div className="card rv d2" id="termCard">
      <div className="bar"><span className="dot"></span><span className="dot"></span><span className="dot"></span><span style={{ 'marginLeft': '8px' } as CSSProperties}>terminal</span>
        <button className="copy" id="copyAll" type="button">Copy</button></div>
<pre id="term" dangerouslySetInnerHTML={{ __html: "<span class=\"s\"># once</span>\n<span class=\"f\">pip</span> install memex-mcp\n\n<span class=\"s\"># in your repository</span>\n<span class=\"f\">memex</span> v1 install claude      <span class=\"s\"># or: codex</span>\n<span class=\"f\">memex</span> v1 doctor\n<span class=\"f\">memex</span> v1 mode shadow        <span class=\"s\"># optional first week</span>\n<span class=\"f\">memex</span> v1 mode live" }} />
    </div>
  </div>
</section>


<section className="closer" id="start">
  <div className="glass g-teal" data-par="-0.05" data-rot="-8" aria-hidden="true"></div>
  <div className="glass g-lime" data-par="0.04" data-rot="10" aria-hidden="true"></div>
  <div className="wrap">
    <svg className="big" aria-hidden="true"><use href="#mark"/></svg>
    <h2 className="stmt" data-words>Give your agents a memory of <em>what they were told.</em></h2>
    <p className="sub rv d1">Open source, MIT. Works with the Neo4j you already run, and never touches your client credentials.</p>
    <div className="actions rv d2">
      <a className="btn lg" href="https://github.com/STiFLeR7/memex/blob/master/docs/v1/25_ONBOARDING.md">Read the setup guide <span className="a" aria-hidden="true">↗</span></a>
      <a className="btn lg ghost" href="https://github.com/STiFLeR7/memex">View on GitHub <span className="a" aria-hidden="true">↗</span></a>
    </div>
  </div>
</section>

</main>

<footer>
  <div className="wrap">
    <a className="brand" href="#top" style={{ 'fontSize': '18px' } as CSSProperties}><svg width="28" height="28" aria-hidden="true"><use href="#mark"/></svg>memex <span className="ver" style={{ 'marginLeft': '4px' } as CSSProperties}><i aria-hidden="true"></i>v1.0.0</span></a>
    <nav aria-label="Footer">
      <a href="https://github.com/STiFLeR7/memex">GitHub</a>
      <a href="https://pypi.org/project/memex-mcp/">PyPI</a>
      <a href="https://www.npmjs.com/package/stifler-memex-mcp">npm</a>
      <a href="https://registry.modelcontextprotocol.io/">MCP Registry · io.github.STiFLeR7/memex</a>
      <a href="mailto:contact@stifler.in">Contact</a>
    </nav>
    <div>© 2026 memex · MIT · Hill Patel</div>
  </div>
</footer>
    </>
  );
}
