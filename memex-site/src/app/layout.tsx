import type { Metadata, Viewport } from 'next';
import Script from 'next/script';
import { Stack_Sans_Headline, DM_Mono } from 'next/font/google';
import './globals.css';

/* Self-hosted at build time by next/font: no external request. */
const display = Stack_Sans_Headline({
  subsets: ['latin'],
  variable: '--f-display',
  display: 'swap',
});

const mono = DM_Mono({
  subsets: ['latin'],
  weight: ['300', '400', '500'],
  variable: '--f-mono',
  display: 'swap',
});

/* Runs before first paint so a returning dark-theme visitor never sees a light flash. */
const THEME_INIT =
  'try{document.documentElement.dataset.theme=localStorage.getItem("memex-theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light")}catch(e){document.documentElement.dataset.theme="light"}';

const SITE = 'https://memex.stifler.in';
const TITLE = 'memex v1 · Trusted engineering context for AI coding agents';
const DESCRIPTION =
  "memex keeps your coding agent's engineering context current as the code changes, and corrects it before an affected edit lands. Claude Code and Codex, over MCP.";

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
      softwareVersion: '1.0.2',
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
    description: "Keeps your agent's engineering context current as the code changes.",
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
        alt: 'memex v1.0.0: trusted engineering context for AI coding agents.',
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
        <script type="application/ld+json">{JSON.stringify(jsonLd)}</script>
        {children}
        <Script src="/site.js" strategy="afterInteractive" />
      </body>
    </html>
  );
}
