import type { MetadataRoute } from 'next';

/* One route, one entry. lastModified is the build time, which is the only
   modification date available to a static export.

   Required by `output: 'export'`, same as robots.ts: without it the build
   fails collecting page data, regardless of whether the route reads Request. */
export const dynamic = 'force-static';

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: 'https://memex.stifler.in',
      lastModified: new Date(),
    },
  ];
}
