# Memex website — gap & resolution checklist

Created 2026-10-25 from a read-only audit of this folder. Work each item, tick it only when the "Done when" condition is verified, and fill in **Resolution** (what changed, files, how verified).

Governing sources (read before changing anything): `D:/memex/docs/web/CONTENT.md` (editorial source), `D:/memex/docs/web/archive/` (previous creative direction + prototype), brand assets in `D:/memex/brag-output/brand/`, reference images in `D:/memex/brag-ref/`, and this folder's `README.md` rules (no Tailwind; every number traces to a file; readable with JS off; code wins over README where they disagree).

⚠️ The memex repo has uncommitted changes that predate this checklist. Don't revert or commit them; check them before deploying (see M-12).

## Items

- [x] **M-01 · Creative-direction compliance audit.** Re-read the governing sources (incl. images) and check the page against them: tokens, type (Stack Sans Headline + DM Mono), teal/lime meaning, motion rules, honesty rules. Fix every deviation or record why it stays.
  - Done when: a deviation table (rule → status → fix) is in Resolution.
  - Resolution: Sources read: CONTENT.md, archive brief + README, brand board (`brag-output/brand/index.html` + `memex-brand-board.png`), `brag-plan.md`, and brag-ref frames (r001, r015, r030, r056, ref0, ref1). Finding: brag-ref is the ARDA reference film (dark/orange) used for the brag video's motion, not a site palette; the archive Swiss/"Lumex" brief is superseded (its README says so). The governing visual system is the brand board.
    | Rule (source) | Status | Fix |
    |---|---|---|
    | Tokens: paper #F7F9F8, ink #111417/#4A5157/#8A9399, teal #00B3B5→#7FDDE0, lime #8EDC5A→#E6F8C8 (brand 05) | match | none |
    | Paper carries faint vertical hairlines (brag-plan Identity, brand body) | deviated: light page was flat | added the 34px hairline background to light-theme body (globals.css) |
    | Type: Stack Sans Headline 200-300 display, H1 220/-3.5%, DM Mono code (brand 06) | match (h1 220/-0.035em, h2 220, body 300, code DM Mono) | none |
    | Teal = memex acting, lime = code now, white = still holds (brand 02/05) | match: mark, told/now model, hero lattice teal→lime, held chip teal, steps Delivered teal / Watched lime | version-badge lime dot kept (reads as "current release") |
    | Voice: no sales CTA, no hype (CONTENT 04/06, archive 13 "not Get started today!") | deviated: hero CTA "Get started" | now "Install memex" (page.tsx) |
    | Honesty: limitations beside claims; never imply an evaluation passed (CONTENT 04/17/19, brand 10) | deviated: 480-trial row omitted that S05 failed efficacy, precision, latency | added "Five gates passed; efficacy, precision and latency did not." (source docs/v1/28_PHASE6_RESULTS.md; pinned by npm run check) |
    | Every number traces (README) | 16/16, 480, 48, five arms, 60 s film, v1.0.2 all traced | pinned in scripts/content.check.mjs |
    | Motion: controlled, prefers-reduced-motion honoured (archive 16/18, brand) | match; reduced motion verified by site-check | none |
    | Readable with JS off / accessibility (archive 19, README) | deviated: pipeline section empty and How-it-works step text hidden with JS off | stages server-rendered in page.tsx (site.js reads data attrs), `.js` class set pre-paint gates collapsing/canvas |
    | Archive Swiss grid / mesh gradients | not applied: superseded direction | recorded, no change |
- [x] **M-02 · README architecture is wrong.** It describes client islands (Nav, HeroMatrix, ControlPlane, Lifecycle, Artifacts, RevealEngine) and `src/lib/content.ts`, none of which exist. It also says globals.css ports `../memex-web/index.html`, which no longer exists. Rewrite to match the real structure (`page.tsx` markup + `public/site.js`).
  - Resolution: Rewrote `README.md` for the real structure (page.tsx markup, layout.tsx, globals.css, public/site.js, llms.txt, scripts), design source (brand board), rules, and current known issues. Removed the islands / content.ts / memex-web claims. Verified each listed file exists.
- [x] **M-03 · Version mismatch.** JSON-LD says SoftwareApplication version 1.0.2; the page badge says v1.0.0. Confirm the real released version from the memex repo and make every mention agree.
  - Resolution: Released version is 1.0.2 (`pyproject.toml`, `npm/package.json`, `server.json`, tag v1.0.2, CHANGELOG 2026-10-08). JSON-LD was right; changed the nav and footer badges and the og:image alt to v1.0.2 (page.tsx, layout.tsx), and the OG card (M-04). `npm run check` now fails if any version in page.tsx, layout.tsx, llms.txt or og.html differs from pyproject.toml. The 60 s film and its poster still show the v1.0.0 release; left as is (it is a film of that release).
- [x] **M-04 · Stale OG source.** `scripts/og.html` is the old v0.9 blue design and copy. Update it to the v1 design/copy and regenerate `public/og.jpg` (1200×630).
  - Resolution: Replaced `scripts/og.html` with the v1 card (same composition as the 1200 banner in brag-output/brand/render.py, v1.0.2 badge). Added `npm run og` (`scripts/og.mjs`, Playwright + Chrome) and regenerated `public/og.jpg`: 1200×630, baseline JPEG (SOF0), visually checked; identical to the previous card except the version.
- [x] **M-05 · Dead code.** Unused `.tile` / `.grid3` CSS and the tile handler in `public/site.js`. Remove. Decide on `src/lib/confidence.ts` (only used by its check): keep and document why, or remove.
  - Resolution: Removed `.tile`/`.grid3`/`.night .tile*` CSS and the tile pointer handler in site.js, plus other dead selectors found by grep (`.ctas`, `.stat`, `.float`, `.glass > .float`). Removed `src/lib/confidence.ts` + its check: the v1 page has no decay readouts, so it was a port with no consumer. `npm run check` now runs `scripts/content.check.mjs` (versions + evidence numbers). Build/lint/site-check green.
- [x] **M-06 · No llms.txt.** Every sibling site ships one. Add `public/llms.txt` summarising the page with source links and the "will not pretend" limits.
  - Resolution: Added `public/llms.txt` (summary, install, six stages, hosts, evidence including the failed S05 gates, the five "will not pretend" limits, source links). Live at https://memex.stifler.in/llms.txt (200, text/plain).
- [x] **M-07 · CONTENT.md is dated v0.9.0.** Sync `D:/memex/docs/web/CONTENT.md` to the v1 page (it's the editorial source), and fix the archive README's "dark, cinematic" description of the current design (it's light-by-default with a dark toggle).
  - Resolution: `docs/web/CONTENT.md`: header now v1.0.2; new section 00 "v1 Homepage (as shipped)" with the shipped copy and a source for every figure; section 06 marked as the superseded v0.9 draft; section 16 lists the shipped title/description/social card; section 19 says S05 did not pass. `docs/web/archive/README.md`: "dark, cinematic" replaced with the v1 brand, light by default with a dark toggle.
- [x] **M-08 · Font override warning** for Stack Sans Headline (README known issue). Resolve it.
  - Resolution: `layout.tsx` sets `adjustFontFallback: false` and `fallback: ['system-ui','sans-serif']` for Stack Sans Headline (Next has no metrics for it). `next build` no longer prints the font override warning (grep count 0).
- [x] **M-09 · No site QA check.** Add a minimal runnable check against `out/` (console errors, failed requests, overflow at 1440/390, headings, anchors, reduced motion final states, JS-off readable, theme toggle works). Wire as npm script.
  - Resolution: Added `scripts/site-check.mjs`, wired as `npm run site-check` (Playwright devDependency, Chrome channel, serves out/ itself). Checks console errors, page errors, failed requests and HTTP ≥400; horizontal overflow at 1440 and 390; one h1, no empty headings or skipped levels; every #anchor resolves; reduced motion final states (reveals, hero demo, pipeline caption); JS off (content visible, 6 stages, >3000 chars); theme toggle flips, persists and survives reload; og/robots/sitemap/llms/icon/poster served; unknown path 404. It caught the two JS-off bugs fixed under M-01. Passes.
- [x] **M-10 · npm audit (5 dev-only findings).** Re-check; fix if a non-breaking update exists, otherwise record.
  - Resolution: The audit had changed since the README note: next 16.3.8 had a critical advisory, plus sharp/undici/zod highs. Upgraded next and eslint-config-next to 16.4.3 (exact pins) and ran `npm audit fix`. Remaining: 3 findings (1 high, 2 moderate) in undici under wrangler → miniflare, dev/deploy tooling only, not shipped; the only fix offered is a breaking wrangler downgrade, so recorded in README Known.
- [x] **M-11 · Build, lint, `npm run check`, site check all green.**
  - Resolution: On the final tree (Next 16.4.3): `npm run build` clean (no warnings), `npm run lint` clean, `npm run check` 2/2 pass, `npm run site-check` all pass.
- [x] **M-12 · Pre-existing uncommitted changes reviewed.** List them; confirm they're safe to ship before deploying (if something looks unfinished or broken, stop and report instead of deploying).
  - Resolution: At start, `git status` in D:/memex showed no modified tracked files; the pre-existing changes are all untracked: `.planning/`, `.wrangler/`, `AUDIT.pdf`, `brag-output*/`, `brag-ref/`, `goal10_live_matrix.py/.sh`, `output/`, `wireframes/`, and this CHECKLIST.md. None is inside the build input except CHECKLIST.md, which is not in out/. The deploy uploads only `memex-site/out/`, so none of them shipped. Nothing committed, stashed or reverted.
- [x] **M-13 · Deploy.** `npm run deploy` (Worker `memex` → memex.stifler.in). Record version id.
  - Resolution: `npm run deploy` succeeded. Worker `memex`, Current Version ID `83b5f114-d0a5-4023-8564-d04d0922c606`, custom domain memex.stifler.in (19 new assets uploaded).
- [x] **M-14 · Verify live.** https://memex.stifler.in/ 200, matches build, og/robots/sitemap/llms reachable, video plays, 404 works.
  - Resolution: Live checks: / 200 and byte-identical to out/index.html; og.jpg, site.js identical to local; robots.txt, sitemap.xml, llms.txt, icon.svg, poster 200; /nope-404 returns 404 with the 404 page; 6× "v1.0.2" in live HTML. Chrome on the live site: no console errors or failed requests, 6 pipeline stages, the film plays (currentTime 2.4 s after 3 s, 1920 wide). Note: the mp4 answers Range requests with 200 (full file) rather than 206; playback works, seeking ahead may wait for download. Not changed.

## Sign-off
- [x] All items above ticked or explicitly deferred with a reason.

## SEO / AEO / GEO (audit 2026-10-26)

Source: `D:/SEO/memex.stifler.in-audit/ACTION-PLAN.md` (scores before: health 68, SEO 69, AEO 52, GEO 61). Re-audit and projected scores: `D:/SEO/memex.stifler.in-audit/RE-AUDIT-2026-10-26.md`. Items marked **Owner:** cannot be done from this repo; the exact command or edit is given. Nothing here was deployed or committed.

- [ ] **S-H1 · High · HTTP is not redirected to HTTPS** (Cloudflare zone, not in repo). `http://memex.stifler.in/` returns 200.
  - Owner: Cloudflare dashboard → stifler.in → SSL/TLS → Edge Certificates → **Always Use HTTPS: On**. Verify: `curl -sI http://memex.stifler.in/` → `301` with `Location: https://memex.stifler.in/`. (The new HSTS header only protects browsers after their first HTTPS visit, so the toggle is still needed.)
- [x] **S-H2 · High · No security or caching headers** (`public/_headers`, new).
  - Resolution: added `public/_headers`: HSTS (1 year, includeSubDomains), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, `X-Frame-Options: DENY`, COOP, and `/_next/static/*` → `Cache-Control: public, max-age=31536000, immutable`. CSP deliberately left out (needs hashes for the theme script and Next's inline flight scripts). Verified on the local preview with `curl -I`; `npm run site-check` fails if `out/_headers` lacks HSTS or the immutable rule. Live check after deploy: `curl -sI https://memex.stifler.in/ | grep -i strict` and `curl -sI https://memex.stifler.in/_next/static/chunks/<any>.js | grep -i cache-control`.
- [x] **S-H3 · High · Pipeline stages 02-06 were not visible text** (`src/app/page.tsx` ~142-153, `public/site.js`).
  - Resolution: the six stages are now a visible `<ol id="stages">` (an `<h3>`, description, module and output per stage), shared via `STAGES` in `src/app/shell.tsx`; the `<noscript>` copy and the data-attribute caption are gone. `site.js` only marks the active stage. Verified: rendered probe shows 6/6 stage descriptions visible with JS on and off; site-check asserts it.
- [ ] **S-H4 · High · Off-site descriptions are v0.9 and contradict the site** (GitHub About, `D:/memex/README.md`, `D:/memex/pyproject.toml`, `D:/memex/npm/package.json`).
  - Owner (one-liner to use everywhere: "Open-source MCP server and hook service that keeps Claude Code and Codex engineering context current as the code changes, and holds edits whose evidence changed."):
    - GitHub: `gh repo edit STiFLeR7/memex --description "Open-source MCP server and hook service that keeps Claude Code and Codex engineering context current as the code changes, and holds edits whose evidence changed." --homepage https://memex.stifler.in`
    - `D:/memex/pyproject.toml`: `description = "Open-source MCP server and hook service that keeps Claude Code and Codex engineering context current as the code changes."`, and under `[project.urls]` set `Homepage = "https://memex.stifler.in"` and add `Repository = "https://github.com/STiFLeR7/memex"`. Reaches PyPI at the next release.
    - `D:/memex/npm/package.json`: the same `description` and `"homepage": "https://memex.stifler.in"`. Reaches npm at the next publish.
    - `D:/memex/README.md` lead blockquote: replace "memex builds a bitemporal knowledge graph..." with the one-liner; keep the graph detail lower down.
    - Ask glama.ai, pulsemcp.com, mcpservers.org and the TensorBlock awesome list to refresh the description and list `https://memex.stifler.in` as the homepage.
- [x] **S-H5 · High · No FAQ / question headings** (`src/app/page.tsx`, `src/app/globals.css`).
  - Resolution: new `#faq` section before Install with 8 `<h3>` questions and 50-80-word answers (what memex is; which agents / Cursor; Neo4j; down or cannot check; vs Mem0 / RAG; free; more accurate or faster (the honest S05 answer); install). Each answer's source file is named in a comment; the S05 figures are pinned by `npm run check`. No FAQPage schema (info-only per the plugin; rich results retired). Linked from the nav.
- [x] **S-H6 · High · No entity definition under the H1; "memex-mcp" absent from prose** (`src/app/page.tsx` hero).
  - Resolution: the lede now opens "memex is an open-source MCP server and hook service (`memex-mcp`) that keeps your coding agent's engineering context current..." and names Claude Code and Codex; the first FAQ answer repeats the definition with both package names.
- [x] **S-M1 · Medium · Single-URL site** (new routes, `src/app/sitemap.ts`, nav).
  - Resolution: added `/how-it-works`, `/setup`, `/evaluation`, `/changelog`, each drawn from the memex repo (`docs/v1/01`, `21`, `24`, `25`, `28`, `CHANGELOG.md`) with question-shaped H2s, per-page title/description/canonical/OG, WebPage + BreadcrumbList JSON-LD, and links from the home nav, the footer and the body. The FAQ stays on the home page (no separate /faq). Shared frame in `src/app/shell.tsx`. The sitemap lists all 5 URLs.
- [x] **S-M2 · Medium · Missing JSON-LD nodes** (`src/app/layout.tsx`, pages).
  - Resolution: layout emits WebSite, SoftwareApplication (+ alternateName memex-mcp, image, screenshot, downloadUrl, installUrl, releaseNotes, dateModified, isAccessibleForFree, contributor), Person (Hill Patel, jobTitle from the README) and Person (Nirvaan Lagishetty). Each page adds WebPage with `dateModified`; home adds VideoObject (uploadDate 2026-10-08, PT1M); inner pages add BreadcrumbList. No HowTo, no FAQPage, no ratings. Validate in the Rich Results Test after deploy.
- [x] **S-M3 · Medium · No visible dates** (`src/app/shell.tsx` footer and doc header).
  - Resolution: the footer shows "Released 2026-10-08 · page updated 2026-10-26"; inner pages show "memex v1.0.2 · released 2026-10-08 · updated 2026-10-26". `npm run check` checks `RELEASED` against CHANGELOG.md.
- [x] **S-M4 · Medium · 187 KB JPEG poster** (`public/memex-v1-video.jpg`, `src/app/page.tsx`).
  - Resolution: `public/memex-v1-video.webp` (38 KB, same 1920×1080) is the poster; the JPEG stays only as the VideoObject thumbnail and is not loaded by the page.
- [ ] **S-M5 · Medium · mp4 ignores Range requests; film not on YouTube** (`public/memex-v1.mp4`, `src/app/page.tsx`).
  - Owner: host the mp4 on R2 (`npx wrangler r2 bucket create memex-media`, then `npx wrangler r2 object put memex-media/memex-v1.mp4 --file public/memex-v1.mp4 --content-type video/mp4 --remote`, and bind a public custom domain such as media.stifler.in) or Cloudflare Stream, then change `<source src>` in `src/app/page.tsx` and `contentUrl` in `FILM`. Publish the film on YouTube with a description linking https://memex.stifler.in and add that URL to `FILM` as `embedUrl`.
- [ ] **S-M6 · Medium · AI training-crawler policy is Cloudflare's default** (Cloudflare AI Crawl Control).
  - Owner decision: Cloudflare → AI Crawl Control → managed robots.txt. At minimum add `ai-input=yes` to the Content-Signal line if grounding/RAG use is wanted; decide whether to unblock GPTBot, ClaudeBot, Google-Extended, CCBot and Applebot-Extended. No repo change.
- [x] **S-M7 · Medium · H1/H2 carry no descriptive keywords** (`src/app/page.tsx`).
  - Resolution: eyebrow line above the H1 ("Context for AI coding agents · Claude Code, Codex, MCP"); the poetic H1/H2s stay; the pipeline section got a plain lead sentence; inner pages use descriptive, question-shaped H2s.
- [x] **S-M8 · Medium · Thin author entity** (`src/app/shell.tsx` footer, `src/app/layout.tsx`).
  - Resolution: footer maintainer block (Hill Patel, architect and maintainer, GitHub and X; Nirvaan Lagishetty, lead contributor and maintainer; roles from the memex README) and Person `jobTitle`. Owner: supply the LinkedIn profile URL to add to Person `sameAs` (not added: no sourced URL).
- [ ] **S-M9 · Medium · Indexing unverified** (GSC / Bing).
  - Owner: verify `memex.stifler.in` (or the `stifler.in` domain property via DNS TXT) in Google Search Console and Bing Webmaster Tools, submit `https://memex.stifler.in/sitemap.xml`, and request indexing of `/`, `/how-it-works`, `/setup`, `/evaluation` and `/changelog` after deploy.
- [ ] **S-M10 · Medium · PSI / Lighthouse Agentic not measured** (quota).
  - Owner, after deploy and with a Google API key: `claude-seo run pagespeed_check.py https://memex.stifler.in/ --strategy both` and `claude-seo run lighthouse_agentic.py https://memex.stifler.in/ --strategy both`.
- [ ] **S-L1 · Low · Two robots.txt sources** (`src/app/robots.ts` + Cloudflare managed).
  - Owner decision: keep Cloudflare's managed robots.txt and delete `src/app/robots.ts`, or turn the managed file off and move the Content-Signal policy into `robots.ts`. Left as is so no crawler policy changes without a decision.
- [ ] **S-L2 · Low · Baiduspider and PetalBot blocked** (Cloudflare managed robots.txt).
  - Owner: unblock them in AI Crawl Control unless the exclusion is intended.
- [x] **S-L3 · Low · No favicon.ico / apple-icon** (`src/app/`).
  - Resolution: `src/app/favicon.ico` (48×48, PNG in ICO) and `src/app/apple-icon.png` (180×180) rendered from `icon.svg`; Next emits the link tags; site-check fetches both.
- [x] **S-L4 · Low · Sitemap lastmod = build time** (`src/app/sitemap.ts`).
  - Resolution: lastmod is `UPDATED` from `shell.tsx` (2026-10-26), bumped by hand when copy changes.
- [x] **S-L5 · Low · og:description shorter than the meta description** (`src/app/layout.tsx`).
  - Resolution: OG and Twitter title/description equal each page's title/description (`pageMeta` in shell.tsx).
- [x] **S-L6 · Low · Install is one `<pre>`** (`src/app/page.tsx`).
  - Resolution: `/setup` gives the install as an `<ol>` of explained steps plus the command block and per-host recipes. The home terminal card (with its Copy button) is unchanged by design.
- [x] **S-L7 · Low · "Read the reports" points at a directory listing** (`src/app/page.tsx`).
  - Resolution: it links to `/evaluation`, which links `28_PHASE6_RESULTS.md` and `27_PHASE6_PROTOCOL.md` directly.
- [ ] **S-L8 · Low · Optional Markdown mirror / llms-full.txt** (`public/`).
  - Deferred: no score weight and no confirmed consumer. `public/llms.txt` was updated instead (definition, release date, the Cursor boundary, the five pages).
- [x] **S-L9 · Low · No content images** (`src/app/how-it-works/page.tsx`, `public/`).
  - Resolution: `/how-it-works` shows a real still from the v1 film (`public/memex-film-live-context.webp`, 1280×720, 25 KB, lazy, width/height set, descriptive alt, captioned as a film still); it is also the SoftwareApplication `screenshot`. No staged product screenshot was invented.
- [ ] **S-L10 · Low · Brand footprint** (off-site).
  - Owner: a Show HN or r/ClaudeAI write-up of the preregistered S05 evaluation (link /evaluation); a Wikidata item (instance of free software; website, repository, licence MIT, developer Hill Patel), whose URL then goes in SoftwareApplication `sameAs`; ask MCP directories to link the homepage.
- [x] **S-L11 · Low · Four fonts preloaded** (`src/app/layout.tsx`).
  - Resolution: DM Mono has `preload: false`; only the display face is preloaded (1 preload instead of 4 in the built HTML).
- [x] **S-V · Verification.**
  - Resolution: `npm run build` clean, `npm run lint` clean, `npm run check` 3/3 (a new test pins the release date and every S05 figure on /evaluation and in the FAQ), `npm run site-check` all pass (it now also checks the four inner pages with and without JS at 390 px, canonicals, headings, `out/_headers`, and the favicon, apple-icon and WebP assets).
- [ ] **S-D · Deploy (owner).** From `D:/memex/memex-site`: `npm run build && npm run check && npm run site-check && npm run deploy`. Then run the live checks in S-H2, the Rich Results Test on `/` and `/how-it-works`, S-H1 and S-M9.
