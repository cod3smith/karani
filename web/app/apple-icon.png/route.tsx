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
 * thicker bars, because the display variant's 9-unit bars thin out at this
 * size. The inset matches the brand kit's own app icon, which pads the
 * 64-unit mark by a quarter of the square. iOS rounds the corners itself,
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
        <svg width="128" height="128" viewBox="0 0 64 64" fill="none">
          <g fill="#F6F7F9">
            <rect x="8" y="6" width="12" height="52" rx="6" />
            <rect x="26" y="6" width="30" height="12" rx="6" />
            <rect x="26" y="26" width="22" height="12" rx="6" />
          </g>
          <rect x="26" y="46" width="12" height="12" rx="6" fill="#7DD3FC" />
        </svg>
      </div>
    ),
    size,
  )
}
