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
