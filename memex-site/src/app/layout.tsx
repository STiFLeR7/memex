import type { Metadata, Viewport } from 'next';
import Script from 'next/script';
import { Stack_Sans_Headline, DM_Mono } from 'next/font/google';
import './globals.css';
import { AUTHOR_ID, JsonLd, RELEASED, SITE, VERSION, pageMeta } from './shell';

/* Self-hosted at build time by next/font: no external request.
   Next has no metric data for Stack Sans Headline, so it cannot synthesise a
   size-adjusted fallback and warned on every build. The fallback is declared
   instead: system-ui, as globals.css already uses. */
const display = Stack_Sans_Headline({
  subsets: ['latin'],
  variable: '--f-display',
  display: 'swap',
  adjustFontFallback: false,
  fallback: ['system-ui', 'sans-serif'],
});

const mono = DM_Mono({
  subsets: ['latin'],
  weight: ['300', '400', '500'],
  variable: '--f-mono',
  display: 'swap',
  preload: false, // only the display face is needed for the first paint (the H1)
});

/* Runs before first paint so a returning dark-theme visitor never sees a light
   flash. The js class marks that script runs, so JS-only states (collapsed
   steps, the pipeline canvas) never hide content from a reader without it. */
const THEME_INIT =
  'document.documentElement.classList.add("js");try{document.documentElement.dataset.theme=localStorage.getItem("memex-theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light")}catch(e){document.documentElement.dataset.theme="light"}';

const TITLE = 'memex v1 · Trusted engineering context for AI coding agents';
const DESCRIPTION =
  "memex keeps your coding agent's engineering context current as the code changes, and corrects it before an affected edit lands. Claude Code and Codex, over MCP.";

/* Site-wide JSON-LD. Each page adds its own WebPage node (shell.tsx pageLd).
   SoftwareApplication, not SoftwareSourceCode: this site markets a thing you
   install and run (pip/npm/MCP Registry), not a source tree you read. The
   repository and the registries go in sameAs so the same entity resolves
   across all four places. Every value here is sourced from pyproject.toml,
   CHANGELOG.md, the README or the published packages; fields that would need
   numbers we cannot source (aggregateRating, downloadCount) are omitted
   rather than guessed. */
const jsonLd = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'WebSite',
      '@id': `${SITE}/#website`,
      url: `${SITE}/`,
      name: 'memex',
      alternateName: 'memex-mcp',
      description: DESCRIPTION,
      inLanguage: 'en',
      publisher: { '@id': AUTHOR_ID },
    },
    {
      '@type': 'SoftwareApplication',
      '@id': `${SITE}/#memex`,
      name: 'memex',
      alternateName: ['memex-mcp', 'stifler-memex-mcp'],
      description: DESCRIPTION,
      url: `${SITE}/`,
      image: `${SITE}/og.jpg`,
      screenshot: `${SITE}/memex-film-live-context.webp`,
      applicationCategory: 'DeveloperApplication',
      applicationSubCategory: 'MCP server and hook service for AI coding agents',
      // "Operating System :: OS Independent" in pyproject.toml.
      operatingSystem: 'Any',
      softwareVersion: VERSION,
      dateModified: RELEASED,
      softwareRequirements: 'Python 3.11 or newer, Git, Neo4j 5',
      downloadUrl: 'https://pypi.org/project/memex-mcp/',
      installUrl: `${SITE}/setup`,
      releaseNotes: `${SITE}/changelog`,
      license: 'https://opensource.org/licenses/MIT',
      isAccessibleForFree: true,
      identifier: 'io.github.STiFLeR7/memex',
      sameAs: [
        'https://github.com/STiFLeR7/memex',
        'https://pypi.org/project/memex-mcp/',
        'https://www.npmjs.com/package/stifler-memex-mcp',
      ],
      offers: { '@type': 'Offer', price: 0, priceCurrency: 'USD' },
      author: { '@id': AUTHOR_ID },
      contributor: { '@id': `${SITE}/#nirvaan` },
    },
    {
      '@type': 'Person',
      '@id': AUTHOR_ID,
      name: 'Hill Patel',
      // README.md, "Core Contributors & Maintainers".
      jobTitle: 'Architect and maintainer of memex',
      url: 'https://github.com/STiFLeR7',
      sameAs: ['https://github.com/STiFLeR7', 'https://x.com/hillpatel07'],
    },
    {
      '@type': 'Person',
      '@id': `${SITE}/#nirvaan`,
      name: 'Nirvaan Lagishetty',
      jobTitle: 'Lead contributor and maintainer of memex',
      url: 'https://github.com/Nirvaan05',
    },
  ],
};

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  authors: [{ name: 'Hill Patel', url: 'https://github.com/STiFLeR7' }],
  ...pageMeta('/', TITLE, DESCRIPTION),
};

export const viewport: Viewport = {
  themeColor: '#F7F9F8',
  colorScheme: 'light dark',
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body>
        {/* A single string child of <script> is emitted verbatim by react-dom
            (escapeEntireInlineScriptContent only rewrites </script), so the
            JSON survives intact without dangerouslySetInnerHTML. */}
        <JsonLd data={jsonLd} />
        {children}
        <Script src="/site.js" strategy="afterInteractive" />
      </body>
    </html>
  );
}
