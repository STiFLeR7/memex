import type { Metadata, Viewport } from 'next';
import {
  Stack_Sans_Headline,
  Instrument_Sans,
  Instrument_Serif,
  DM_Mono,
} from 'next/font/google';
import './globals.css';

/* Self-hosted at build time by next/font — no external request, no FOUT.
   Weights are exactly what globals.css asks for, nothing speculative. */
const display = Stack_Sans_Headline({
  subsets: ['latin'],
  weight: ['400', '600'],
  variable: '--f-display',
  display: 'swap',
});

const body = Instrument_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--f-body',
  display: 'swap',
});

const serif = Instrument_Serif({
  subsets: ['latin'],
  weight: '400',
  style: 'italic',
  variable: '--f-serif',
  display: 'swap',
});

const mono = DM_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--f-mono',
  display: 'swap',
});

const SITE = 'https://memex.stifler.in';
const TITLE = 'memex: engineering context infrastructure';
const DESCRIPTION =
  'memex builds a bitemporal knowledge graph of your repository (modules, symbols, decisions, problems) and serves bounded, provenance-aware context to coding agents over MCP.';

/* SoftwareApplication, not SoftwareSourceCode: this page markets a thing you
   install and run (pip/npm/MCP Registry), not a source tree you read. The
   repository and the registries go in sameAs so the same entity resolves
   across all four places. Every value here is sourced from pyproject.toml or
   the published packages; fields that would need numbers we cannot source
   (aggregateRating, downloadCount) are omitted rather than guessed. */
const AUTHOR_ID = `${SITE}/#author`;

const jsonLd = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'SoftwareApplication',
      '@id': `${SITE}/#memex`,
      name: 'memex',
      description: DESCRIPTION,
      url: SITE,
      applicationCategory: 'DeveloperApplication',
      // "Operating System :: OS Independent" in pyproject.toml.
      operatingSystem: 'Any',
      softwareVersion: '0.9.0',
      softwareRequirements: 'Python 3.11 or newer',
      license: 'https://opensource.org/licenses/MIT',
      identifier: 'io.github.STiFLeR7/memex',
      sameAs: [
        'https://github.com/STiFLeR7/memex',
        'https://pypi.org/project/memex-mcp/',
        'https://www.npmjs.com/package/stifler-memex-mcp',
      ],
      offers: { '@type': 'Offer', price: 0, priceCurrency: 'USD' },
      author: { '@id': AUTHOR_ID },
    },
    {
      '@type': 'Person',
      '@id': AUTHOR_ID,
      name: 'Hill Patel',
      url: 'https://github.com/STiFLeR7',
      sameAs: ['https://github.com/STiFLeR7', 'https://x.com/hillpatel07'],
    },
  ],
};

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  alternates: { canonical: '/' },
  title: TITLE,
  description: DESCRIPTION,
  authors: [{ name: 'Hill Patel', url: 'https://github.com/STiFLeR7' }],
  openGraph: {
    type: 'website',
    url: '/',
    locale: 'en_US',
    title: TITLE,
    description:
      'A bitemporal knowledge graph of a repository, served to coding agents as bounded, provenance-carrying context.',
    siteName: 'memex',
    images: [
      {
        url: '/og.jpg',
        // metadataBase does not resolve this one, and a relative
        // og:image:secure_url is invalid, so it is absolute by hand.
        secureUrl: 'https://memex.stifler.in/og.jpg',
        width: 1200,
        height: 630,
        type: 'image/jpeg',
        alt: 'memex: a knowledge graph for the parts of engineering work files do not explain.',
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    site: '@hillpatel07',
    creator: '@hillpatel07',
    images: ['/og.jpg'],
  },
};

export const viewport: Viewport = {
  themeColor: '#02050B',
  colorScheme: 'dark',
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${serif.variable} ${mono.variable}`}
    >
      <body>
        {/* A single string child of <script> is emitted verbatim by react-dom
            (escapeEntireInlineScriptContent only rewrites </script), so the
            JSON survives intact without dangerouslySetInnerHTML. */}
        <script type="application/ld+json">{JSON.stringify(jsonLd)}</script>
        {children}
      </body>
    </html>
  );
}
