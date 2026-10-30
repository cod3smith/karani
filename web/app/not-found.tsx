import type { Metadata } from 'next'
import Link from 'next/link'

/*
 * The layout's canonical is './', resolved against the route being rendered.
 * For the 404 that route is Next's internal /_not-found, which would leak
 * into public HTML. The export serves this page at /404/ (and as 404.html),
 * so that is the address it declares. It stays noindex, so it is never a
 * search result and never in the sitemap; the canonical only has to be true.
 */
export const metadata: Metadata = {
  title: 'Page not found',
  alternates: { canonical: '/404/' },
}

/*
 * The 404 is the one place the mark is allowed to be an empty sheet.
 *
 * It is the same filed sheet as the mark -- spine, ruled lines -- but the
 * rules are gone and the stroke across the foot is cut in two and offset,
 * drawn in the refusal colour instead of the brand blue. karani's job is to
 * fill a sheet in; an address that holds no page is a sheet that was never
 * filed.
 *
 * Geometry is public/icon.svg's. The stroke is the original slanted polygon
 * cut at x=48 and x=54, the right-hand half dropped 6 units. Static export
 * turns this into out/404.html, which GitHub Pages serves for any unknown
 * path. It is exempt from scripts/check-links.mjs's orphan rule, so it needs
 * no navigation entry.
 */
export default function NotFound() {
  return (
    <main
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '1.25rem',
        padding: '6rem 1rem',
        textAlign: 'center',
      }}
    >
      <svg
        width="120"
        height="120"
        viewBox="0 0 100 100"
        fill="none"
        aria-hidden="true"
        style={{ color: 'var(--ka-ink)' }}
      >
        <g fill="currentColor">
          <rect x="22" y="12" width="7" height="76" />
        </g>
        <g fill="var(--ka-refuse)">
          <polygon points="22,80 48,76.75 48,84.75 22,88" />
          <polygon points="54,81.75 78,79 78,87 54,90.75" />
        </g>
      </svg>
      <h1
        style={{
          margin: 0,
          fontFamily: 'var(--ka-font-display)',
          fontWeight: 400,
          fontSize: '2rem',
          letterSpacing: '-0.02em',
          color: 'var(--ka-ink)',
        }}
      >
        Nothing is filed at this address.
      </h1>
      <p style={{ margin: 0, color: 'var(--ka-ink-muted)' }}>
        The page you asked for does not exist, or it has moved.
      </p>
      <Link href="/">Back to the start</Link>
    </main>
  )
}
