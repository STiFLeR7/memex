# memex-site

The memex website. Next.js 16 (App Router) + TypeScript, no CSS framework.

## Run

```bash
npm install
npm run dev          # http://localhost:3000
npm run build        # static export of /
npm start
npm run lint
npm run check        # decay-regime assertions (node:test, no test framework)
```

## Why no Tailwind

The visual design is a hand-authored token system — `--void`, `--ice`, a type
scale, timing curves, the glow and border primitives. It lives in
`src/app/globals.css` in 13 numbered blocks and is the approved artifact.
Porting it to utility classes would be a large diff with no functional gain and
would make the design harder to keep faithful to the reference.

`src/app/globals.css` is a direct port of `../memex-web/index.html`, which is
retained as the signed-off visual reference.

## Architecture

Most of the page is React Server Components. Interactivity is isolated to four
client islands, so the shipped JS stays small:

| Component | Why it is a client component |
|---|---|
| `Nav` | menu toggle, scroll state |
| `HeroMatrix` | canvas |
| `ControlPlane` | canvas + stage selection + scroll linkage |
| `Lifecycle` | canvas fields + bar fill on reveal |
| `Artifacts` | tool selection |
| `RevealEngine` | one IntersectionObserver for the whole page |

Everything else — `Hero`, `Thesis`, `Capabilities`, `Final`, `Footer`,
`Statement` — renders on the server.

### RevealEngine

One observer for the page. Sections stay server components and carry `.rv` /
`.stmt` classes; the island wires them up. The init pass is load-bearing: the
observer's `-12%` bottom margin would otherwise strand anything already on
screen (the hero CTAs sit at y≈921 in a 1000px viewport and never fired in the
static build). Honours `prefers-reduced-motion` by revealing everything
immediately.

### Statement

Headline words are split at render, not at runtime. The markup ships correct,
so headings are readable with JS disabled and there is no layout thrash.

Whitespace between words **must** be a sibling text node, never a child of
`.wr` — that span is `overflow:hidden` + `inline-block`, so a space inside it
is clipped and the words run together. This was a real regression during the
port.

## Content and claims

`src/lib/content.ts` holds every claim, each with the repository path that
substantiates it. `src/lib/confidence.ts` is a port of
`memex/graph/confidence.py::current_confidence` — the four readouts in the
Temporal section are computed from it, not written in by hand, so they move if
the constants do.

Three regimes, not two. The memex README says two; the code implements three.
The code wins. `npm run check` pins this.

Do not add a number to this site that does not trace to a file.

## Known

`next build` warns `Failed to find font override values for font 'Stack Sans
Headline'` — Next has no metric data for that face, so it cannot synthesise a
matched fallback. The font loads and renders correctly; the fallback is
Instrument Sans, which is self-hosted in the same request, so the swap is
visually minor.

The 5 high-severity `npm audit` findings are all in the ESLint dev chain
(`braces` → `micromatch` → `fast-glob` → `@next/eslint-plugin-next`). Dev-only,
not shipped to the browser. `npm audit fix --force` downgrades
`eslint-config-next` and breaks linting.
