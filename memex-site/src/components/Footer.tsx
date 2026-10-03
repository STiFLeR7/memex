import Mark from './Mark';

const REPO = 'https://github.com/STiFLeR7/memex';

const COLUMNS = [
  {
    h: 'System',
    links: [
      { t: 'Thesis', u: '#thesis' },
      { t: 'Control plane', u: '#plane' },
      { t: 'Temporal model', u: '#motion' },
      { t: 'MCP surface', u: '#artifacts' },
    ],
  },
  {
    h: 'Repository',
    links: [
      { t: 'GitHub', u: REPO },
      { t: 'README', u: `${REPO}/blob/master/README.md` },
      { t: 'Changelog', u: `${REPO}/blob/master/CHANGELOG.md` },
      { t: 'Evaluation', u: `${REPO}/blob/master/BENCHMARK.md` },
    ],
  },
  {
    h: 'Packages',
    links: [
      { t: 'PyPI · memex-mcp', u: 'https://pypi.org/project/memex-mcp/' },
      { t: 'npm · stifler-memex-mcp', u: 'https://www.npmjs.com/package/stifler-memex-mcp' },
      // The registry has no per-server page, and its API returns raw JSON, so
      // this points at the registry itself rather than at a payload.
      { t: 'MCP Registry', u: 'https://registry.modelcontextprotocol.io/' },
    ],
  },
  {
    h: 'Project',
    links: [
      { t: 'Licence', u: `${REPO}/blob/master/LICENSE` },
      { t: 'Contributing', u: `${REPO}/blob/master/CONTRIBUTING.md` },
      { t: 'Security policy', u: `${REPO}/blob/master/SECURITY.md` },
      { t: 'contact@stifler.in', u: 'mailto:contact@stifler.in' },
    ],
  },
] as const;

/* Author accounts, not project accounts: memex has none of its own. */
const SOCIAL = [
  {
    t: 'X',
    u: 'https://x.com/hillpatel07',
    d: 'M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z',
  },
  {
    t: 'LinkedIn',
    u: 'https://linkedin.com/in/hillpatel07',
    d: 'M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.225 0z',
  },
] as const;

export default function Footer() {
  return (
    <footer>
      <div className="edge wrap">
        <div className="foot">
          <div className="foot-brand">
            <a className="brand" href="#top">
              <Mark size={22} className="mk" />memex
            </a>
            <p>
              A bitemporal knowledge graph of repository facts, decisions,
              problems and relations, served to coding agents over MCP.
            </p>

            <a className="chip" href={`${REPO}/blob/master/LICENSE`}>
              <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                <path
                  fill="currentColor"
                  d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38v-1.34c-2.23.49-2.7-1.07-2.7-1.07-.36-.93-.89-1.18-.89-1.18-.73-.5.06-.49.06-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.88 2.34.67.07-.52.28-.88.51-1.08-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.19c0 .21.15.46.55.38A8 8 0 0 0 8 0Z"
                />
              </svg>
              Open source · MIT
            </a>

            <div className="socials">
              {SOCIAL.map((s) => (
                <a className="social" key={s.t} href={s.u} aria-label={s.t}>
                  <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
                    <path fill="currentColor" d={s.d} />
                  </svg>
                </a>
              ))}
            </div>
          </div>

          {COLUMNS.map((c) => (
            <div key={c.h}>
              <h4>{c.h}</h4>
              <ul>
                {c.links.map((l) => (
                  <li key={l.t}>
                    <a href={l.u}>{l.t}</a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="foot-end">
          <span>© 2026 memex · v0.9.0 · Hill Patel</span>
          <span>Grounded in the current implementation.</span>
        </div>
      </div>

      {/*
        Oversized mark-and-wordmark series, rolling continuously and clipped by
        the page edge. Two identical sets: the track travels exactly one set
        width, so the loop has no seam. Decorative, hence aria-hidden.
      */}
      <div className="foot-roll" aria-hidden="true">
        <div className="foot-roll-track">
          {[0, 1].map((set) => (
            <div className="foot-roll-set" key={set}>
              {Array.from({ length: 6 }, (_, i) => (
                <span className="foot-roll-item" key={i}>
                  <Mark size={100} />
                  memex
                </span>
              ))}
            </div>
          ))}
        </div>
      </div>
    </footer>
  );
}
