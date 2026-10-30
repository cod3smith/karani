/**
 * Fail when karani's mark stops meeting its spec.
 *
 * The mark is a filed sheet: a spine down the left edge, three ruled lines
 * of a record, and one blue stroke across the foot where a signature goes.
 * karani fills the sheet in; the blue stroke is the part it will not draw
 * for you, which is the product in one glyph.
 *
 * It is drawn four times, and that is the reason this check exists:
 * public/icon.svg (display, 32px and up), public/favicon.svg (small, below
 * 32px, thicker slabs), components/Mark.tsx (the navbar, at 24px, so the
 * small geometry) and app/apple-icon.png/route.tsx (the home-screen icon,
 * also small). Four copies of one drawing drift.
 *
 * The defect this exists to catch first is a favicon that vanishes on a
 * light tab strip, so the favicon's own colour-scheme handling is asserted
 * before anything else.
 *
 * Usage:
 *   node scripts/check-mark.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p) => readFileSync(join(root, p), 'utf8')

const failures = []
function assert(cond, message) {
  if (!cond) failures.push(message)
}

// The console's own palette. The mark is the product's, so these are the
// product's values and not the site's idea of them.
const BLUE = '#7DD3FC'
const DARK = '#0B0D10'
const PAPER = '#F6F7F9'

// The two drawings, transcribed from the brief. Anything that renders the
// mark must use one of them exactly.
const DISPLAY = [
  '<rect x="22" y="12" width="7" height="76"',
  '<rect x="35" y="22" width="43" height="6"',
  '<rect x="35" y="40" width="43" height="6"',
  '<rect x="35" y="58" width="28" height="6"',
  'points="22,80 78,73 78,81 22,88"',
]
const SMALL = [
  '<rect x="18" y="10" width="10" height="80"',
  '<rect x="34" y="20" width="48" height="10"',
  '<rect x="34" y="40" width="48" height="10"',
  '<rect x="34" y="60" width="30" height="10"',
  'points="18,78 82,70 82,84 18,92"',
]

let favicon = ''
let icon = ''
try {
  favicon = read('public/favicon.svg')
  icon = read('public/icon.svg')
} catch (err) {
  console.error(`FAIL: ${err.message}`)
  process.exit(1)
}
const mark = read('components/Mark.tsx')
const markCss = read('components/Mark.module.css')
const apple = read('app/apple-icon.png/route.tsx')
const layout = read('app/layout.tsx')
const notFound = read('app/not-found.tsx')

// ---- the favicon must carry BOTH tab-strip schemes -----------------------

assert(/prefers-color-scheme:\s*dark/.test(favicon), 'favicon has no dark-scheme override')
assert(new RegExp(DARK, 'i').test(favicon), 'light tab strip needs dark letterforms')
assert(new RegExp(PAPER, 'i').test(favicon), 'dark tab strip needs pale letterforms')
// The default, outside the media query, must be the dark one: a reader on a
// light tab strip is the case that fails invisibly.
const outsideStyle = favicon.replace(/<style>[\s\S]*?<\/style>/, '')
assert(
  !new RegExp(PAPER, 'i').test(outsideStyle),
  'favicon letterforms must default to dark, with the pale fill only inside the media query',
)

// ---- geometry, exactly ---------------------------------------------------

for (const fragment of DISPLAY) {
  assert(icon.includes(fragment), `public/icon.svg (display variant) is missing ${fragment}`)
}
for (const fragment of SMALL) {
  assert(favicon.includes(fragment), `public/favicon.svg (small variant) is missing ${fragment}`)
}
assert(
  /viewBox="0 0 100 100"/.test(favicon) && /viewBox="0 0 100 100"/.test(icon),
  'both SVGs must use viewBox 0 0 100 100',
)

// The small variant is the thicker one. That is its whole reason to exist:
// the display variant's 6-unit rules land under a pixel at 16px.
assert(/height="6"/.test(icon), 'the display variant\'s ruled lines must be 6 units')
assert(/height="10"/.test(favicon), 'the small variant\'s ruled lines must be 10 units')

// ---- the stroke is the brand, and it is slanted --------------------------

for (const [name, svg] of [['favicon', favicon], ['icon', icon]]) {
  assert(/<polygon/.test(svg), `${name}: the signature stroke must be a <polygon>`)
  const stroke = svg.match(/<polygon[^>]*>/)?.[0] ?? ''
  assert(new RegExp(BLUE, 'i').test(stroke), `${name}: the stroke must be ${BLUE}`)
  // Four points, two of which share neither y with their pair: a flat
  // rectangle would read as a fourth ruled line rather than a stroke.
  const points = stroke.match(/points="([^"]+)"/)?.[1] ?? ''
  const ys = points.split(' ').map((p) => Number(p.split(',')[1]))
  assert(ys.length === 4, `${name}: the stroke must have four points`)
  assert(
    ys.length === 4 && ys[0] !== ys[1] && ys[2] !== ys[3],
    `${name}: the stroke must be slanted; a flat one reads as another ruled line`,
  )
}

// ---- neither file depends on a webfont ----------------------------------

assert(!/font-family|<text/.test(favicon + icon), 'the SVGs must not use text or fonts')

// ---- the React mark draws the SMALL variant -----------------------------
//
// It is rendered at 24px, below the 32px at which the display variant holds
// up, so it must take the small geometry and theme-aware colour.

for (const fragment of SMALL) {
  assert(mark.includes(fragment), `Mark.tsx (24px, small variant) is missing ${fragment}`)
}
assert(
  !DISPLAY.some((f) => f.startsWith('<rect x="22"') && mark.includes(f)),
  'Mark.tsx must not draw the display variant\'s spine',
)
const glyphSize = Number(mark.match(/<svg[^>]*\swidth="(\d+)"/)?.[1])
assert(
  glyphSize > 0 && glyphSize < 32,
  `Mark.tsx draws the glyph at ${glyphSize}px; below 32px it must use the small variant, and from 32px up the display one`,
)
assert(/currentColor/.test(mark), 'Mark.tsx letterforms must use currentColor')
assert(/--ka-brand/.test(mark + markCss), 'the Mark stroke must use var(--ka-brand)')

// ---- the Apple icon is the small variant too ---------------------------

for (const fragment of SMALL) {
  assert(apple.includes(fragment), `apple-icon route (small variant) is missing ${fragment}`)
}
assert(new RegExp(BLUE, 'i').test(apple), `the apple icon's stroke must be ${BLUE}`)
assert(new RegExp(DARK, 'i').test(apple), 'the apple icon must sit on the console\'s own ground')

// ---- the display face is wired to the variable tokens.css reads ---------

assert(/Newsreader/.test(layout), 'layout.tsx must load Newsreader')
assert(/variable:\s*'--font-newsreader'/.test(layout), "the display variable must be '--font-newsreader'")
assert(/newsreader\.variable/.test(layout), "the display face's .variable must be on <html>")

// ---- metadata.icons must declare the SMALL variant only ----------------
//
// icon.svg collapses at 16px, and listing both lets the browser pick it for
// the tab strip. 'favicon.svg' contains 'icon.svg' as a substring, so these
// match on the quoted path.

const iconsBlock = layout.match(/icons:\s*\{[\s\S]*?\n  \},/)?.[0] ?? ''
assert(iconsBlock !== '', 'layout.tsx must declare metadata.icons')
assert(/'\/favicon\.svg'/.test(iconsBlock), 'metadata.icons must reference /favicon.svg')
assert(
  !/'\/icon\.svg'/.test(iconsBlock),
  'metadata.icons must not declare /icon.svg (the display variant)',
)
// Next links a file-convention icon only when metadata.icons is unset, so
// the Apple icon has to be named here, and it must be the generated PNG.
const appleBlock = iconsBlock.match(/apple:\s*\[[\s\S]*?\]/)?.[0] ?? ''
assert(/'\/apple-icon\.png'/.test(appleBlock), "metadata.icons.apple must reference '/apple-icon.png'")
assert(!/icon\.svg/.test(appleBlock), 'metadata.icons.apple must not reference an SVG')

// ---- the 404 is the mark refusing ---------------------------------------
//
// Same sheet, rules gone, stroke cut in two and drawn in the refusal
// colour. If it stops being the mark it stops saying anything.

assert(/<rect x="22" y="12" width="7" height="76"/.test(notFound), 'the 404 must draw the mark\'s spine')
assert(
  (notFound.match(/<polygon/g) ?? []).length === 2,
  'the 404 must draw the stroke cut in two',
)
assert(/var\(--ka-refuse\)/.test(notFound), 'the 404\'s cut stroke must be var(--ka-refuse)')
assert(
  !new RegExp(BLUE, 'i').test(notFound) && !/--ka-brand/.test(notFound),
  'the 404 must not draw the stroke in the brand colour; the point is that it is not signed',
)

if (failures.length) {
  for (const f of failures) console.error(`FAIL: ${f}`)
  process.exit(1)
}
console.log('check-mark: ok')
