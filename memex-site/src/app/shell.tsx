import type { Metadata } from 'next';
import type { CSSProperties, ReactNode } from 'react';

/* Shared by every page: site constants, the pipeline stages, the mark, the
   footer, the doc-page frame and per-page metadata / JSON-LD. */

export const SITE = 'https://memex.stifler.in';
export const VERSION = '1.0.3';
export const RELEASED = '2026-10-26'; // CHANGELOG.md, [1.0.3]
export const UPDATED = '2026-10-27'; // bump when the copy changes (sitemap lastmod, dateModified)
export const AUTHOR_ID = `${SITE}/#author`;

/* Pipeline stages. Rendered as visible text on the home page and on
   /how-it-works; public/site.js only highlights the active one. */
export const STAGES = [
  { n: '01', t: 'Capture', p: 'memex/runtime/views.py', c: 'Two matching listings and reads of every permitted source, with HEAD, or no view at all. Drift during capture is rejected, not averaged.', a: 'EMITS → stable source capture' },
  { n: '02', t: 'Index', p: 'memex/runtime/coordinator.py', c: 'Each capture becomes an immutable repository view. The graph commit precedes the local acknowledgement, and an already published view is never rewritten.', a: 'EMITS → RepositoryView, structure' },
  { n: '03', t: 'Verify', p: 'memex/runtime/verification.py', c: 'Every claim is checked against the evidence it cites, at this view: hashes, symbols, tests, approvals and supersession. Deterministic, with no model in the loop.', a: 'DERIVES → supported · needs_revalidation · unsupported' },
  { n: '04', t: 'Deliver', p: 'memex/integrations/host_adapter.py', c: "A bounded packet goes into the session, marked so it can be found again. It counts as delivered only when the client's own transcript shows it arrived whole.", a: 'HOLDS → what each session was told' },
  { n: '05', t: 'Check', p: 'memex/runtime/actions.py', c: 'Before a declared edit, its targets and the evidence behind delivered claims are rechecked against the current view, through a long-lived hook service.', a: 'DECIDES → proceed · replan · unknown' },
  { n: '06', t: 'Correct', p: 'memex/integrations/host_adapter.py', c: 'An affected edit is held, and the correction names what changed and what to re-read. If memex cannot check, the edit proceeds and the agent is told so.', a: 'EMITS → correction, change notice' },
];

export const PAGES = [
  { href: '/how-it-works', label: 'How it works' },
  { href: '/setup', label: 'Setup' },
  { href: '/evaluation', label: 'Evaluation' },
  { href: '/changelog', label: 'Changelog' },
];

const OG_IMAGE = {
  url: '/og.jpg',
  // metadataBase does not resolve this one, and a relative
  // og:image:secure_url is invalid, so it is absolute by hand.
  secureUrl: `${SITE}/og.jpg`,
  width: 1200,
  height: 630,
  type: 'image/jpeg',
  alt: `memex v${VERSION}: trusted engineering context for AI coding agents.`,
};

/* Next merges metadata shallowly, so every page restates openGraph in full. */
export function pageMeta(path: string, title: string, description: string): Metadata {
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: { type: 'website', url: path, locale: 'en_US', siteName: 'memex', title, description, images: [OG_IMAGE] },
    twitter: { card: 'summary_large_image', site: '@hillpatel07', creator: '@hillpatel07', title, description, images: ['/og.jpg'] },
  };
}

/* WebPage node (plus a breadcrumb on inner pages) for one URL. */
export function pageLd(path: string, name: string, description: string, extra: object[] = []) {
  const url = path === '/' ? `${SITE}/` : `${SITE}${path}`;
  const graph: object[] = [{
    '@type': 'WebPage',
    '@id': `${url}#webpage`,
    url,
    name,
    description,
    inLanguage: 'en',
    isPartOf: { '@id': `${SITE}/#website` },
    about: { '@id': `${SITE}/#memex` },
    author: { '@id': AUTHOR_ID },
    primaryImageOfPage: `${SITE}/og.jpg`,
    dateModified: UPDATED,
  }];
  if (path !== '/') graph.push({
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: 'memex', item: `${SITE}/` },
      { '@type': 'ListItem', position: 2, name, item: url },
    ],
  });
  return { '@context': 'https://schema.org', '@graph': [...graph, ...extra] };
}

export function JsonLd({ data }: { data: object }) {
  // A single string child of <script> is emitted verbatim by react-dom.
  return <script type="application/ld+json">{JSON.stringify(data)}</script>;
}

export function MarkDefs() {
  return (
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
  );
}

export function ThemeButton() {
  return (
    <button className="theme" id="theme" type="button" aria-label="Switch to dark theme" aria-pressed="false">
      <svg className="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/></svg>
      <svg className="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true"><path d="M20.2 14.6A8.2 8.2 0 0 1 9.4 3.8a8.2 8.2 0 1 0 10.8 10.8Z"/></svg>
    </button>
  );
}

export function Footer() {
  return (
    <footer>
      <div className="wrap">
        <a className="brand" href="/" style={{ 'fontSize': '18px' } as CSSProperties}><svg width="28" height="28" aria-hidden="true"><use href="#mark"/></svg>memex <span className="ver" style={{ 'marginLeft': '4px' } as CSSProperties}><i aria-hidden="true"></i>v1.0.3</span></a>
        <nav aria-label="Footer">
          {PAGES.map(p => <a key={p.href} href={p.href}>{p.label}</a>)}
          <a href="https://github.com/STiFLeR7/memex">GitHub</a>
          <a href="https://pypi.org/project/memex-mcp/">PyPI</a>
          <a href="https://www.npmjs.com/package/stifler-memex-mcp">npm</a>
          <a href="https://registry.modelcontextprotocol.io/">MCP Registry · io.github.STiFLeR7/memex</a>
          <a href="mailto:contact@stifler.in">Contact</a>
        </nav>
        <div className="maint">
          <p>Built and maintained by <a href="https://github.com/STiFLeR7">Hill Patel</a> (architect and maintainer;{' '}
            <a href="https://x.com/hillpatel07">@hillpatel07</a>) with <a href="https://github.com/Nirvaan05">Nirvaan Lagishetty</a> (lead contributor and maintainer).</p>
          <p>Released {RELEASED} · page updated <time dateTime={UPDATED}>{UPDATED}</time></p>
        </div>
        <div className="legal"><span>© 2026 memex · MIT · Hill Patel</span><a className="credit" href="https://x.com/SoT__Design" target="_blank" rel="noopener">Designed by SystemOfThoughts</a></div>
      </div>
    </footer>
  );
}

/* Frame for the inner pages: nav, breadcrumb, H1 and lede, then the body. */
export function DocShell({ path, title, lede, children }: { path: string; title: string; lede: ReactNode; children: ReactNode }) {
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <MarkDefs />
      <nav className="top scrolled" aria-label="Primary">
        <div className="wrap">
          <a className="brand" href="/"><svg width="34" height="34" aria-hidden="true"><use href="#mark"/></svg>memex</a>
          <span className="ver"><i aria-hidden="true"></i>v1.0.3</span>
          <ul>
            {PAGES.map(p => <li key={p.href}><a href={p.href} aria-current={p.href === path ? 'page' : undefined}>{p.label}</a></li>)}
          </ul>
          <ThemeButton />
          <a className="btn" href="https://github.com/STiFLeR7/memex">GitHub <span className="a" aria-hidden="true">↗</span></a>
        </div>
        <div className="progress" aria-hidden="true"></div>
      </nav>
      <main id="main" tabIndex={-1}>
        <article className="doc wrap">
          <nav aria-label="Breadcrumb"><ol className="crumbs"><li><a href="/">memex</a></li><li aria-current="page">{title}</li></ol></nav>
          <h1>{title}</h1>
          <p className="lede">{lede}</p>
          <p className="meta">memex v1.0.3 · released {RELEASED} · updated <time dateTime={UPDATED}>{UPDATED}</time></p>
          {children}
        </article>
      </main>
      <Footer />
    </>
  );
}
