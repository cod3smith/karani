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
 * The 404 is the one place the mark is allowed to go wrong.
 *
 * It is the same list as the mark -- spine, two bars -- but the last bar,
 * the one karani normally stops at for a signature, is drawn in the
 * refusal colour instead of the brand blue. The mark says "filled in down
 * to the line that is yours"; this says the line is not there.
 *
 * Geometry is public/icon.svg's display variant, unchanged. Static export
 * turns this into out/404.html, which GitHub Pages serves for any unknown
 * path. It is exempt from scripts/check-links.mjs's orphan rule, so it
 * needs no navigation entry.
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
        viewBox="0 0 64 64"
        fill="none"
        aria-hidden="true"
        style={{ color: 'var(--ka-ink)' }}
      >
        <g fill="currentColor">
          <rect x="10" y="8" width="9" height="48" rx="4.5" />
          <rect x="27" y="8" width="28" height="9" rx="4.5" />
          <rect x="27" y="27.5" width="20" height="9" rx="4.5" />
        </g>
        <rect x="27" y="47" width="9" height="9" rx="4.5" fill="var(--ka-refuse)" />
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
