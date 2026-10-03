import type { MetadataRoute } from 'next';

/* Statically emitted to /robots.txt by the export. There is nothing private on
   this one-route site, so nothing is disallowed.

   `output: 'export'` requires the force-static opt-in explicitly: not reading
   the request is NOT enough, the build fails to collect page data without it. */
export const dynamic = 'force-static';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: '*', allow: '/' },
    sitemap: 'https://memex.stifler.in/sitemap.xml',
  };
}
