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

export const metadata: Metadata = {
  metadataBase: new URL('https://memex.stifler.in'),
  alternates: { canonical: '/' },
  title: 'memex: engineering context infrastructure',
  description:
    'memex builds a bitemporal knowledge graph of your repository (modules, symbols, decisions, problems) and serves bounded, provenance-aware context to coding agents over MCP.',
  authors: [{ name: 'Hill Patel', url: 'https://github.com/STiFLeR7' }],
  openGraph: {
    type: 'website',
    url: '/',
    locale: 'en_US',
    title: 'memex: engineering context infrastructure',
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
      <body>{children}</body>
    </html>
  );
}
