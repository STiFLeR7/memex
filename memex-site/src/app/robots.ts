import type { MetadataRoute } from 'next';

/* Statically emitted to /robots.txt by the export. There is nothing private on
   this one-route site, so nothing is disallowed.

   `output: 'export'` requires the force-static opt-in explicitly: not reading
   the request is NOT enough, the build fails to collect page data without it.

   Only the Sitemap line: Cloudflare's managed robots.txt (prepended at the edge) already has the
   User-agent: * group; a second one here made two conflicting * groups. With managed robots.txt
   off, no rules = allow all. */
export const dynamic = 'force-static';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [],
    sitemap: 'https://memex.stifler.in/sitemap.xml',
  };
}
