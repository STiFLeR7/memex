import type { MetadataRoute } from 'next';
import { PAGES, SITE, UPDATED } from './shell';

/* lastModified is UPDATED in shell.tsx (bumped when the copy changes), not the
   build time, so a redeploy does not claim a modification.

   Required by `output: 'export'`, same as robots.ts: without it the build
   fails collecting page data, regardless of whether the route reads Request. */
export const dynamic = 'force-static';

export default function sitemap(): MetadataRoute.Sitemap {
  return ['', ...PAGES.map(p => p.href)].map(path => ({ url: `${SITE}${path}`, lastModified: UPDATED }));
}
