import { ImageResponse } from 'next/og'

/*
 * The home-screen icon, rendered once at build time.
 *
 * A route handler in a folder named apple-icon.png, not the apple-icon.tsx
 * file convention, so that the export is `out/apple-icon.png` and not an
 * extensionless file. Next also links the convention only when
 * metadata.icons is unset, and layout.tsx sets it, so layout.tsx declares
 * this icon itself.
 *
 * It draws the SMALL variant of the mark (geometry of public/favicon.svg):
 * thicker slabs and a thicker stroke, because the display variant's 6-unit
 * slabs thin out to hairlines at this size. iOS rounds the corners itself,
 * so the plate is full bleed. Colours are literals: next/og reads no CSS.
 *
 * The home screen is where this icon matters most -- the submission console
 * is a file you open on a phone -- so it is the console's own ground and
 * accent, not a lighter variant.
 */
export const dynamic = 'force-static'
const size = { width: 180, height: 180 }

export function GET() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0B0D10',
        }}
      >
        <svg width="128" height="128" viewBox="0 0 100 100" fill="none">
          <g fill="#F6F7F9">
            <rect x="18" y="10" width="10" height="80" />
            <rect x="34" y="20" width="48" height="10" />
            <rect x="34" y="40" width="48" height="10" />
            <rect x="34" y="60" width="30" height="10" />
          </g>
          <polygon points="18,78 82,70 82,84 18,92" fill="#7DD3FC" />
        </svg>
      </div>
    ),
    size,
  )
}
