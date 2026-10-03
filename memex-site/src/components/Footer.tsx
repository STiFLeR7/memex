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
      { t: 'Security policy', u: `${REPO}/blob/master/SECURITY.md` },
    ],
  },
  {
    h: 'Install',
    links: [
      { t: 'PyPI · memex-mcp', u: 'https://pypi.org/project/memex-mcp/' },
      { t: 'npm · stifler-memex-mcp', u: 'https://www.npmjs.com/package/stifler-memex-mcp' },
      { t: 'Contributing', u: `${REPO}/blob/master/CONTRIBUTING.md` },
    ],
  },
] as const;

export default function Footer() {
  return (
    <footer>
      <div className="edge wrap">
        <div className="foot">
          <div>
            <a className="brand" href="#top">
              <Mark size={22} className="mk" />memex
            </a>
            <p>
              A bitemporal knowledge graph of repository facts, decisions,
              problems and relations, served to coding agents over MCP.
            </p>
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
          <span>memex v0.9.0 · MIT · Hill Patel</span>
          <span>Grounded in the current implementation</span>
        </div>
      </div>
    </footer>
  );
}
