/**
 * Where this site is served from. The one place it is written down.
 *
 * Everything reads this: the metadata base and canonicals (app/layout.tsx),
 * robots.txt, the sitemap, the llms.txt links, and the four build gates
 * that check them against each other. Changing where the site lives is one
 * line, or one environment variable.
 *
 *     KARANI_DOCS_ORIGIN=https://docs.example.com npm run build
 *
 * **It has to be a zone root — a bare scheme and host, no path.**
 *
 * That is not a preference, it is what this site's links require, and it
 * was measured rather than assumed. A Next `basePath` prefixes assets and
 * `next/link` navigation but leaves a prose link alone: built under
 * `basePath: '/karani'`, the stylesheet resolved to
 * `/karani/_next/static/css/...` while the prose links in the same page
 * still pointed at `/decisions/` and `/guides/`, and both declared icons at
 * `/favicon.svg`. On a subpath, every prose link and both icons 404 while
 * the page itself looks fine.
 *
 * The content is written with root-absolute links on purpose -- they
 * survive being read as markdown out of context, which is most of why
 * llms.txt is worth publishing. So the fix is a zone root (a custom domain,
 * or a user/organisation GitHub Pages site), not a basePath.
 *
 * Which is why the default below is a subdomain of kelynnjeri.com rather
 * than a path under it: `kelynnjeri.com/karani` would need a basePath and
 * would break in exactly the way described above.
 *
 * A trailing slash is stripped, so both forms work.
 */
export const ORIGIN = (process.env.KARANI_DOCS_ORIGIN ?? 'https://karani.kelynnjeri.com').replace(
  /\/+$/,
  '',
)

/** The repository, named once for the same reason. */
export const REPOSITORY = 'https://github.com/cod3smith/karani'
