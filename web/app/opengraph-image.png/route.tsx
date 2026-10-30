import { ImageResponse } from 'next/og'

/*
 * The social card, rendered once at build time.
 *
 * This is a route handler in a folder named opengraph-image.png, not the
 * opengraph-image.tsx file convention, on purpose. Under `output: 'export'`
 * the file convention writes `out/opengraph-image` with no extension, which
 * GitHub Pages types by extension and so would likely serve as
 * application/octet-stream -- and scrapers check Content-Type. A route
 * handler whose path ends in .png is exported as `out/opengraph-image.png`.
 * The convention would also hash the URL and link it itself; here layout.tsx
 * names it explicitly.
 *
 * `output: 'export'` has no server to render it per request, so the route
 * must be declared static.
 *
 * next/og resolves neither CSS variables nor next/font bindings, so every
 * colour is a literal (the same values as tokens.css and public/icon.svg)
 * and the display face is fetched and handed over through the `fonts` option.
 */
export const dynamic = 'force-static'
const size = { width: 1200, height: 630 }

const PLATE = '#0B0D10'
const PAPER = '#F6F7F9'
const BLUE = '#7DD3FC'
const MUTED = '#A0A7AF'

const DESCRIPTION = 'It drafts the whole application. You press submit.'

type Font = {
  name: string
  data: ArrayBuffer
  style: 'normal' | 'italic'
  weight: 400
}

/*
 * Newsreader, upright and italic, subset by Google to exactly the glyphs
 * this card uses so the files stay a few kilobytes against next/og's 500KB
 * budget. Asking Google's CSS endpoint without a browser user agent is what
 * makes it answer with TrueType, which is the only format Satori reads.
 *
 * A build must never break on a font CDN. Any failure returns an empty list,
 * and next/og then draws with the face it bundles (a sans, since it ships no
 * serif), which is a worse card but a card.
 */
async function loadFonts(text: string): Promise<Font[]> {
  try {
    const glyphs = encodeURIComponent([...new Set(text)].join(''))
    const cssUrl =
      'https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400' +
      `&text=${glyphs}`
    const cssResponse = await fetch(cssUrl)
    if (!cssResponse.ok) return []
    const css = await cssResponse.text()

    const fonts: Font[] = []
    for (const block of css.match(/@font-face\s*{[^}]*}/g) ?? []) {
      const url = block.match(/src:\s*url\(([^)]+)\)\s*format\('truetype'\)/)?.[1]
      const style = /font-style:\s*italic/.test(block) ? 'italic' : 'normal'
      if (!url) continue
      const fontResponse = await fetch(url)
      if (!fontResponse.ok) return []
      fonts.push({
        name: 'Newsreader',
        data: await fontResponse.arrayBuffer(),
        style,
        weight: 400,
      })
    }
    return fonts.length > 0 ? fonts : []
  } catch {
    // Deliberate: a font CDN outage degrades the card to next/og's bundled
    // sans rather than failing the build. check-assets cannot see the
    // difference, so a degraded card is a visual regression to look for.
    return []
  }
}

/* The display mark, geometry from public/icon.svg, as Satori-friendly SVG. */
function DisplayMark({ px }: { px: number }) {
  return (
    <svg width={px} height={px} viewBox="0 0 100 100" fill="none">
      <g fill={PAPER}>
        <rect x="22" y="12" width="7" height="76" />
        <rect x="35" y="22" width="43" height="6" />
        <rect x="35" y="40" width="43" height="6" />
        <rect x="35" y="58" width="28" height="6" />
      </g>
      <polygon points="22,80 78,73 78,81 22,88" fill={BLUE} />
    </svg>
  )
}

export async function GET() {
  const fonts = await loadFonts(`karani${DESCRIPTION}`)
  const serif = fonts.length > 0 ? 'Newsreader' : 'sans-serif'

  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          background: PLATE,
          padding: '0 96px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <DisplayMark px={230} />
          <div
            style={{
              display: 'flex',
              marginLeft: 40,
              fontFamily: serif,
              fontSize: 180,
              letterSpacing: '-0.015em',
              lineHeight: 1,
              color: PAPER,
            }}
          >
            karani
          </div>
        </div>
        <div
          style={{ width: 48, height: 3, background: BLUE, marginTop: 56, marginBottom: 32 }}
        />
        <div style={{ fontFamily: serif, fontSize: 32, lineHeight: 1.25, color: MUTED }}>
          {DESCRIPTION}
        </div>
      </div>
    ),
    { ...size, fonts },
  )
}
