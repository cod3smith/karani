import type { MetadataRoute } from 'next'
import type { PageMapItem } from 'nextra'
import { getPageMap } from 'nextra/page-map'
import { ORIGIN } from '../site.config.mjs'

/*
 * The sitemap is read off the same page map the sidebar is built from, so a
 * page that is in the navigation is in the sitemap and the two cannot drift.
 * That includes the pages generated at build time (reference/cli,
 * reference/mcp, reference/config and every decision page), which exist in
 * the page map and not in the repository.
 *
 * Only MDX pages are listed. A folder has a `route` too, but it is a page
 * only if it has an index.mdx, and that arrives as its own child.
 *
 * `trailingSlash: true` makes the export write `guides/qualification/index.html`,
 * and GitHub Pages answers `/guides/qualification` with a redirect to the
 * slashed form, so the slashed form is the canonical one and the one listed.
 *
 * The file convention is dynamic by default; `output: 'export'` has no server
 * to answer it, so it is declared static.
 */
export const dynamic = 'force-static'

function routesOf(items: readonly PageMapItem[]): string[] {
  const routes: string[] = []
  for (const item of items) {
    if ('children' in item) routes.push(...routesOf(item.children))
    else if ('route' in item) routes.push(item.route)
    // `{ data }` entries are _meta.ts records, not pages.
  }
  return routes
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const routes = [...new Set(routesOf(await getPageMap()))].sort()
  return routes.map((route) => ({
    url: `${ORIGIN}${route === '/' ? '' : route}/`,
  }))
}
